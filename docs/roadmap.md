# Roadmap

This file captures known directions that are intentionally out of scope for the LiteLLM migration.

## Responses-Style Backend

textllm currently calls LiteLLM through a streaming chat-completion wrapper. The wrapper is intentionally small so a future Responses-style backend can be added without changing the file parser or CLI flow first.

A future backend may be useful for provider features that do not fit cleanly into chat completions, including richer reasoning controls and generated non-text outputs.

## Reasoning and Provider Settings

Conversation TOML is flat and passes settings through to LiteLLM. This should allow many provider settings, including future reasoning-related settings, without textllm maintaining its own allowlist.

Future versions may add textllm-owned settings for title generation, such as a separate title model, lower-cost title defaults, or title-specific reasoning controls. Those settings should be designed carefully so they do not collide with LiteLLM pass-through keys.

## Richer Outputs

Assistant responses are currently persisted as text only. If model output later includes images, tool calls, citations, structured data, or reasoning summaries, textllm will need a Markdown representation that stays readable while preserving useful metadata.

Image output or image generation may require a Responses-style backend or provider-specific LiteLLM support. That should be designed as an explicit extension to the conversation format rather than inferred from the current text-only assistant block.
