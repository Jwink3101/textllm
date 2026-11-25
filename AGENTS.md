# Repository Guidelines

## Project Layout

- `textllm/` is the package: `backends.py` owns model adapters and events, `config.py` owns environment defaults, templates, and recursive settings merging, `markdown.py` owns parsing and directives, `conversation.py` owns conversation updates, `files.py` owns filenames and editor operations, `state.py` owns temporary locations and JSON continuation pointers, and `cli.py` owns command orchestration.
- MCP execution lives in `textllm/tools.py`, bounded orchestration in `textllm/tool_loop.py`, and sidecar persistence/replay in `textllm/history.py`.
- `textllm/__init__.py` preserves public imports and the console entry point (`textllm=textllm:cli`). `textllm/__main__.py` supports `python -m textllm`.
- Root `textllm.py` is a development launcher that prioritizes the adjacent package over installed versions, including when called from another directory or through a symlink.
- `test_textllm.py` contains the pytest suite, including CLI-style tests and image handling tests.
- `testresources/img/` contains image fixtures used by tests.
- `readme.md` and `changelog.md` are the main root docs; secondary docs live in `docs/`, including `docs/manual_tests.md`, `docs/testing_strategy.md`, `docs/migration_llm_settings.md`, `docs/migration_backends.md`, and `docs/roadmap.md`.
- Unimplemented design proposals live in `docs/proposals/`; keep them separate from current feature documentation. Current architecture and file-format design docs live in `docs/development/`.
- Generated or local scratch artifacts may already exist (`htmlcov/`, `.coverage`, `testdir/`, `tmp/`, `.ipynb_checkpoints/`, logs). Do not treat them as source unless the user asks.

## Commands

- Run the main test suite with coverage:

  ```bash
  pytest test_textllm.py --cov --cov-report term --cov-report html
  ```

- The equivalent local helper is:

  ```bash
  ./run_test.sh
  ```

## Coding Notes

- This is a Python package using `setuptools` configured in `pyproject.toml`; `textllm/_version.py` is the single version source for runtime and build metadata.
- Runtime dependencies include `python-dotenv`, `any-llm-sdk>=1.27.0`, `mcp>=2.3.0,<3`, `jsonlitedb>=0.6.0`, and `jsonschema>=4.20`; do not add provider-specific dependencies unless there is a concrete need. any-llm 1.27.0 is required for native OpenRouter Responses support.
- The CLI reads and writes Markdown conversation files, parses optional TOML settings, supports image Markdown, `[@attach optional description](target)` file inputs, and `[@include optional description](target)` local UTF-8 text inputs (including inline and Markdown reference-link forms). `@attachment` remains an alias for `@attach`. It uses `iter_model_events()` as the normalized boundary to any-llm's streaming completion, Responses, and Messages APIs. `iter_completion_text()` remains the completion adapter. `--- Note ---` and `--- Reasoning ---` blocks are local-only and must be excluded from both chat and automatic-title model requests.
- The reserved `[textllm]` settings table selects local behavior and must not be passed to any-llm. Responses and Messages URL citations are appended as Markdown sources; optional provider-returned reasoning content may be written as local-only reasoning blocks. OpenRouter-prefixed Messages calls use any-llm's Anthropic transport against OpenRouter's native Messages base URL and `$OPENROUTER_API_KEY`. Automatic-title calls must never receive tools or tool choice. They use native user/assistant history, replace original system/developer instructions with the title system prompt, and append a user request for the title; cover this message structure in deterministic tests.
- Chat responses stream to stdout as chunks arrive. The conversation file assistant block is created on the first response text chunk, then subsequent chunks are written into it as they arrive. Read role markers as plain separators or level 1 through 6 Markdown headings. Precedence for newly written markers is `--header-level`, `[textllm].header_level`, `$TEXTLLM_DEFAULT_HEADER_LEVEL`, then `0`; environment files use the existing loading order. The built-in template applies the effective level to initial markers. Explicit templates opt in with `{header}` and otherwise keep literal markers unchanged. Role markers such as `--- User ---` and `## --- User ---` are reserved syntax at the start of a line; keep read-side support for backslash-escaped literal markers.
- Keep edits focused and conservative; preserve the simple text-file workflow described in `readme.md`.
- Template selectors beginning with `@` resolve through `TEXTLLM_NAMED_TEMPLATE_<name>` before immediate `.md` files in `TEXTLLM_TEMPLATE_DIR`. Resolve and read templates only for new conversations, before creating the conversation file. `--list-templates` loads environment settings and exits without creating a conversation or invoking a model; broken entries produce a nonzero status.
- Prefer ASCII in source and docs unless the surrounding file already uses non-ASCII.
- Always assume filenames could have spaces, symbols, or any other valid character!

