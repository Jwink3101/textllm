# Migrating to LiteLLM

textllm 0.7.0 replaces LangChain with LiteLLM. The Markdown conversation format is mostly unchanged, but model names, dependencies, and streaming behavior changed.

## Model Names

Change LangChain-style model strings from `provider:model` to LiteLLM-style `provider/model`.

```toml
# Before
model = "openai:gpt-4o-mini"

# After
model = "openai/gpt-4o-mini"
```

Provider examples:

```toml
model = "openai/gpt-5.5"
model = "anthropic/claude-sonnet-4-5"
model = "gemini/gemini-2.5-pro"
model = "ollama/llama3.1"
model = "openrouter/openai/gpt-4o-mini"
```

textllm does not translate the old `provider:model` form at runtime. Update existing conversation files before using them with 0.7.0.

## Dependencies

You no longer need LangChain or provider-specific LangChain packages for textllm.

```bash
pip install --upgrade textllm
```

LiteLLM handles provider integrations. Keep provider API keys in the environment, such as `$OPENAI_API_KEY` or `$ANTHROPIC_API_KEY`, or load them with textllm's `.env`, `$TEXTLLM_ENV_PATH`, or `--env` support.

## Streaming

The `--stream` and `--no-stream` flags were removed. textllm always streams responses to stdout and writes the collected text to the conversation file.

Title generation also uses the streaming model path internally, but title text is not printed to stdout.

## Settings

TOML settings remain flat. `model` selects the LiteLLM model, and every other key is passed through to LiteLLM.

```toml
model = "openai/gpt-5.5"
temperature = 1.0
max_tokens = 2000
```

Unsupported settings now fail at the LiteLLM or provider layer rather than at a LangChain layer.

## Roles

The existing role blocks still work:

```text
--- System ---
--- User ---
--- Assistant ---
```

textllm 0.7.0 also supports:

```text
--- Developer ---
```

Internally, these are sent as OpenAI-style roles: `system`, `developer`, `user`, and `assistant`.

## Images

Markdown image input is still supported in user messages. Standalone image lines are converted into LiteLLM/OpenAI-style multimodal content blocks. Assistant output is still persisted as text only.
