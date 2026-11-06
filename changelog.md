# Changelog

## 0.10.2 (2026-11-06)

User Facing:

- Add named templates through `--template @name`, `TEXTLLM_NAMED_TEMPLATE_<name>`, and `TEXTLLM_TEMPLATE_DIR`, plus `--list-templates` for discovery and validation. `TEXTLLM_TEMPLATE_FILE` also accepts `@name`; literal paths beginning with `@` now need a path prefix such as `./`.
- Apply the effective header level to the built-in template's initial role markers and add an opt-in `{header}` placeholder for explicit templates.

Backend:

- Split the implementation into focused package modules while preserving public imports and the `textllm` command. Keep root `textllm.py` as a launcher for the local development checkout and add `python -m textllm` support.

## 0.10.1 (2026-09-26)

- Stream provider-returned reasoning into local-only `Reasoning` blocks in event order, allowing reasoning and assistant blocks to alternate while keeping standard output answer-only.
- Accept role separators as level 1 through 6 Markdown headings and add `[textllm].header_level`, `$TEXTLLM_DEFAULT_HEADER_LEVEL`, and the overriding `--header-level` CLI option for newly written markers.

## 0.10.0 (2026-09-13)

- Added local-only `--- Reasoning ---` blocks and use them for selected provider-returned reasoning summaries. The `reasoning_summary = "block"` selector replaces `reasoning_summary = "note"`.

## 0.9.3 (2026-09-04)

- Fix AGAIN for missing attachments or includes. Now more robust to spaces in the names.

## 0.9.2 (2026-09-04)

- Fix for missing attachments or includes

## 0.9.1 (2026-09-04)

- Added Markdown-native file directives: `@attach` sends a file as a provider attachment and `@include` places a local UTF-8 text file in the user prompt. Both support optional descriptions and Markdown reference links; `@attachment` remains an alias for `@attach`. Use in link text: `[@include note](path/to/note.md)`
- Added attachment debug logging for directive detection, reference resolution, MIME detection, loading, and Responses/Messages conversion. Payload contents are not logged.

## 0.9.0 (2026-09-03)

- Added an opt-in any-llm Responses backend with normalized streaming events.
- Added an opt-in any-llm Messages backend, including native Anthropic and OpenRouter Messages transports, thinking, hosted web-search citations, system/developer conversion, image inputs, and usage events.
- Added OpenAI and OpenRouter hosted web search, Markdown URL sources, and optional local-only reasoning summaries.
- Added reasoning and provider configuration examples for OpenAI, Anthropic, and OpenRouter.
- Prevented automatic-title calls from invoking configured tools or retaining tool choice and added title-specific provider overrides.
- Required any-llm 1.27.0 or newer for native OpenRouter Responses support.

## 0.8.2 (2026-09-02)

- Added local-only `--- Note ---` blocks, which remain in conversation files but are excluded from chat and automatic-title model requests.

## 0.8.1 (2026-06-06)

- Do not print an Assistant block until tokens start to stream

## 0.8.0 (2026-06-05)

- Replaced LiteLLM with any-llm for streamed model calls.
    - The move to LiteLLM turned out to add a lot of additional time to import so I moved to any_llm. As per the next bullet, should be an easy migration
- Accepted both `provider/model` and `provider:model` model settings
- Stream tokens to the file at the same time rather than afterwards

## 0.7.1 (2026-05-27)

* *Minor*: Remove LiteLLM messages when streaming

## 0.7.0 (2026-05-25)

**Potentially Breaking Change!!!**

- Replaced LangChain with LiteLLM for streamed model calls
- Changed model settings to LiteLLM-style strings such as `openai/gpt-5.5`
- Removed `--stream` and `--no-stream`; chat responses now always stream to stdout while being written to the file
- Added `--- Developer ---` role support and local adjacent same-role message merging
- Kept text-only assistant persistence while preserving image input support
- Moved secondary docs into `docs/` and added a LiteLLM migration guide and roadmap
- Modernized packaging into `pyproject.toml`, removed `setup.py`, and updated runtime dependencies to `litellm` and `python-dotenv`

## 0.6.3 (2026-05-24)

- Added public docstrings and refreshed documentation formatting
- Updated README examples for the current default model and template

## 0.6.2 (2025-06-13)

- Title calls now use the same settings

## 0.6.1 (2025-03-09)

- Better handle the white-space in the input file.

## 0.6.0 (2025-02-14)

- Fixed bug with reading stdin and launching editor
- Added `--stdin` instead of `--prompt -` so that you can do both
- Changed `--silent` to `--quiet`
- Added "Created with {version} at {now}" to the template and those are now fillable

## 0.5.1 (2025-02-13)

- The environment is now loaded dynamically. This means that you can specify an environment file with textllm settings as well (except for the `$TEXTLLM_ENV_PATH` obviously)
- Adds `loads()` to help parse the file into a dictionary

## 0.5.0 (2025-02-05)

Introduces (and tests) the ability to parse Markdown images

## 0.4.1 (2025-02-05)

- Introduced `$TEXTLLM_DEFAULT_MODEL` and `$TEXTLLM_DEFAULT_TEMPERATURE` to control default behavior for *both* new template files and if it's not set
- Introduced `$TEXTLLM_TEMPLATE_FILE` to specify a new template file. Does *not* include `$TEXTLLM_DEFAULT_MODEL` and `$TEXTLLM_DEFAULT_TEMPERATURE`

## 0.4.0 (2025-02-05)

- Removed environment settings `$TEXTLLM_AUTO_RENAME` and `$TEXTLLM_STREAM` because as I use it, I don't see the utility. `--rename` default is based on whether a file is specified. There is little reason to universally set --no-stream.

Shortened the default system prompt and cleanup help message

## 0.3.0 (2025-02-05)

- Made specifying a filename optional. Instead, will create a new file. If an *existing* directory is specified, will create the new file there.
  - `--rename` defaults to True if a new file is created. Still defaults to `$TEXTLLM_AUTO_RENAME` otherwise which itself defaults to False
- Removed `--create` setting. Specifying a new file is all that is needed.
- Removed the double `--edit` where it opened up again. I think it's not worth it. Better to just have the user submit again.

## 0.2.0 (2025-02-04)

- Changed default to `--stream`
- Added a tester. It's not perfect but it covers most cases

Minor:

- More robust parsing of settings

## 0.1.1 (2025-02-03)

(Bumping second digit. Should have done 0.1.0 instead of 0.0.5)

Added --env, documented options for environment files, and bug fixes.

## 0.0.5 (2025-02-03)

Made it so if you specify `--edit --edit`, it will create a recursive loop calling the editor after each chat too. This makes it *almost* like an interactive chat. To exit, just exit the editor without saving. Note that `--prompt` is only applied on the first round.

Also some documentation tweaks.

## 0.0.4 (2025-02-02)

- Added `--stream / --no-stream` to stream the model output to stdout in addition to the file.
- Added `--prompt` and `--edit` flags edit or add a new prompt.

The two above flags make it possible to treat textllm as a simple client without even opening the file!

Minor:

- Fixed bug that made textllm still rename a file.
- Decided to *remove* all of the commented models. They are added to the readme.

## 0.0.3 (2025-02-02)

More commented models in the template. Move to different numbering scheme

## 20250201.0.xx

These are the initial release but also in flux (the different .xx) as I work out kinks. Still. **BETA**