## Testing Notes

- Verify state write failures warn without losing completed responses or replacing earlier pointers. Corrupt JSON continuation state must fail without guessing or creating conversations. Interrupted temporary streams must preserve their logged path, flushed partial response, and separate continuation pointers.
- Isolate continuation state for every test, including subprocesses: clear inherited `TEXTLLM_TEMP_DIR`, patch the cached `tempfile.tempdir` and set `TMPDIR`, `TEMP`, and `TMP` to a test directory. Never read or update personal continuation pointers. Cover global continuation, final rename tracking, missing targets, and sequential JSON pointer updates and atomic write failure cleanup using deterministic calls.
- Give tests a copied environment and remove inherited `TEXTLLM_ENV_PATH` and `TEXTLLM_DEFAULT_HEADER_LEVEL`; tests of these settings must supply controlled values rather than load personal settings.
- Cover `TEXTLLM_TEMP_DIR` overrides, empty values, relative paths, home expansion, unusual paths, and environment-file loading with separate continuation state.
- Cover compact ISO 8601 UTC temporary filenames with six fractional digits and trailing `Z`, filename collisions with a fixed clock, ignored rename flags, separate regular/temp continuation, path logging before model failures, template failures without conversation creation, and keeping a temporary conversation by copying it. Keep temporary subprocess checks deterministic and isolated.
- Cover repeatable TOML overrides across completion, Responses, and Messages: recursive table merges, array/scalar replacement, argument ordering, dedicated CLI flag precedence, title tool isolation, local output settings, unchanged saved TOML, and malformed fragments failing before file mutation. Include initial template header behavior and temporary continuation in subprocesses.
- Cover template placeholder substitution and double-brace escaping, including literal placeholders in TOML comments and preservation of unknown placeholders.
- Tests mutate local files and directories such as `testdir/`, `.env`, `log`, and `*.md`; the suite includes cleanup helpers but interrupted runs can leave artifacts behind.
- Some tests invoke `textllm.py` in subprocesses and rely on environment variables such as `TEXTLLM_DEFAULT_MODEL`, `TEXTLLM_DEFAULT_TEMPERATURE`, `TEXTLLM_DEFAULT_HEADER_LEVEL`, and `TEXTLLM_TEMPLATE_FILE`.
- Patch functions in the module that looks them up: completion streams in `textllm.backends`, normalized events in `textllm.conversation`, and dotenv loading in `textllm.cli`. In-process test settings live in `textllm.config`; patch the title prompt in `textllm.conversation`.
- Preserve subprocess coverage for the root launcher's local-package precedence from other directories and symlinks, plus the `python -m textllm` entry point. When packaging changes, build and smoke-test an isolated wheel without live model calls.
- Verify fresh imports select the `textllm/` package over `textllm.py`, importing the launcher does not modify `sys.path`, and a detached launcher fails clearly instead of using another installation.
- The default pytest suite must use the deterministic fake model path, including subprocess tests through `TEXTLLM_TEST_MODE=1`. It should not require provider credentials or spend money.
- Avoid running live LLM calls manually unless the user explicitly wants that; tests configure deterministic prompts and test mode behavior.
- Keep named-template tests isolated from personal environment registrations and discovered dotenv files. Cover registration/directory precedence, dotenv expansion and loading order, literal paths, unusual filenames, failures without conversation creation, existing-conversation behavior, and listing success/failure exit statuses using deterministic CLI and subprocess checks. One `--list-templates` lists only sorted, quoted names; two or more include paths, sources, and shadowed entries. Verify both levels preserve validation and empty-list behavior, and document repetition in CLI help.
- Keep `docs/testing_strategy.md` in sync with the deterministic completion, Responses, and Messages streams, `iter_model_events()`, local-only note filtering, citation, reasoning-summary, and token-usage behavior, title tool isolation, stdout and file streaming behavior, image and attachment test expectations, and any opt-in live-provider testing approach.

