# Testing Strategy

This project tests `textllm` as a local text-file workflow, not as a live model benchmark. The default test suite must be deterministic, offline, and safe to run without API credentials or provider accounts.

## Goals

- Verify the CLI behavior users rely on: creating conversation files, appending prompts, always streaming output to stdout and the conversation file, reporting provider token usage to stderr or a local-only note, setting titles, renaming files, reading stdin/editor input, loading templates, and applying environment defaults.
- Verify the Markdown conversation format: title parsing, TOML settings, plain and heading-prefixed role blocks, header-level CLI, file, direct-environment, and `--env` precedence, built-in template headings, explicit-template `{header}` opt-in and literal opt-out, escaped role markers, adjacent role merging, local-only note and reasoning filtering, and message conversion.
- Verify image, attachment, and include handling as transport behavior: Markdown image references, `@attach` links, and `@include` links are found, local files are read and converted to data URLs or UTF-8 text, reference links resolve, embedded data URLs are preserved, and media/file blocks are converted for the selected model boundary.
- Verify Responses behavior: OpenAI and OpenRouter routing, event normalization, reasoning-summary handling, hosted web-search status, citation rendering, usage, and title-call tool isolation.
- Verify Messages behavior: Anthropic routing, OpenRouter native-endpoint transport selection, required token limits, system/developer and image conversion, text and thinking events, hosted web-search status, citations, split usage merging, and title-call tool isolation.
- Avoid spending money or requiring credentials in the main test suite.
- Keep tests stable enough to run in subprocesses, in fresh environments, and during normal development.

## Method

The automated suite uses deterministic test streams instead of a live provider. Tests set `TEXTLLM_TEST_MODE=1`, and in-process tests also set `textllm.config.TEST_MODE = True`. In that mode, `iter_completion_text()` yields deterministic completion chunks, while the Responses and Messages backends yield normalized deterministic events rather than making live any-llm calls.

Patch functions where they are looked up: completion streams in `textllm.backends`, normalized events in `textllm.conversation`, and dotenv loading in `textllm.cli`. Template overrides belong to `textllm.config`; the title prompt is patched in `textllm.conversation`. Public imports such as `textllm.Conversation`, `textllm.loads`, and `textllm.cli` remain available at the package root.

The fake streams sit at the same model boundaries as production. Production code still parses files, builds messages, filters local-only `Note` and `Reasoning` blocks from chat and title requests, processes images and attachments, merges adjacent same-role messages, normalizes events, writes responses and citations, and runs rename/title logic. This keeps the tests focused on `textllm` behavior while removing live provider cost, latency, nondeterminism, and account setup.

Backend mocks are useful for targeted tests of OpenAI-style chunk parsing, but they should not replace `TEXTLLM_TEST_MODE=1` for subprocess tests or dynamic assertions. The subprocess suite needs deterministic behavior that survives a fresh Python interpreter.

The fake returns small deterministic responses derived from the actual messages it receives:

- Title generation returns `title set automatically`. The fake title path omits history; dedicated request-construction tests disable that shortcut and verify native user/assistant history, replacement of original system/developer instructions, a final user title request, and exclusion of local-only blocks.
- Template override tests can request the fixed response `hello` through their system prompt.
- Normal chat responses report the number of user messages and the last word of the latest user text.
- Image responses report how many image blocks reached the fake model.
- Responses calls with requested reasoning summaries emit a fixed summary.
- Messages calls with adaptive or enabled thinking emit the same fixed summary through the normalized reasoning event.
- Responses calls with OpenAI or OpenRouter web search and Messages calls with an Anthropic web-search tool emit deterministic search lifecycle events and an `example.com` citation.

These responses are intentionally simple. They are not meant to simulate model reasoning or image understanding. They are assertions that the right information reached the model boundary.

## Streaming

textllm always calls the model through a streaming path. Chat responses are printed to stdout while response chunks are also written to the conversation file. Title generation also uses the streaming path, but suppresses stdout and file streaming so title chunks do not mix with the user-visible answer or conversation body.

The fake streams yield deterministic answer chunks so all three backends exercise chunk accumulation, stdout streaming, and file streaming. Responses and Messages tests additionally exercise non-answer event handling, event-ordered local-only reasoning blocks, and source-list finalization. A targeted interleaved stream verifies `Reasoning`, `Assistant`, `Reasoning`, `Assistant` persistence, answer-only standard output, reasoning filtering, and assistant-segment merging on replay. Other targeted backend mocks verify provider usage fields, nested usage merging, completion requests for streamed usage, and token reports sent to stderr or local-only notes. Output blocks are written lazily on the first matching event; tests cover normal streaming and failures or empty streams before any output is emitted. Console logging tests cover interruptions mid-line, chunks ending in a newline, consecutive status records, and suppressed logs; only an emitted console record interrupting an unfinished line adds a newline, without changing response content. There is no non-streaming CLI mode.

