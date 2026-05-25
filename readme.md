# textllm

This is a **SIMPLE** text-based interface to LLMs. It is not intended to be a general purpose or overly featureful tool. It is just an easy way to call an LLM and save results in a simple format (text/markdown). It can also read images referenced in the Markdown.

textllm uses [LiteLLM][litellm] to interact with many AI models.

## Setup

Install from PyPI:

    $ pip install textllm

LiteLLM handles provider integrations. In most cases, install textllm and set the provider API key in the environment.

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

You can use `--prompt` to specify the new prompt and/or `--edit` to open a terminal text editor before running. textllm always streams the response to stdout while also writing the collected response to the conversation file.

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
| `$TEXTLLM_TEMPLATE_FILE` | Sets a file to read for the template. This is used for new chats but not for defaults. |

These can be set before calling textllm or via an environment file, either `.env` or with the `--env` flag. The file can also be specified with `$TEXTLLM_ENV_PATH` except for itself of course.

For custom templates that should follow environment defaults for different models, either omit `model` and `temperature` from the template settings or use placeholders such as `{model}` and `{temperature}`. See [Template Defaults and Environment Variables](docs/template_defaults.md) for examples.

### API Environment Variables and Loading

LiteLLM usually reads provider API keys from environment variables. For example, OpenAI uses `$OPENAI_API_KEY`, Anthropic uses `$ANTHROPIC_API_KEY`, and Google uses `$GEMINI_API_KEY` or provider-specific LiteLLM settings.

These can be specified outside of textllm, but you can also store them in a file. You can tell textllm where to find that file in any or all of three ways:

1. Set environment variable `$TEXTLLM_ENV_PATH`
2. Create a `.env` file for [python-dotenv][dotenv] to find
3. Use the `--env` command-line argument

## Models and Settings

Any model understood by LiteLLM's [completion API][completion] can be used. The model should use LiteLLM's provider/model naming, for example:

```toml
model = "openai/gpt-5.5"
model = "openai/gpt-4o-mini"
model = "anthropic/claude-sonnet-4-5"
model = "gemini/gemini-2.5-pro"
model = "ollama/llama3.1"
```

All TOML settings except `model` are passed through to LiteLLM. Unsupported settings will fail at the LiteLLM or provider layer.

## Format Description

The format is designed to be very simple. An input is broken up into three main parts:

1. Title (optional)
2. Settings (optional)
3. Conversation

### (1) Title:

The first line of the document. If and only if it contains `!!AUTO TITLE!!`, it will be replaced with an appropriate title based on the document using the LLM.

Generally, this is only set once, but if `!!AUTO TITLE!!` is added back to the first line, it will get refreshed.

### (2) Settings

Specify settings in [TOML][toml] format inside a Markdown fenced code block. All settings are directly passed to LiteLLM, except for `model`, which textllm uses to select the model. The template settings are the default and conversation settings update them.

Note that providers require API keys. Keys can be passed through settings when LiteLLM supports that, but environment variables or an environment file are usually better.

### (3) Conversation

The conversation is written with simple Markdown role blocks. `System`, `Developer`, `User`, and `Assistant` are supported and are sent as OpenAI-style roles.

```text
--- System ---

Enter your system prompt. These are like super user blocks.

--- User ---

The last "User" block is usually the question.

--- Assistant ---

The response.
```

Generally, you want the final block to be the new `User` question, but it does not have to be if `--no-require-user-prompt` is used. A new `--- User ---` heading will be added after the last response. You can escape a block marker with a leading `\`; textllm will also do this automatically if a response contains a marker.

## Tips and Tricks

### Images

You can include images in the Markdown in normal format. Standalone image lines in user messages are converted into LiteLLM/OpenAI-style multimodal input blocks.

### Open Vim at Bottom

If using `--edit` to edit the file before submitting, it can be useful to open at the bottom of the file. textllm will correctly handle flags in `$TEXTLLM_EDITOR` so you can do something like:

    export TEXTLLM_EDITOR="vim +"

## More Docs

- [LiteLLM migration guide](docs/migration_litellm.md)
- [File format specification](docs/format_spec.md)
- [Design](docs/design.md)
- [Roadmap](docs/roadmap.md)
- [Testing strategy](docs/testing_strategy.md)
- [Manual tests](docs/manual_tests.md)

[dotenv]: https://github.com/theskumar/python-dotenv
[toml]: https://toml.io/
[litellm]: https://docs.litellm.ai/
[completion]: https://docs.litellm.ai/docs/completion/input
