# Repository Guidelines

## Project Layout

- `textllm.py` is the package module and CLI entry point (`textllm=textllm:cli`).
- `test_textllm.py` contains the pytest suite, including CLI-style tests and image handling tests.
- `testresources/img/` contains image fixtures used by tests.
- `readme.md` and `changelog.md` are the main root docs; secondary docs live in `docs/`, including `docs/manual_tests.md`, `docs/testing_strategy.md`, `docs/migration_any_llm.md`, `docs/migration_litellm.md`, and `docs/roadmap.md`.
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

- This is a single-file Python package using `setuptools` configured in `pyproject.toml`; keep `textllm.py` and `pyproject.toml` versions in sync.
- Runtime dependencies are `python-dotenv` and `any-llm-sdk>=1.27.0`; do not add provider-specific dependencies unless there is a concrete need. any-llm 1.27.0 is required for native OpenRouter Responses support.
- The CLI reads and writes Markdown conversation files, parses optional TOML settings, supports image Markdown, `[@attach optional description](target)` file inputs, and `[@include optional description](target)` local UTF-8 text inputs (including inline and Markdown reference-link forms). `@attachment` remains an alias for `@attach`. It uses `iter_model_events()` as the normalized boundary to any-llm's streaming completion, Responses, and Messages APIs. `iter_completion_text()` remains the completion adapter. `--- Note ---` blocks are local-only and must be excluded from both chat and automatic-title model requests.
- The reserved `[textllm]` settings table selects local behavior and must not be passed to any-llm. Responses and Messages URL citations are appended as Markdown sources; optional provider-returned reasoning content may be written as local-only notes. OpenRouter-prefixed Messages calls use any-llm's Anthropic transport against OpenRouter's native Messages base URL and `$OPENROUTER_API_KEY`. Automatic-title calls must never receive tools or tool choice.
- Chat responses stream to stdout as chunks arrive. The conversation file assistant block is created on the first response text chunk, then subsequent chunks are written into it as they arrive. Role markers such as `--- User ---` are reserved syntax at the start of a line; keep read-side support for backslash-escaped literal markers.
- Keep edits focused and conservative; preserve the simple text-file workflow described in `readme.md`.
- Prefer ASCII in source and docs unless the surrounding file already uses non-ASCII.
- Always assume filenames could have spaces, symbols, or any other valid character!

## Testing Notes

- Tests mutate local files and directories such as `testdir/`, `.env`, `log`, and `*.md`; the suite includes cleanup helpers but interrupted runs can leave artifacts behind.
- Some tests invoke `textllm.py` in subprocesses and rely on environment variables such as `TEXTLLM_DEFAULT_MODEL`, `TEXTLLM_DEFAULT_TEMPERATURE`, and `TEXTLLM_TEMPLATE_FILE`.
- The default pytest suite must use the deterministic fake model path, including subprocess tests through `TEXTLLM_TEST_MODE=1`. It should not require provider credentials or spend money.
- Avoid running live LLM calls manually unless the user explicitly wants that; tests configure deterministic prompts and test mode behavior.
- Keep `docs/testing_strategy.md` in sync with the deterministic completion, Responses, and Messages streams, `iter_model_events()`, local-only note filtering, citation, reasoning-summary, and token-usage behavior, title tool isolation, stdout and file streaming behavior, image and attachment test expectations, and any opt-in live-provider testing approach.

## Documentation and Formatting

- For all Markdown files:
    - Do not use hard line breaks
    - Ensure there is always a blank line after a section header
    - Prefer `-` to `*` for itemize but also prefer consistency with the rest of the document or block
    - Never use full filesystem paths to cross-reference docs. Always prefer them to be relative links.
- When changing testing behavior or guidance, update both `AGENTS.md` and `docs/testing_strategy.md` so future agents see the same strategy.
- For public Python functions, use NumPy style but prefer Markdown-esque formatting