- Verify console logs interrupting streamed stdout start on their own line without extra blank lines; cover newline-terminated chunks, consecutive records, and suppressed logs without modifying response content.

- Verify omitted temperature stays absent from chat and automatic-title requests across completion, Responses, and Messages backends even when `TEXTLLM_DEFAULT_TEMPERATURE` is set; explicit temperatures must still pass through.

## Documentation and Formatting

- For all Markdown files:
    - Do not use hard line breaks
    - Ensure there is always a blank line after a section header
    - Prefer `-` to `*` for itemize but also prefer consistency with the rest of the document or block
    - Never use full filesystem paths to cross-reference docs. Always prefer them to be relative links.
- When changing testing behavior or guidance, update both `AGENTS.md` and `docs/testing_strategy.md` so future agents see the same strategy.
- For public Python functions, use NumPy style but prefer Markdown-esque formatting

## Configuration Environment Expansion

- Resolve `${NAME}` once throughout parsed TOML string values after environment loading and source merging, preserving keys/types and saved TOML. `$${NAME}` is literal; substituted values are not recursively expanded. Template-only creation must not require runtime variables. Test provider/local/nested/array/title/override expansion across all backends, dotenv order, overridden missing values, execution-time errors before mutation, and secret-free debug logging. `config.py` owns expansion across the full configuration. Cover template creation and missing-reference failures without MCP, unused title references, overridden missing references, numeric-setting strings, and escaped provider schema literals.

## Settings Namespace Migration

- Write provider settings under `[llm]`; keep local `[textllm]` and `[textllm.title]` unchanged. Legacy root-level provider settings remain supported with one warning per invocation. Explicit `[llm]` settings win within a source; normalize each CLI override separately so later fragments win across formats.
- Test legacy/new/mixed settings, nested tables and tool arrays, invalid namespace tables, provider isolation across completion/Responses/Messages, title overrides and tool isolation, warning frequency, unchanged saved TOML, template rendering, and deterministic subprocess override ordering. Never migrate personal templates in tests.

