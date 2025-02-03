# textllm

This is a **SIMPLE** text-based interface to LLMs. It is not intended to be a general purpose or overly featureful tool. It is just an easy way to call an LLM and save results in a simple format (text/markdown)

textllm uses [LangChain][LangChain] to interact with many AI models. 

[LangChain]:https://www.langchain.com/

## Setup

Install from PyPI

    $ pip install textllm

Then depending on needs

    $ pip install langchain-openai
    $ pip install langchain-anthropic
    $ pip install langchain-google-genai
    ...


## Usage

Create a new text file. See the format description below for more:

    $ textllm --new mytitle.md
    
That will look something like:

    # !!AUTO TITLE!!
    
    ```toml
    # Optional Settings
    # TOML Format
    temperature = 0.5
    model = "openai:gpt-4o"
    
    # END Optional Settings
    ```
    
    --- System ---
    
    You are a helpful assistant. Provide clear and thorough answers but be concise unless instructed otherwise.
    
    --- User ---


Then (optionally) modify the System prompt and add your query under the user prompt. Then

    $ textllm mytitle.md

and it will (a) update the title, and (b) add the response, with a new user block ready to go, below it. You will need to re-open the text editor when its done.

### Streaming and Prompts

By settings `--stream` (to make textllm stream the output as well) and setting `--prompt` or using `--edit`, this can be like a command-line editor. Use `--silent` to really make it just input and output. Note that a filename must still be provided! 

## Titles and Names

As noted in "Format Description", the title is the first line. If "!!AUTO TITLE!!" is in the first line, textllm will generate a title for the document (using the same model). This can be disabled or just manually set the title. To regenerate a title, reset the title to `!!AUTO TITLE!!`.

If `--rename` is set, the document will also be renamed for the title. Numbers will be added to the name as needed to avoid conflicts if needed.

## Environment Variables

Most behavior is governed by command-line flags but there are a few exceptions. 

| Variable | Description |
|--|--|
|`$TEXTLLM_ENV_PATH` | Path to an environment file for API keys. |
|`$TEXTLLM_AUTO_RENAME` | Set to "true" to make `--rename` the *default*. Command-line settings will override. |
| `$TEXTLLM_STREAM` | Set to "true" to make `--stream` the *default*. Command-line settings will override. |
| `$TEXTLLM_EDITOR` | Set the editor for the `--edit` flag. Will fallback to `$EDITOR` then finally `vi`. |

## Other Models

Any model understood by the LangChain function [here][init_chat_model] could be used. Below are some examples:

```toml
model = "openai:gpt-4o"  # pip install langchain-openai
model = "openai:gpt-4o-mini"
model = "anthropic:claude-3-5-sonnet-latest"  # pip install langchain-anthropic
model = "anthropic:claude-3-5-haiku-latest"
model = "google_genai:gemini-1.5-pro" # pip install langchain-google-genai
model = "google_genai:gemini-1.5-flash" 
```

## Format Description

The format is designed to be very simple. An input is broken up into three main parts:

1. Title (optional)
2. Settings (optional)
3. Conversation

### (1) Title:

The first line of the document. If and only if it contains "!!AUTO TITLE!!", it will be replaced with an appropriate title based on the document (using the LLM).

Generally, this is only set once, but if "!!AUTO TITLE!!" is added back to the first line, it will get refreshed.

### (2) Settings

Optionally specify settings for the object in [TOML][toml] format inside of a Markdown fenced code block. Do NOT modify the leading comments, as they are needed for the correct parsing. All settings are directly passed, including 'model'. The model should be in the format of "<provider>:<name>" where providers are those from LangChain. See [`init_chat_model` docs][init_chat_model] for the naming scheme and needed Python package and [Chat Models][chat models] for more details.

Note that they require an API key. It can be specified in the settings or can be set with an environment variable. Alternatively, an environment file can be specified with '$TEXTLLM_ENV_PATH' that may contain all API keys.

### (3) Conversation

The conversation is with a simple format. There are three block types as demonstrated below. General practice is to specify 'system' at the top and only once, but textllm will translate all that are specified.

```text 
--- System ---

Enter your system prompt. These are like *super* user blocks.

--- User ---

The last "User" block is usually the question.

--- Assistant ---

The response. 
```

Generally, you want the final block to be the new "User" question but it doesn't have to be. Note that a new "--- User ---" heading will be added after the last response.

You can escape a block with a leading "\". It will be done if somehow the response also has such a block.


[toml]: https://toml.io/ 
[init_chat_model]: https://python.langchain.com/api_reference/langchain/chat_models/langchain.chat_models.base.init_chat_model.html
[chat models]: https://python.langchain.com/docs/integrations/chat/