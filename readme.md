# textllm

This is a **SIMPLE** text-based interface to LLMs. It is not intended to be a general purpose or overly featureful tool. It is just an easy way to call an LLM and save results in a simple format (text/markdown). It can also read images referenced in the Markdown.

textllm uses [any-llm][any-llm] to interact with many AI models.

## Setup

Install from PyPI:

    $ pip install textllm

any-llm handles provider integrations. In most cases, install textllm and set the provider API key in the environment.

From a source checkout, run `python textllm.py` to use the local development code whether or not textllm is installed. The launcher also works when called by path from another directory. Runtime dependencies must still be installed. Both the checkout and installed package support `python -m textllm`.

## Usage

Simply call textllm. If no file is specified, it will create `New Conversation.md` with an incremented filename as needed. If the file does not exist, a template will be written.

    $ textllm
    $ textllm mytitle.md

That will look something like:

````text
# !!AUTO TITLE!!

```toml
# Optional Settings
temperature = 1.0
model = 'openai/gpt-5.5'
```

Created with textllm-0.7.0 at 2026-05-24T12:00:00-06:00

--- System ---

You are an expert assistant. Provide concise, accurate answers.

--- User ---

````

Then modify the system prompt if needed and add your query under the user prompt. Then run:

    $ textllm mytitle.md

textllm will update the title if needed, stream the response to stdout, append the response to the file, and add a new user block ready for the next prompt.

Role separators may also be Markdown headings, such as `## --- User ---`. Set `[textllm].header_level` or `$TEXTLLM_DEFAULT_HEADER_LEVEL` to an integer from `0` through `6` to control newly written role markers; `0`, the default, writes plain separators. The `--header-level {0..6}` option overrides the file and environment settings for one invocation. The built-in template uses the effective level for its initial markers. Explicit templates can opt in with `{header}--- User ---`; templates without `{header}` remain unchanged.

### Streaming and Prompts

You can use `--prompt` to specify the new prompt and/or `--edit` to open a terminal text editor before running. textllm always streams the response to stdout while also writing response chunks to the conversation file as they arrive.

## Titles and Names

