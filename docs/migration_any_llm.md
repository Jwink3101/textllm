# Migrating to any-llm

textllm now uses any-llm instead of LiteLLM for streamed model calls. The Markdown conversation format is unchanged, and existing `provider/model` model strings continue to work.

## Model Names

textllm accepts both slash and colon model settings. Prefer slash form in textllm files:

```toml
model = "openai/gpt-4o-mini"
model = "openai:gpt-4o-mini"
```

Both examples select provider `openai` and model `gpt-4o-mini`. textllm splits only on the first separator, so nested model ids still work:

```toml
model = "openrouter/openai/gpt-4o-mini"
model = "openrouter:openai/gpt-4o-mini"
```

Use `provider:model` when copying examples from any-llm or when converting older LangChain-era files. Both forms are parsed the same way by current textllm.

## Dependencies

You no longer need LiteLLM for textllm.

```bash
pip install --upgrade textllm
```

any-llm handles provider integrations. Keep provider API keys in the environment, such as `$OPENAI_API_KEY`, `$ANTHROPIC_API_KEY`, or `$GEMINI_API_KEY`, or load them with textllm's `.env`, `$TEXTLLM_ENV_PATH`, or `--env` support.

## Settings

TOML settings remain flat. `model` selects the provider and model, and every other key is passed through to any-llm.

```toml
model = "openai/gpt-5.5"
temperature = 1.0
max_tokens = 2000
```

Unsupported settings now fail at the any-llm or provider layer.

## Streaming and Images

textllm still streams responses to stdout and writes the collected text to the conversation file. Title generation also uses the streaming model path internally, but title text is not printed to stdout.

Markdown image input is still supported in user messages. Standalone image lines are converted into OpenAI-style multimodal content blocks. Assistant output is still persisted as text only.
