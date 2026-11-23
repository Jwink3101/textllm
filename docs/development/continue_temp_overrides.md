# Continuation, Temporary Conversations, and Settings Overrides

## Status

Implemented in 0.11.0. This extends the existing Markdown workflow with explicit continuation, temporary storage, and invocation-specific settings. No new dependencies are needed.

## Command Behavior

| Command | Behavior |
| --- | --- |
| `textllm` | Create a new regular conversation, as today. |
| `textllm file.md` | Open or create that file, as today. |
| `textllm -c` or `textllm --continue` | Resume the last regular conversation. |
| `textllm -c file.md` | Resume that existing file; fail if it is missing. |
| `textllm -t` or `textllm --temp` | Create a new temporary conversation. |
| `textllm --temp -c` | Resume the last temporary conversation. |

Continuation uses two global pointers, regular and temporary, independent of the working directory. Temporary calls do not replace the regular pointer. An explicit file under the managed temporary conversation directory counts as temporary; other explicit files count as regular.

Reject `--temp` with an explicit path, and reject `--continue` with a directory. Existing directory arguments without these flags retain their current behavior. Continuation requires an existing remembered file: missing state or a missing target produces a clear error rather than creating a new conversation. Supplying its new filename repairs the pointer after an external move or rename.

Once selected, the conversation follows normal prompt, stdin, editor, title, and streaming behavior. Continuation does not bypass the requirement for a new user prompt. Print/log the selected temporary path through the normal path-reporting mechanism, including when continuing. Regular conversations retain normal rename reporting. No special `@continue` filename or automatic continuation is introduced.

## Temporary Files and State

Use a private, user-specific subdirectory beneath `tempfile.gettempdir()`. Its layout is approximately:

```text
textllm-<user>/
    state.sqlite3
    conversations/
        20261119T190132.123456Z.md
```

Temporary filenames use compact ISO 8601 in UTC with six fractional digits and a trailing `Z`, for example `20261119T190132.123456Z.md`. Create files exclusively and retry a collision with the later of the current UTC time or the previous candidate plus one microsecond; the clock alone is not a uniqueness guarantee. Do not delete conversations on process exit. The operating system may eventually clean this directory.

Report the selected temporary path before any model call, so the file can be recovered even if title generation or streaming fails.

Temporary conversations still use the selected template and can receive an automatic document title, but always retain their timestamp filenames. Temporary mode ignores `--rename` (including its `--move` alias); this also applies to `--temp --continue` and explicit files recognized as managed temporary conversations. Relative images, includes, and attachments resolve from the actual conversation directory, as they do today.

Use Python's standard-library `sqlite3` for state, with one small key/value table:

```sql
CREATE TABLE IF NOT EXISTS state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
```

Store absolute paths under `last_regular` and `last_temp`, updating each with an upsert. SQLite is simpler here than JSON plus portable locking and atomic file replacement: short transactions handle concurrent updates and interrupted writes without a separate application lock. Use a connection timeout for contention and close connections promptly. No ORM, WAL configuration, history tables, or migration framework is needed for this scope.

Do not hold a transaction while editing or calling a model. State transactions do not lock Markdown conversations against simultaneous edits.

Record a newly created template even when the command exits without calling a model. For an existing conversation, record it after successful completion, including title-only operations. Always record the final path after any textllm rename. Concurrent successful commands use the order of state updates to determine which is last. State updates preserve the other conversation pointer.

If a model operation fails after creating a file, retain that file and its recorded creation path. If an existing conversation operation fails, leave the previous pointer unchanged. State write failures should be reported without undoing conversation content already written. A missing or unreadable state file must never cause `--continue` to guess another file.

Keeping both pointers in temporary storage means system cleanup can also forget the last regular conversation. Its Markdown file survives; selecting it explicitly establishes the pointer again. There is no conversation history database or directory scanning.

### Keeping a Temporary Conversation

