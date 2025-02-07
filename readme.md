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
|`$TEXTLLM_ENV_PATH` | Path to an environment file for API keys. They can also just be set directly.|
| `$TEXTLLM_EDITOR` | Set the editor for the `--edit` flag. Will fallback to `$EDITOR` then finally `vi`. |
| `$TEXTLLM_DEFAULT_MODEL` | Sets the default model if one is not specified AND sets one in a template for new chats |
| `$TEXTLLM_DEFAULT_TEMPERATURE` | Sets the default temperature if one is not specified AND sets one in a template for new chats |
| `$TEXTLLM_TEMPLATE_FILE` | Sets a file to read for the template. This is used for new chats but *not* the defaults.


textllm uses [python-dotenv][dotenv] to read `$TEXTLLM_ENV_PATH` but this also means you can have a `.env` file read automatically. These are read for executing the LLM only. Not for setting the above.

### API Environment Variables and loading

Most APIs called by LangChain require the API key be in the environment. For example `$OPENAI_API_KEY`, `$ANTHROPIC_API_KEY`, `$GOOGLE_API_KEY`.

These can be specified, as normal, outside of textllm, but you can also store them in a file. You can tell textllm where to find that file in any (or all) of three ways:

1. Set environment variable `$TEXTLLM_ENV_PATH`
2. Create a `.env` file for [python-dotenv][dotenv] to find
3. The `--env` command-line argument.


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

Specify settings for the object in [TOML][toml] format inside of a Markdown fenced code block. All settings are directly passed, including 'model'. The model should be in the format of "<provider>:<name>" where providers are those from LangChain. See [`init_chat_model` docs][init_chat_model] for the naming scheme and needed Python package and [Chat Models][chat models] for more details. The template settings are the default and these update them.

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

## Tips and Tricks

### Images

You can include images in the Markdown in normal format. They can be used to ask questions about specific images.

### Open vim at Bottom

If using `--edit` to edit the file before submitting, it would be nice to open at the bottom of the file. textllm will correctly handle flags in `$TEXTLLM_EDITOR` so you can do something like:

    export TEXTLLM_EDITOR="vim +"

[dotenv]:https://github.com/theskumar/python-dotenv
[toml]: https://toml.io/ 
[init_chat_model]: https://python.langchain.com/api_reference/langchain/chat_models/langchain.chat_models.base.init_chat_model.html
[chat models]: https://python.langchain.com/docs/integrations/chat/