As noted in "Format Description", the title is the first line. If `!!AUTO TITLE!!` is in the first line, textllm will generate a title for the document using the document settings. By default it uses the conversation model; `[textllm.title]` can override the model and provider settings for titles while keeping the same backend. See [Automatic Titles](docs/reasoning_and_tools.md#automatic-titles) for examples. This can be disabled or the title can be manually set. To regenerate a title, reset the title to `!!AUTO TITLE!!`.

If `--rename` is set, the document will also be renamed from the title. Numbers will be added to avoid conflicts if needed. `--rename` is the default for new files. This means you can do something like:

    $ textllm --prompt "What is the meaning of life, the universe, and everything"

And it will respond and rename `New Conversation.md` to something like `Meaning of Life Inquiry.md`.

## Environment Variables

Most behavior is governed by command-line flags but there are a few exceptions.

| Variable | Description |
|--|--|
| `$TEXTLLM_ENV_PATH` | Path to an environment file for API keys. They can also just be set directly. |
| `$TEXTLLM_EDITOR` | Set the editor for the `--edit` flag. Will fall back to `$EDITOR` and then `vi`. |
| `$TEXTLLM_DEFAULT_MODEL` | Sets the default model if one is not specified and writes it into templates for new chats. |
| `$TEXTLLM_DEFAULT_TEMPERATURE` | Fills `{temperature}` in templates for new chats; omitted conversation settings are not sent to the provider. |
| `$TEXTLLM_DEFAULT_HEADER_LEVEL` | Sets the default heading level for newly written role markers when the file does not set `[textllm].header_level`. Use `0` through `6`; the default is `0`. |
| `$TEXTLLM_TEMPLATE_FILE` | Selects a template path or `@name` for new chats. Overridden by `--template`; does not supply runtime defaults to existing conversations. |
| `$TEXTLLM_TEMPLATE_DIR` | Directory whose immediate `.md` files define named templates. |
| `$TEXTLLM_NAMED_TEMPLATE_<name>` | Registers a template file path as `@name`, overriding a same-named directory entry. |

These can be set before calling textllm or via an environment file, either `.env` or with the `--env` flag. The file can also be specified with `$TEXTLLM_ENV_PATH` except for itself of course. Template selection goes from `--template`, then `$TEXTLLM_TEMPLATE_FILE` from the current environment or loaded environment files, then the built-in default.

Ready-to-use web and prompt-caching templates for OpenAI, Anthropic, and OpenRouter are available in [Example Templates](examples/README.md).

Templates can also be selected by name. Set `TEXTLLM_TEMPLATE_DIR` to a directory of Markdown templates, or register individual files with `TEXTLLM_NAMED_TEMPLATE_<name>`:

```dotenv
TEXTLLM_TEMPLATE_DIR="${HOME}/.config/textllm/templates"
TEXTLLM_NAMED_TEMPLATE_special="${HOME}/work/custom template.md"
```

```bash
textllm --template @special
textllm --list-templates
```

An explicit registration wins over a same-named directory template. `TEXTLLM_TEMPLATE_FILE=@special` selects a named default. A selector beginning with `@` is always a name; use `./@special` for a literal filename starting with `@`. See [Named Templates](docs/template_defaults.md#named-templates) for discovery, precedence, and environment expansion details.

For custom templates that should follow environment defaults for different models, omit `model` to use the current default at call time, or use placeholders such as `{model}` and `{temperature}` to record defaults in new files. Omit `temperature` to let the provider choose its default; this is required for models that reject that parameter. To use `$TEXTLLM_DEFAULT_HEADER_LEVEL` for the template's initial role markers, write `{header}--- System ---` and `{header}--- User ---`. For example, `TEXTLLM_DEFAULT_HEADER_LEVEL=2` renders these as `## --- System ---` and `## --- User ---`, unless `[textllm].header_level` or `--header-level` overrides it. See [Template Defaults and Environment Variables](docs/template_defaults.md) for examples.

### API Environment Variables and Loading

any-llm usually reads provider API keys from environment variables. For example, OpenAI uses `$OPENAI_API_KEY`, Anthropic uses `$ANTHROPIC_API_KEY`, and Google uses `$GEMINI_API_KEY` or provider-specific any-llm settings.

These can be specified outside of textllm, but you can also store them in a file. You can tell textllm where to find that file in any or all of three ways:

1. Set environment variable `$TEXTLLM_ENV_PATH`
2. Create a `.env` file for [python-dotenv][dotenv] to find
3. Use the `--env` command-line argument

## Models and Settings

Any model understood by the selected any-llm API can be used. The default backend is the completion API. Set `[textllm].backend = "responses"` for providers and models that implement any-llm's Responses API, or `"messages"` for any-llm's Anthropic-style Messages API. Anthropic uses Messages natively. OpenRouter model names use OpenRouter's native Anthropic-compatible Messages endpoint. Remaining providers may use any-llm's compatibility bridge. Prefer `provider/model` in textllm files. textllm also accepts `provider:model` for compatibility with any-llm examples and older LangChain-era files, and passes the provider and remaining model id to any-llm.

```toml
model = "openai/gpt-5.5"
model = "openai:gpt-5.5"
model = "openai/gpt-4o-mini"
model = "anthropic/claude-sonnet-4-5"
model = "gemini/gemini-2.5-pro"
model = "ollama/llama3.1"
model = "openrouter/openai/gpt-4o-mini"
```

All TOML settings except `model` and the reserved `[textllm]` table are passed through to any-llm. Unsupported settings will fail at the any-llm or provider layer. Reasoning controls, OpenAI and OpenRouter Responses examples, native Anthropic Messages examples, reasoning summaries, and built-in web search are documented in [Reasoning Models and Tools](docs/reasoning_and_tools.md).

For example, an OpenAI reasoning call with hosted web search can use:

```toml
model = "openai/gpt-5.5"
reasoning = { effort = "high", summary = "auto" }
tools = [{ type = "web_search" }]
max_tool_calls = 5

[textllm]
backend = "responses"
reasoning_summary = "block"
```

For OpenAI Responses, requesting and saving a reasoning summary require two separate settings: `summary = "auto"` inside the top-level `reasoning` object requests a summary from OpenAI, and `[textllm].reasoning_summary = "block"` tells textllm to save any returned summary. Setting `effort = "high"` alone enables high reasoning effort without requesting a summary. Reasoning tokens can therefore appear in usage reports even when no summary is returned.

The answer streams to terminal stdout. Returned summaries are saved in local-only `--- Reasoning ---` blocks in the conversation file; they are not printed to stdout. URL citations are appended as a Markdown `Sources` section. Reasoning and answer blocks are written in stream-event order, so a provider may produce alternating `Reasoning` and `Assistant` blocks. On later calls, the reasoning blocks are omitted and the assistant segments are merged into one model-facing message. Summaries are provider-supplied explanations, not the raw hidden reasoning.

Use `--tokens {none,stderr,note}` to report every usage field returned by the provider for the latest conversational model call. Counts are not accumulated across earlier calls, and automatic-title usage is excluded. `stderr` prints the report separately from the streamed answer, while `note` appends it in a local-only `--- Note ---` block. The default is `none`. For persistent behavior, set the same value in the reserved table:

```toml
[textllm]
tokens = "note"
```

The command-line flag overrides the file setting. Field names are kept as returned by each backend, including nested cached-token, reasoning-token, and provider-specific usage details when available. Because each call sends the current conversation as context, its provider-reported input count normally includes that context. The completion backend requests streamed usage when reporting is enabled; a provider may still omit some or all usage fields. See [Prompt Caching](docs/prompt_caching.md) for OpenAI, Anthropic, and OpenRouter configuration examples and instructions for checking cache hits.

Token reports are written only after the model call completes successfully and returns answer text. Failed or interrupted calls can leave a partial response in the file without a usage report, even if usage events arrived before the failure.

For a native Anthropic Messages reasoning call:

```toml
model = "anthropic/claude-sonnet-4-6"
max_tokens = 16000
thinking = { type = "adaptive" }
output_config = { effort = "medium" }

[textllm]
backend = "messages"
reasoning_summary = "block"
```

The Messages backend requires `max_tokens`. It converts `System` and `Developer` blocks to the separate Messages system prompt and converts Markdown image inputs to Anthropic image blocks.

The same Messages shape can be sent through OpenRouter by changing only the model provider prefix and setting `$OPENROUTER_API_KEY`:

```toml
model = "openrouter/anthropic/claude-sonnet-4.6"
max_tokens = 16000
thinking = { type = "adaptive" }
output_config = { effort = "medium" }

[textllm]
backend = "messages"
reasoning_summary = "block"
```

## Format Description

The format is designed to be very simple. An input is broken up into three main parts:

1. Title (optional)
2. Settings (optional)
3. Conversation

### (1) Title:

The first line of the document. If and only if it contains `!!AUTO TITLE!!`, it will be replaced with an appropriate title based on the document using the LLM.

Generally, this is only set once, but if `!!AUTO TITLE!!` is added back to the first line, it will get refreshed.

### (2) Settings

Specify settings in [TOML][toml] format inside a Markdown fenced code block. Settings are passed to any-llm except for `model`, which selects the provider and model, and the reserved `[textllm]` table, which controls textllm behavior. A missing `model` uses the environment default. Other provider parameters, including `temperature`, are sent only when present in the conversation settings.

Note that providers require API keys. Keys can be passed through settings when any-llm supports that, but environment variables or an environment file are usually better.

### (3) Conversation

The conversation is written with simple Markdown role blocks. `System`, `Developer`, `User`, and `Assistant` are supported and are sent as OpenAI-style roles. `Note` and `Reasoning` blocks are local annotations that remain in the Markdown file and are never sent to the model, including during automatic title generation. `Reasoning` is used for provider-returned reasoning summaries when selected.

Every role marker may be plain or an ATX Markdown heading from level 1 through 6. For example, `--- Assistant ---`, `# --- Assistant ---`, and `## --- Assistant ---` are equivalent when read. Newly written markers use `--header-level`, then `[textllm].header_level`, then `$TEXTLLM_DEFAULT_HEADER_LEVEL`, in that precedence order. Existing markers are not rewritten, so a file may safely contain mixed levels.

```text
--- System ---

Enter your system prompt. These are like super user blocks.

--- User ---

The last "User" block is usually the question.

--- Assistant ---

The response.

--- Note ---

A private reminder for the human reader. This is not sent to the model.

--- Reasoning ---

A provider-returned reasoning summary. This is not sent to the model.
```

Generally, you want the final block to be the new `User` question, but it does not have to be if `--no-require-user-prompt` is used. A new `User` role marker will be added after the last response.

Role markers such as `--- User ---` and `## --- Assistant ---` are reserved syntax when they appear at the start of a line. In normal assistant responses this is unlikely to matter, but if a response literally includes one of these markers, textllm may interpret it as a new conversation block on the next run. Prefix the entire marker with a backslash, for example `\--- User ---` or `\## --- Assistant ---`, when you want it treated as ordinary text.

## Tips and Tricks

### Images

You can include images in the Markdown in normal format. Standalone image lines in user messages are converted into OpenAI-style multimodal input blocks.

### Attachments

Attach a non-image file with a Markdown link whose label starts with `@attach`. The optional text is sent to the model as context; the link itself is sent as a file input. The directive can appear inline in prose or on its own line:

```markdown
[@attach Q3 financial report](docs/q3-report.pdf)
```

Markdown reference links work too, which keeps a shared path in one place:

```markdown
[@attach Q3 financial report][q3-report]

[q3-report]: docs/q3-report.pdf
```

Attachments are part of the user message that contains them. textllm replays the complete Markdown conversation on later calls, so it sends that file again with each later request. File capability and accepted types depend on the selected model backend.

Use `@include` instead for a local UTF-8 text file that should become ordinary prompt text rather than a provider file input:

```markdown
[@include deployment configuration](config/production.yaml)
```

textllm adds included contents after a clearly labelled delimiter. This works with any model backend, but can use substantial context for large files. `@attachment` remains supported as an alias for `@attach`.

Local attachments and included files are reread from disk on each invocation; their contents are not copied into the conversation file. Editing a referenced file changes the context sent for earlier turns, and deleting it can prevent the conversation from continuing. Keep a separate copy if you need the original contents preserved.

### Open Vim at Bottom

If using `--edit` to edit the file before submitting, it can be useful to open at the bottom of the file. textllm will correctly handle flags in `$TEXTLLM_EDITOR` so you can do something like:

    export TEXTLLM_EDITOR="vim +"

## More Docs

- [any-llm migration guide](docs/migration_any_llm.md)
- [LiteLLM migration guide](docs/migration_litellm.md)
- [Template defaults and environment variables](docs/template_defaults.md)
- [Roadmap](docs/roadmap.md)
- [Testing strategy](docs/testing_strategy.md)
- [Reasoning models and tools](docs/reasoning_and_tools.md)
- [Prompt caching](docs/prompt_caching.md)
- [Custom API endpoints (`api_base`)](docs/api_base.md)
- [Manual tests](docs/manual_tests.md)

Architecture and format details are in the [developer documentation](docs/development/README.md). Unimplemented ideas are kept separately in [proposed designs](docs/proposals/README.md).

[dotenv]: https://github.com/theskumar/python-dotenv
[toml]: https://toml.io/
[any-llm]: https://mozilla-ai.github.io/any-llm/