## Subprocess Tests

Failure checks simulate both JSON write and atomic replacement errors for new and existing conversations, asserting a warning, preserved response, and unchanged earlier pointer. Fresh-process checks with a corrupt JSON state file cover regular and temporary continuation, asserting nonzero exit status, an error, no model output, unchanged candidate files, and no fallback conversation creation. Interrupted temporary streams cover new and continued conversations: verify path reporting before model iteration, flushed chunks before the exception, partial answer preservation with a final user marker, and unchanged regular continuation selection.

Every test isolates continuation state by clearing inherited `TEXTLLM_TEMP_DIR`, patching cached `tempfile.tempdir` and setting `TMPDIR`, `TEMP`, and `TMP` to a test directory inherited by subprocesses. Tests must never read or modify personal continuation pointers. Verify global continuation across working directories, explicit existing-file requirements, missing state/targets, template-only creation, rename tracking, failed-operation behavior, sequential JSON pointer updates and atomic write failure cleanup, and continuation in a fresh interpreter.

Use a copied environment for each test and remove inherited `TEXTLLM_ENV_PATH` and `TEXTLLM_DEFAULT_HEADER_LEVEL`. Tests of environment settings supply their own controlled values rather than loading personal configuration.

Verify `TEXTLLM_TEMP_DIR` overrides the cached system temporary directory, handles empty values, relative paths, home expansion, and spaces/symbols, and loads from environment files in fresh processes with separate continuation state.

Temporary conversation checks cover compact ISO 8601 UTC filenames with six fractional digits and trailing `Z`, and filename collisions advancing by one microsecond even with a fixed clock, ignored rename flags, separate regular/temp pointers, explicit managed temporary files, invalid argument combinations, missing targets, and template failures without file creation. Assert that paths are logged and new files remembered before a failing model operation. Copy a temporary file to permanent storage and verify normal continuation selection. Exercise temporary continuation in a fresh subprocess with the same isolated temporary directory.

Some tests run `textllm.py` in a subprocess to verify fresh-interpreter behavior, especially default environment handling. Those subprocesses inherit `TEXTLLM_TEST_MODE=1`, which keeps them deterministic even though they do not inherit Python globals patched in the parent process.

The root launcher must import the adjacent development package even when another installation appears on `PYTHONPATH`, the working directory differs, or the launcher is reached through a symlink. Test those cases with deterministic conversation updates and filenames containing spaces and symbols. Also cover `python -m textllm` and the public package imports. When packaging changes, build a wheel and smoke-test its imports and entry points in isolation from the checkout, without provider calls.

Fresh-interpreter imports must select `textllm/__init__.py` over the root launcher and allow submodule imports. Loading the launcher as a module must leave `sys.path` unchanged. If the launcher is copied away from its adjacent package, it must fail with a clear message rather than use an installed version or recursively import itself.

Do not replace these subprocess tests with only in-process calls unless the subprocess behavior is no longer relevant. They cover a different failure mode.

## Image Tests

Image tests should assert image plumbing, not fake visual recognition. Good assertions include log lines showing local images were found, output showing the expected image count, and follow-up prompts proving prior image messages remain in context.

Avoid assertions such as "the model says the image is blue" in the default suite. That belongs to live provider validation, not deterministic unit or CLI testing.

## Named Templates

Template rendering checks cover ordinary placeholder substitution, double-brace escapes that preserve literal placeholder names in TOML comments, and unchanged unknown placeholders.

Use temporary directories and isolated environment settings so personal registrations and discovered dotenv files cannot affect named-template checks. Verify explicit registrations take precedence over immediate `.md` directory entries, including broken overrides that must not silently fall back. Cover case-sensitive names, invalid selectors, spaces and symbols in paths, symlinks, nonrecursive discovery, literal `./@name` paths, named defaults, and existing placeholder/header behavior.

Exercise the existing environment-loading order and python-dotenv `${VAR}` expansion with controlled environment files. Confirm later directory changes do not retroactively expand earlier registrations. Template failures must not create a conversation file; existing conversations must not resolve unused template selectors. Listing checks cover sorted, quoted names with one `--list-templates`, paths/sources/shadowed entries with two or more occurrences, and CLI help describing repetition. Both levels retain validation, empty-collection guidance, broken-entry and invalid-directory errors, and exit statuses. Include fresh-interpreter subprocess checks with `TEXTLLM_TEST_MODE=1`; listing must not invoke models, read stdin, launch an editor, or create conversations.

## Attachment Tests

