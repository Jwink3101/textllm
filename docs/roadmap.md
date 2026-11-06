# Roadmap

This file captures known directions that are intentionally out of scope for the provider-backend migration.

## Typed Streaming Backends

textllm now supports explicit Responses and Messages backends through any-llm. Their typed streams are normalized alongside the original completion stream without changing the role-block parser or ordinary CLI flow. Anthropic uses its native Messages API, OpenRouter-prefixed calls use OpenRouter's Anthropic-compatible Messages endpoint, and any-llm can bridge the same interface to other providers.

The current typed-stream scope includes provider-returned reasoning content, provider-hosted web-search status, URL citations, usage, and streamed answer text. Future work may cover generated non-text outputs or additional hosted tools where they have a clear Markdown representation.

## Reasoning and Provider Settings

Conversation TOML passes provider settings through to any-llm. The reserved `[textllm]` table holds local behavior without creating a provider-setting allowlist.

The `[textllm.title]` table provides title-specific overrides, including a separate model while retaining the conversation's backend. See [Automatic Titles](reasoning_and_tools.md#automatic-titles). Future versions may add lower-cost defaults if those defaults justify additional local configuration.

## Richer Outputs

Assistant responses remain readable text. URL citations use a Markdown `Sources` section, and optional provider-returned reasoning content uses local-only `Reasoning` blocks. Search traces, thinking signatures, and raw tool objects are intentionally not persisted.

Image output or image generation may require a Responses-style backend or provider-specific any-llm support. That should be designed as an explicit extension to the conversation format rather than inferred from the current text-only assistant block.

Client-executed function tools would require an approval model, execution loop, tool-result representation, and interruption recovery. They remain out of scope until that workflow can preserve textllm's simple and safe file interface.
