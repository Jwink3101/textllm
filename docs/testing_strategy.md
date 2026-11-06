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

Exercise the existing environment-loading order and python-dotenv `${VAR}` expansion with controlled environment files. Confirm later directory changes do not retroactively expand earlier registrations. Template failures must not create a conversation file; existing conversations must not resolve unused template selectors. Listing checks cover paths, sources, shadowed entries, empty collections, broken entries, invalid directories, and exit statuses. Include fresh-interpreter subprocess checks with `TEXTLLM_TEST_MODE=1`; listing must not invoke models, read stdin, launch an editor, or create conversations.

## Attachment Tests

Attachment tests should assert that inline and reference-style `@attach` links become file blocks, their optional descriptions remain text, their reference definitions are not model text, and local paths resolve relative to the conversation file. Include tests should assert that `@include` links read local UTF-8 text after a labelled delimiter and reject binary or remote sources. Test completion, Responses, and Messages conversion independently. Include a follow-up request to confirm that an attachment or include in an earlier user message remains in replayed context. Avoid live-provider assertions about document contents or model-specific file support.

## Optional Temperature

Verify omitted temperature stays absent from chat and automatic-title requests across completion, Responses, and Messages backends even when `TEXTLLM_DEFAULT_TEMPERATURE` is set; explicit temperatures must still pass through.

## Live Provider Checks

Live model calls are not part of the default automated suite. If provider integration needs to be checked, make it an explicit manual or opt-in integration step that requires the developer to choose credentials and accept cost. Do not make live calls conditional on credentials merely being present, because that can surprise developers who have API keys in their shell.

## Maintenance Rules

When changing `Conversation.call_llm()`, `iter_model_events()`, any backend adapter, message construction, streaming, title generation, image handling, or CLI subprocess behavior, update this document if the test strategy changes. When changing this document, also check `../AGENTS.md` so future agents receive the same guidance.