Attachment tests should assert that inline and reference-style `@attach` links become file blocks, their optional descriptions remain text, their reference definitions are not model text, and local paths resolve relative to the conversation file. Include tests should assert that `@include` links read local UTF-8 text after a labelled delimiter and reject binary or remote sources. Test completion, Responses, and Messages conversion independently. Include a follow-up request to confirm that an attachment or include in an earlier user message remains in replayed context. Avoid live-provider assertions about document contents or model-specific file support.

## Optional Temperature

Verify omitted temperature stays absent from chat and automatic-title requests across completion, Responses, and Messages backends even when `TEXTLLM_DEFAULT_TEMPERATURE` is set; explicit temperatures must still pass through.

## Invocation Settings Overrides

Verify recursive dictionary merging preserves unrelated keys and leaves its inputs independent, while arrays and scalar/table conflicts replace earlier values. Exercise repeated TOML fragments in command-line order and malformed or internally duplicated keys failing before creating or editing files or touching continuation state.

Capture chat and title requests at `textllm.conversation.iter_model_events` for completion, Responses, and Messages. Assert model/temperature overrides, title-specific precedence and tool isolation, array replacement, and exclusion of reserved local settings from provider requests. Verify overridden reasoning blocks, token notes, and role-marker levels use the same effective settings, and a subsequent invocation returns to the saved TOML. Cover dedicated CLI flags winning over `-o`, environment/file/override header precedence, initial built-in and placeholder-enabled template markers, literal template markers, and combined temporary continuation/overrides in fresh subprocesses.

## Live Provider Checks

Live model calls are not part of the default automated suite. If provider integration needs to be checked, make it an explicit manual or opt-in integration step that requires the developer to choose credentials and accept cost. Do not make live calls conditional on credentials merely being present, because that can surprise developers who have API keys in their shell.

## Maintenance Rules

When changing `Conversation.call_llm()`, `iter_model_events()`, any backend adapter, message construction, streaming, title generation, image handling, or CLI subprocess behavior, update this document if the test strategy changes. When changing this document, also check `../AGENTS.md` so future agents receive the same guidance.

## Configuration Environment Expansion

Environment-reference tests cover one pass throughout parsed TOML strings, nested tables/arrays, local/provider/title settings, all three backends, per-call and CLI overrides, environment-file order, escaping, unchanged types/keys and saved TOML, and no recursive expansion or resolved-secret logging. Template-only creation must work without runtime variables; missing values on execution must fail before file/prompt mutation. Cover template creation and missing-reference failures without any MCP configuration, including title references when titles are disabled, overridden missing references, string values for numeric settings, and literal references in provider tool schemas.

## Settings Namespace Migration

Write new provider configurations under `[llm]`, with local settings under `[textllm]` and title overrides under `[textllm.title]`. Keep legacy fixtures to verify backward compatibility. Test legacy/new/mixed settings, recursive merging, nested provider tables and tool arrays, invalid namespace tables, and one deprecation warning per invocation across chat and title calls. Normalize each CLI fragment separately and verify command-line order across both syntaxes, saved-file immutability, and dedicated flag precedence. Capture provider requests across completion, Responses, and Messages to verify namespace removal, local-table isolation, title overrides, and tool isolation. Include deterministic subprocess and built-in/example template checks. Never migrate personal templates in tests.