The [README's usage documentation](../../readme.md#temporary-conversations) shows how to keep a temporary conversation. The normal logs report its path; copy that file to a permanent location, then use the saved copy normally:

```bash
textllm --temp --prompt "Explain gravity"
# Copy the temporary file using the actual path reported in the logs:
cp "/tmp/textllm-<user>/conversations/20261119T190132.123456Z.md" "Gravity notes.md"
textllm "Gravity notes.md" --prompt "How does relativity change this?"
```

The temporary path above is illustrative; use the logged path for your system. Copying preserves the temporary original for `--temp --continue`. Opening the permanent copy makes it the last regular conversation. Copy any referenced local assets separately if needed; relative links remain relative to the conversation file's location.

## Repeatable TOML Overrides

`-o TOML` / `--override TOML` is accepted multiple times:

```bash
textllm -c -o 'temperature = 0.3'
textllm --temp --prompt "Explain gravity" -o 'model = "openai/gpt-5.5"'
textllm -c -o 'textllm.backend = "responses"' -o 'reasoning.effort = "high"'
textllm -c -o 'textllm.title.model = "openai/gpt-5.5"'
```

Each argument is a complete TOML fragment, parsed independently with the existing standard-library TOML parser. Dotted keys, inline tables, and quoted multiline arguments use normal TOML syntax. Invalid fragments fail before creating or editing a conversation or invoking a model. Separate fragments may repeat keys; duplicate keys within one fragment remain a TOML error.

Merge fragments in command-line order into the effective conversation settings:

- Recursively merge when both values are dictionaries.
- Otherwise replace the earlier value with the later value, including arrays and scalar/table conflicts.
- Preserve unrelated keys and avoid mutating parsed file settings or shared defaults.
- Do not introduce deletion syntax; TOML has no null value.

For example, overriding `reasoning.effort` preserves a sibling `reasoning.summary` setting. Overriding `tools` replaces the entire array rather than appending to it.

Settings precedence, from lowest to highest priority:

1. Existing fallback defaults.
2. Settings from the conversation file, including settings written by its template.
3. `-o` / `--override` fragments, in command-line order. Later fragments win over earlier ones.
4. Dedicated explicit CLI flags, such as `--tokens` and `--header-level`.

Environment defaults keep their existing role; in particular, an omitted temperature remains omitted unless supplied by the template/file or an override.

Overrides apply only to this invocation and do not rewrite or append the document's TOML settings. Repeating the command without `-o` uses the saved settings again. They affect both provider parameters and reserved `[textllm]` behavior, including output formatting and token reporting. The reserved table remains local and is never sent to a provider. Overrides do not select templates, storage locations, or continuation targets.

Build one effective settings view and use it consistently for chat, title generation, and local output behavior. Preserve existing title-specific precedence: `[textllm.title]` overrides the shared model settings for title requests, including when supplied through `-o`. Title requests must still strip tools and tool choice. Apply an overridden header level to initial built-in or placeholder-enabled template markers as well as newly appended markers, without rewriting literal markers in explicit templates.

## Implementation Shape

Keep argument handling and operation ordering in `cli.py`. Isolate temporary paths and continuation state in a small helper module. Add a small recursive merge helper and an invocation settings overlay to `Conversation`, so local output and model calls use the same values. Preserve the existing package entry points and normal file workflow.

Implementation proceeds in three stages, committing after each: continuation and SQLite state, temporary conversations, then TOML overrides. All three share one release entry in the changelog. Tests isolate temporary storage and continuation state, including subprocesses, so the suite never touches personal continuation pointers.

The implementation uses these choices from the discussion:

- Global continuation with separate regular and temporary pointers.
- Simple SQLite state in the same temporary root, accepting that cleanup forgets both pointers.
- Timestamp filenames for temporary conversations, ignoring rename flags and reporting paths in normal logs.
- A README example showing how to copy a logged temporary conversation to permanent storage.
- Runtime-only overrides, with dedicated CLI flags taking precedence.
