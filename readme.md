# textllm

This is a **SIMPLE** text-based interface to LLMs. It is not intended to be a general purpose or overly featureful tool. It is just an easy way to call an LLM and save results in a simple format (text/markdown). It can also read images referenced in the Markdown.

textllm uses [any-llm][any-llm] to interact with many AI models.

## Setup

Install from PyPI:

    $ pip install textllm

any-llm handles provider integrations. In most cases, install textllm and set the provider API key in the environment.

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

### Streaming and Prompts

You can use `--prompt` to specify the new prompt and/or `--edit` to open a terminal text editor before running. textllm always streams the response to stdout while also writing response chunks to the conversation file as they arrive.

## Titles and Names

As noted in "Format Description", the title is the first line. If `!!AUTO TITLE!!` is in the first line, textllm will generate a title for the document using the document settings, including the same model. This can be disabled or the title can be manually set. To regenerate a title, reset the title to `!!AUTO TITLE!!`.

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
| `$TEXTLLM_DEFAULT_TEMPERATURE` | Sets the default temperature if one is not specified and writes it into templates for new chats. |
| `$TEXTLLM_TEMPLATE_FILE` | Sets a file to read for the template. This is used for new chats but not for defaults, and is overridden by `--template`. |

These can be set before calling textllm or via an environment file, either `.env` or with the `--env` flag. The file can also be specified with `$TEXTLLM_ENV_PATH` except for itself of course. Template selection goes from `--template`, then `$TEXTLLM_TEMPLATE_FILE` from the current environment or loaded environment files, then the built-in default.

For custom templates that should follow environment defaults for different models, either omit `model` and `temperature` from the template settings or use placeholders such as `{model}` and `{temperature}`. See [Template Defaults and Environment Variables](docs/template_defaults.md) for examples.

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
reasoning_summary = "note"
```

The answer streams normally. URL citations are appended as a Markdown `Sources` section. With `reasoning_summary = "note"`, a provider-supplied reasoning summary is written in a local-only `--- Note ---` block. Raw hidden reasoning is not stored.

Use `--tokens {none,stderr,note}` to report every usage field returned by the provider for the latest conversational model call. Counts are not accumulated across earlier calls, and automatic-title usage is excluded. `stderr` prints the report separately from the streamed answer, while `note` appends it in a local-only `--- Note ---` block. The default is `none`. For persistent behavior, set the same value in the reserved table:

```toml
[textllm]
tokens = "note"
```

The command-line flag overrides the file setting. Field names are kept as returned by each backend, including nested cached-token, reasoning-token, and provider-specific usage details when available. Because each call sends the current conversation as context, its provider-reported input count normally includes that context. The completion backend requests streamed usage when reporting is enabled; a provider may still omit some or all usage fields. See [Prompt Caching](docs/prompt_caching.md) for OpenAI, Anthropic, and OpenRouter configuration examples and instructions for checking cache hits.

For a native Anthropic Messages reasoning call:

```toml
model = "anthropic/claude-sonnet-4-6"
max_tokens = 16000
thinking = { type = "adaptive" }
output_config = { effort = "medium" }

[textllm]
backend = "messages"
reasoning_summary = "note"
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
reasoning_summary = "note"
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

Specify settings in [TOML][toml] format inside a Markdown fenced code block. Settings are passed to any-llm except for `model`, which selects the provider and model, and the reserved `[textllm]` table, which controls textllm behavior. The template settings are the default and conversation settings update them.

Note that providers require API keys. Keys can be passed through settings when any-llm supports that, but environment variables or an environment file are usually better.

### (3) Conversation

The conversation is written with simple Markdown role blocks. `System`, `Developer`, `User`, and `Assistant` are supported and are sent as OpenAI-style roles. `Note` blocks are local annotations that remain in the Markdown file and are never sent to the model, including during automatic title generation.

```text
--- System ---

Enter your system prompt. These are like super user blocks.

--- User ---

The last "User" block is usually the question.

--- Assistant ---

The response.

--- Note ---

A private reminder for the human reader. This is not sent to the model.
```

Generally, you want the final block to be the new `User` question, but it does not have to be if `--no-require-user-prompt` is used. A new `--- User ---` heading will be added after the last response.

Role markers such as `--- User ---` and `--- Assistant ---` are reserved syntax when they appear at the start of a line. In normal assistant responses this is unlikely to matter, but if a response literally includes one of these markers, textllm may interpret it as a new conversation block on the next run. Prefix the marker with a backslash, for example `\--- User ---`, when you want it treated as ordinary text.

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

### Open Vim at Bottom

If using `--edit` to edit the file before submitting, it can be useful to open at the bottom of the file. textllm will correctly handle flags in `$TEXTLLM_EDITOR` so you can do something like:

    export TEXTLLM_EDITOR="vim +"

## More Docs

- [any-llm migration guide](docs/migration_any_llm.md)
- [LiteLLM migration guide](docs/migration_litellm.md)
- [File format specification](docs/format_spec.md)
- [Design](docs/design.md)
- [Roadmap](docs/roadmap.md)
- [Testing strategy](docs/testing_strategy.md)
- [Reasoning models and tools](docs/reasoning_and_tools.md)
- [Prompt caching](docs/prompt_caching.md)
- [Manual tests](docs/manual_tests.md)

[dotenv]: https://github.com/theskumar/python-dotenv
[toml]: https://toml.io/
[any-llm]: https://mozilla-ai.github.io/any-llm/
