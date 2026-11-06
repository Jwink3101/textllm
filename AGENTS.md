# Repository Guidelines

## Project Layout

- `textllm/` is the package: `backends.py` owns model adapters and events, `config.py` owns environment defaults and templates, `markdown.py` owns parsing and directives, `conversation.py` owns conversation updates, `files.py` owns filenames and editor operations, and `cli.py` owns command orchestration.
- `textllm/__init__.py` preserves public imports and the console entry point (`textllm=textllm:cli`). `textllm/__main__.py` supports `python -m textllm`.
- Root `textllm.py` is a development launcher that prioritizes the adjacent package over installed versions, including when called from another directory or through a symlink.
- `test_textllm.py` contains the pytest suite, including CLI-style tests and image handling tests.
- `testresources/img/` contains image fixtures used by tests.
- `readme.md` and `changelog.md` are the main root docs; secondary docs live in `docs/`, including `docs/manual_tests.md`, `docs/testing_strategy.md`, `docs/migration_any_llm.md`, `docs/migration_litellm.md`, and `docs/roadmap.md`.
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
- Runtime dependencies are `python-dotenv` and `any-llm-sdk>=1.27.0`; do not add provider-specific dependencies unless there is a concrete need. any-llm 1.27.0 is required for native OpenRouter Responses support.
- The CLI reads and writes Markdown conversation files, parses optional TOML settings, supports image Markdown, `[@attach optional description](target)` file inputs, and `[@include optional description](target)` local UTF-8 text inputs (including inline and Markdown reference-link forms). `@attachment` remains an alias for `@attach`. It uses `iter_model_events()` as the normalized boundary to any-llm's streaming completion, Responses, and Messages APIs. `iter_completion_text()` remains the completion adapter. `--- Note ---` and `--- Reasoning ---` blocks are local-only and must be excluded from both chat and automatic-title model requests.
- The reserved `[textllm]` settings table selects local behavior and must not be passed to any-llm. Responses and Messages URL citations are appended as Markdown sources; optional provider-returned reasoning content may be written as local-only reasoning blocks. OpenRouter-prefixed Messages calls use any-llm's Anthropic transport against OpenRouter's native Messages base URL and `$OPENROUTER_API_KEY`. Automatic-title calls must never receive tools or tool choice.
- Chat responses stream to stdout as chunks arrive. The conversation file assistant block is created on the first response text chunk, then subsequent chunks are written into it as they arrive. Read role markers as plain separators or level 1 through 6 Markdown headings. Precedence for newly written markers is `--header-level`, `[textllm].header_level`, `$TEXTLLM_DEFAULT_HEADER_LEVEL`, then `0`; environment files use the existing loading order. The built-in template applies the effective level to initial markers. Explicit templates opt in with `{header}` and otherwise keep literal markers unchanged. Role markers such as `--- User ---` and `## --- User ---` are reserved syntax at the start of a line; keep read-side support for backslash-escaped literal markers.
- Keep edits focused and conservative; preserve the simple text-file workflow described in `readme.md`.
- Template selectors beginning with `@` resolve through `TEXTLLM_NAMED_TEMPLATE_<name>` before immediate `.md` files in `TEXTLLM_TEMPLATE_DIR`. Resolve and read templates only for new conversations, before creating the conversation file. `--list-templates` loads environment settings and exits without creating a conversation or invoking a model; broken entries produce a nonzero status.
- Prefer ASCII in source and docs unless the surrounding file already uses non-ASCII.
- Always assume filenames could have spaces, symbols, or any other valid character!

## Testing Notes

- Tests mutate local files and directories such as `testdir/`, `.env`, `log`, and `*.md`; the suite includes cleanup helpers but interrupted runs can leave artifacts behind.
- Some tests invoke `textllm.py` in subprocesses and rely on environment variables such as `TEXTLLM_DEFAULT_MODEL`, `TEXTLLM_DEFAULT_TEMPERATURE`, `TEXTLLM_DEFAULT_HEADER_LEVEL`, and `TEXTLLM_TEMPLATE_FILE`.
- Patch functions in the module that looks them up: completion streams in `textllm.backends`, normalized events in `textllm.conversation`, and dotenv loading in `textllm.cli`. In-process test settings live in `textllm.config`; patch the title prompt in `textllm.conversation`.
- Preserve subprocess coverage for the root launcher's local-package precedence from other directories and symlinks, plus the `python -m textllm` entry point. When packaging changes, build and smoke-test an isolated wheel without live model calls.
- Verify fresh imports select the `textllm/` package over `textllm.py`, importing the launcher does not modify `sys.path`, and a detached launcher fails clearly instead of using another installation.
- The default pytest suite must use the deterministic fake model path, including subprocess tests through `TEXTLLM_TEST_MODE=1`. It should not require provider credentials or spend money.
- Avoid running live LLM calls manually unless the user explicitly wants that; tests configure deterministic prompts and test mode behavior.
- Keep named-template tests isolated from personal environment registrations and discovered dotenv files. Cover registration/directory precedence, dotenv expansion and loading order, literal paths, unusual filenames, failures without conversation creation, existing-conversation behavior, and listing success/failure exit statuses using deterministic CLI and subprocess checks.
- Keep `docs/testing_strategy.md` in sync with the deterministic completion, Responses, and Messages streams, `iter_model_events()`, local-only note filtering, citation, reasoning-summary, and token-usage behavior, title tool isolation, stdout and file streaming behavior, image and attachment test expectations, and any opt-in live-provider testing approach.

## Documentation and Formatting

- For all Markdown files:
    - Do not use hard line breaks
    - Ensure there is always a blank line after a section header
    - Prefer `-` to `*` for itemize but also prefer consistency with the rest of the document or block
    - Never use full filesystem paths to cross-reference docs. Always prefer them to be relative links.
- When changing testing behavior or guidance, update both `AGENTS.md` and `docs/testing_strategy.md` so future agents see the same strategy.
- For public Python functions, use NumPy style but prefer Markdown-esque formatting