When legacy support is eventually removed, follow the [maintainer removal checklist](migration_llm_settings.md#maintainer-checklist-for-removing-legacy-support). Replace compatibility tests with actionable rejection checks for files, templates, direct overrides, and CLI fragments; preserve empty/local-only settings and all new-format request and ordering coverage.


## MCP and Sidecars

`[textllm].tools_display` defaults to `"comment"`; `"block"` preserves visible Tools output. Test parsing visible, commented, and mixed blocks independently of the setting; all heading levels; grouped references; and exact native replay across backends in both modes. Wrapper delimiters must not leak into model messages, and ordinary HTML comments retain their existing behavior. Invalid setting values and malformed Tools comments fail before file mutation. Verify comments close before assistant/reasoning blocks, token notes, and trailing User markers on success, model failure, and interruption. Existing blocks, examples, and templates are not rewritten for this option.

At DEBUG (`-v`), verify MCP connection/session initialization and each `tools/list` page log starts/completions with server identity and elapsed time, plus per-page tool counts and discovery totals. Cover paginated and empty discovery, setup failures/interruption, deterministic timing, and suppression at INFO. URLs, command arguments, pagination cursors, credentials, and raw exception details must not appear in these logs.

Generate new sidecar IDs as UUIDv7 using only the standard library, preserving IDs on updates. Test timestamp extraction/order, version/variant bits, random-bit preservation and distinct IDs within a fixed millisecond with deterministic clock/randomness.

Use only deterministic local MCP fixtures and mocked HTTP transports; never discover personal servers or contact live model providers. `testresources/mcp/server.py` runs a stdio MCPServer fixture. HTTP tests use HTTPX2 MockTransport to check initialize/list/call and bearer headers without network access. Model tests patch the same any-llm/event boundaries as existing backend tests and assert native call/result shapes, streamed argument accumulation, signed/encrypted reasoning preservation, and sidecar replay for completion, Responses, and Messages.

Across fresh CLI processes, mark saved sidecar tool definitions with a unique description and verify it never reaches model requests. Current MCP definitions must appear once per request, without historical copies. Remove MCP configuration on a later invocation and verify native call/result history still replays while no saved definitions are sent; the sidecar record must remain unchanged. Cover all three backends with deterministic request capture.

Cover one-pass environment expansion, literal escapes, argument arrays/strings and unusual paths, literal/interpolated bearer tokens with one-pass expansion, validation, settings-log redaction, and no sidecar credential persistence, invalid configuration before file creation/prompt edits, schema validation, output selection/error/size behavior, execution limits, title isolation, and fresh-process deterministic MCP configuration. Sidecar tests must force an existing WAL database to `DELETE` journal mode, verify UUID records and missing/incomplete/corrupt/duplicate/out-of-order references, tool-associated reasoning retention in every display mode, orphan exclusion, and independent branching. Interruptions and storage failures must preserve partial text/history without automatically executing or retrying recorded calls. Verify paired rename/copy behavior, collision protection, and temporary conversations with isolated continuation state. Keep all fixtures and sidecars inside test-owned directories.

Exercise call lifecycle logging at the orchestration boundary with a fake executor and deterministic clock: starts precede execution, completions identify each call and elapsed time, tool/output errors warn, and interruptions report unknown outcomes without retry. Capture console and debug logs to ensure arguments, result bodies, credentials, and raw transport error details do not leak. Cover consecutive calls, quiet-mode suppression, streamed chunks with/without trailing newlines, and unchanged Markdown response text. Template examples can be rendered and validated using controlled environment variables without connecting to their remote servers or launching package-manager commands.

Responses replay tests must include real SDK response/function-call objects, wire-alias serialization with absent optional fields omitted, and old sidecars containing `async_`. Ensure the fix leaves JSON argument data and original records unchanged.

Reference tests use only `[@sidecar optional description](uuid)` and reject the former syntax. Test grouped reasoning/exchange directives under one `Tools` block, all reasoning display modes across all backends, separate native reasoning storage and exact reassembly order without duplication, signatures/encrypted content, explicit reasoning removal without fallback, mismatched/dangling records, and display-only `Reasoning` blocks. Formatting tests assert a single blank line at block boundaries, consecutive directive lines, and correct streaming for plain and heading markers. Newly written role markers omit trailing hard-break spaces; old files with that whitespace remain readable.


## Answer Recovery and Chat Finalization

Use deterministic model fixtures to return answers only in native completed Responses/Messages payloads and verify the answer reaches stdout and Markdown exactly once. Cover ordinary streamed deltas too, so fallback recovery never duplicates output. Tool-loop fixtures distinguish reasoning returned with a tool call (explicit reasoning directive, including opaque-only data) from reasoning returned only with the final answer (standalone reasoning directive with native assistant metadata). An intermediate preamble must not make an empty/reasoning-only final response appear successful.

Chat attempts that open the file finish with an empty User block on success, provider failure before output, empty streams, partial streams, and interruptions/uncertain tools. Preserve partial responses, error propagation, and isolated continuation pointers. Preflight validation failures remain non-mutating, and incomplete tool records must still fail replay even when the file has a trailing User block.


## Native Reasoning Across Turns

Create sidecars only when a model requests an MCP tool call; configuring MCP alone or returning reasoning must not create a database. Cover both summary-only and opaque native reasoning with MCP disabled or configured but unused, across all backends/display modes and temporary conversations. Buffer initial reasoning until the first tool request, then preserve returned native reasoning during the exchange and with its completed final answer. This storage policy may change if general native-state replay is introduced. Tests cover completion, Responses, and Messages in omit/stderr/block display modes, then construct a later request and assert exact native payload/order, one copy of encrypted/signature data, and preserved Responses assistant phase. Edited/added Markdown Reasoning text must never reach the wire as reasoning or ordinary prose. Removing the standalone reference block excludes its native state. Summary-only fake streams without formal native items remain local-only; do not invent provider state from display text.

Verify that `reasoning_summary = "stderr"` writes returned summaries directly to stderr even when informational logging is disabled; summaries must stay out of stdout and Markdown in that mode.

Verify completed-payload answer/reasoning ties across Responses and Messages: references precede assistant text and group with adjacent Tools references, while earlier streamed text keeps its position. Cover native replay with references before or after the answer, exact visible content, no duplicate assistant messages, and removal from grouped or separate Tools blocks.
