# Repository Guidelines

## Project Layout

- `textllm.py` is the package module and CLI entry point (`textllm=textllm:cli`).
- `test_textllm.py` contains the pytest suite, including CLI-style tests and image handling tests.
- `testresources/img/` contains image fixtures used by tests.
- `readme.md` and `changelog.md` are the main root docs; secondary docs live in `docs/`, including `docs/manual_tests.md`, `docs/testing_strategy.md`, `docs/migration_litellm.md`, and `docs/roadmap.md`.
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
- Runtime dependencies are `python-dotenv` and `litellm`; do not add provider-specific dependencies unless there is a concrete need.
- The CLI reads and writes Markdown conversation files, parses optional TOML settings, supports image Markdown, and uses `iter_completion_text()` as the boundary to LiteLLM's streaming completion API.
- Keep edits focused and conservative; preserve the simple text-file workflow described in `readme.md`.
- Prefer ASCII in source and docs unless the surrounding file already uses non-ASCII.

## Testing Notes

- Tests mutate local files and directories such as `testdir/`, `.env`, `log`, and `*.md`; the suite includes cleanup helpers but interrupted runs can leave artifacts behind.
- Some tests invoke `textllm.py` in subprocesses and rely on environment variables such as `TEXTLLM_DEFAULT_MODEL`, `TEXTLLM_DEFAULT_TEMPERATURE`, and `TEXTLLM_TEMPLATE_FILE`.
- The default pytest suite must use the deterministic fake model path, including subprocess tests through `TEXTLLM_TEST_MODE=1`. It should not require provider credentials or spend money.
- Avoid running live LLM calls manually unless the user explicitly wants that; tests configure deterministic prompts and test mode behavior.
- Keep `docs/testing_strategy.md` in sync with the deterministic test stream, `iter_completion_text()`, streaming behavior, image test expectations, and any opt-in live-provider testing approach.

## Documentation and Formatting

- For all Markdown files:
    - Do not use hard line breaks
    - Ensure there is always a blank line after a section header
    - Prefer `-` to `*` for itemize but also prefer consistency with the rest of the document or block
    - Never use full filesystem paths to cross-reference docs. Always prefer them to be relative links.
- When changing testing behavior or guidance, update both `AGENTS.md` and `docs/testing_strategy.md` so future agents see the same strategy.
- For public Python functions, use NumPy style but prefer Markdown-esque formatting