- Before removing legacy settings support, follow the [maintainer removal checklist](docs/migration_llm_settings.md#maintainer-checklist-for-removing-legacy-support), including replacing compatibility tests with rejection tests and preserving valid empty/local-only settings.

## MCP and Sidecars

- `tools.py` owns MCP configuration validation and SDK connections; `tool_loop.py` owns bounded model/tool orchestration; `history.py` owns UUID records and replay. Keep the executor/history boundary generic for future tools.
- Configure inline `[[textllm.mcp]]` entries with exactly one URL or command. Expose discovered tools without a separate registry. Keep MCP settings local; preserve `[llm].tools` for provider-hosted tools. Do not add execution/command-trust approval flows without a new design decision.
- Generate new sidecar IDs as UUIDv7 using only the standard library, preserving IDs on updates. Test timestamp extraction/order, version/variant bits, random-bit preservation and distinct IDs within a fixed millisecond with deterministic clock/randomness.
- The sidecar for `conversation.md` is `conversation.md.db`. Always disable JSONLiteDB WAL and explicitly set SQLite `DELETE` journal mode, including existing databases. Preserve paired renames/copies and reject destination collisions. Never execute recorded history or automatically retry uncertain calls.
- Preserve reasoning display modes while persisting tool-associated reasoning and native continuation data in sidecars; reasoning alone creates no database. Markdown references control replay inclusion/order; displayed reasoning is not the native replay source. Title requests exclude reasoning/tools and never connect MCP servers.
- MCP tests use only the deterministic stdio fixture and mocked HTTP transports, never personal servers or live providers. Cover configuration/interpolation and unusual arguments, literal/interpolated bearer tokens with one-pass expansion, redaction, and no credential persistence in sidecars, native streaming/replay across all three backends, reasoning modes/signatures, validation/limits/errors, missing/corrupt/incomplete/duplicate/out-of-order references, storage failures and uncertain outcomes, paired lifecycle/collisions, and isolated deterministic CLI/subprocess behavior. Keep this guidance aligned with `docs/testing_strategy.md`.
- Verify across fresh CLI processes that saved sidecar tool definitions never enter model requests. Current MCP definitions are sent once per request; removing MCP configuration must preserve native call/result replay without sending saved definitions.
- Log MCP execution starts/completions with server/tool identity, call ID, and elapsed time; distinguish tool/output errors from uncertain transport outcomes. Never log call arguments, result bodies, credentials, or raw transport exceptions. Test the real orchestration logging boundary with deterministic timing, multiple calls, errors/interruption, quiet mode, and streamed chunks with/without a terminating newline; call logs must not modify persisted assistant text.
- At DEBUG (`-v`), log MCP connection/session initialization and each `tools/list` page with server identity, elapsed time, per-page counts, and discovery totals. Test paginated and empty discovery, setup failures/interruption, deterministic timing, and suppression at INFO; exclude URLs, command arguments, pagination cursors, credentials, and raw exception details.
- Native SDK response objects must serialize with wire aliases and omitted null fields; test real SDK objects and repair legacy `async_` fields on Responses replay without changing stored records or JSON argument data.
- Use only `[@sidecar optional description](uuid)` references under `Tools`, grouping a reasoning reference before its tool exchange. Store native reasoning separately and reassemble it in original positions once; no implicit fallback when its directive is removed. `Reasoning` blocks contain display text only, generated only in block mode. Test all display modes/backends, signatures/encrypted content and ordering, explicit removal/mismatches/dangling references, rejection of the former reference syntax, and compact grouped-link spacing. New role markers omit hard-break spaces; older marker whitespace remains readable.

- `[textllm].tools_display` defaults to `"comment"`; `"block"` writes visible Tools blocks. Always read both forms, including mixed files, independent of this setting. Test heading levels, grouped references and native replay in both modes, wrapper exclusion from model-facing text, unchanged ordinary comments, invalid settings and malformed wrappers before mutation, and comment closure before visible blocks/notes and trailing User markers on success, failure, and interruption. Do not rewrite existing blocks or change example/template formats solely for this display option.

- After a chat attempt opens the conversation for writing, finish with an empty User block on success, model failure, empty output, and interruption; preserve partial text/history and error propagation. Preflight failures still do not mutate files. Test final-payload-only answers without duplicate text across Responses/Messages, reasoning returned only after tools, opaque tool reasoning without readable summaries, and missing final answers after intermediate preambles. Incomplete exchanges must remain non-replayable despite the trailing User marker.

- Sidecars are only for MCP tool-call history and future client tool integrations. Reasoning alone must not create one, even with MCP configured. Buffer initial reasoning until a tool request establishes the need; preserve native reasoning during the resulting exchange and its final answer. This policy may change if general native-state replay is added. Cover unused MCP configurations, summary-only and opaque reasoning, all backends/display modes, temporary conversations, and no database creation before the first tool request. A standalone reasoning directive under Tools restores a completed native assistant response from its sidecar metadata/order; paired reasoning directives still precede tool exchanges. Never derive model-facing reasoning from Markdown Reasoning blocks or display settings. Test later-turn replay across all backends/modes, exact signed/encrypted payloads without duplication, assistant phase preservation, display edits/removal, and native-state exclusion when its reference block is removed.

Verify that `reasoning_summary = "stderr"` writes returned summaries directly to stderr even when informational logging is disabled; summaries must stay out of stdout and Markdown in that mode.

Verify completed-payload answer/reasoning ties across Responses and Messages: references precede assistant text and group with adjacent Tools references, while earlier streamed text keeps its position. Cover native replay with references before or after the answer, exact visible content, no duplicate assistant messages, and removal from grouped or separate Tools blocks.
