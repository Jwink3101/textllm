# Earlier Backend Migrations

This page consolidates the historical LangChain-to-LiteLLM and LiteLLM-to-any-llm migrations. For the current configuration change, see [Migrating Settings to llm](migration_llm_settings.md).

## Dependencies and Model Names

Version 0.7.0 replaced LangChain with LiteLLM; version 0.8.0 replaced LiteLLM with any-llm. Current textllm needs neither LangChain nor LiteLLM. Upgrade with `pip install --upgrade textllm`; provider credentials remain in environment variables or textllm environment files.

Prefer `provider/model`, such as `openai/gpt-6-luna`. Current releases also accept `provider:model`, including older LangChain-era names. Only the first separator is split, preserving nested model identifiers such as `openrouter/openai/gpt-6-luna`. The temporary slash-only restriction in 0.7.x no longer applies.

Provider parameters now belong under `[llm]`; `[textllm]` controls local behavior. Unsupported provider parameters fail at the any-llm or provider layer.

## Streaming and Conversation Files

The old `--stream` and `--no-stream` flags were removed in 0.7.0. Responses always stream to stdout and the conversation file as chunks arrive. Automatic titles use streaming internally without printing title chunks to stdout.

Existing role blocks and Markdown image inputs remain supported. Version 0.7.0 added the `Developer` role. Assistant output is persisted as text.

## Additional Backends

Version 0.9.0 added optional Responses and Messages backends for reasoning, hosted web search, citations, and token usage. These require any-llm 1.27.0 or newer. Messages calls require `max_tokens`. See [Reasoning Models and Tools](reasoning_and_tools.md) for current configuration examples and transport details.
