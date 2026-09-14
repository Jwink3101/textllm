# Prompt Caching

Prompt caching lets a provider reuse an unchanged prefix of a model request. It can reduce input cost and latency for long, repeated context. It does not remove that context from the model's context window, reuse an earlier answer, or create a local cache managed by textllm.

textllm is a good fit for prefix caching because it replays the Markdown conversation on every call. As turns are appended, the system instructions and earlier conversation remain an unchanged prefix of the next request. Provider settings in the conversation's TOML block are passed through to any-llm, so caching remains an opt-in provider capability rather than a textllm-specific cache format.

Caching is most useful when the reusable prefix is large enough to meet the provider and model's minimum. Typical examples include long system instructions, tool definitions, examples, reference documents, or an established multi-turn conversation. Do not pad a short prompt merely to reach a caching threshold.

## OpenAI

OpenAI automatically caches eligible repeated prompt prefixes. No setting is required to enable ordinary implicit caching. A stable `prompt_cache_key` can improve cache-hit rates by helping requests with the same prefix reach the same cache:

```toml
model = "openai/gpt-5.6"
prompt_cache_key = "textllm:research-assistant:v1"

[textllm]
backend = "responses"
tokens = "note"
```

Use a stable, non-secret label for a workload whose opening instructions and tools are the same. Change its version when that reusable prefix changes substantially. The key influences request routing; it does not name stored prompt content or guarantee a cache hit.

Models that support OpenAI's newer cache controls can set them in the Responses request body through `extra_body`:

```toml
model = "openai/gpt-5.6"
prompt_cache_key = "textllm:research-assistant:v1"
extra_body = { prompt_cache_options = { mode = "implicit", ttl = "30m" } }

[textllm]
backend = "responses"
tokens = "note"
```

Some earlier models use `prompt_cache_retention = "24h"` instead. Cache options, retention values, minimum prompt sizes, and model support are provider details that may change. Check the [OpenAI prompt caching guide](https://developers.openai.com/api/docs/guides/prompt-caching) for the selected model.

The completion backend can also benefit from OpenAI's implicit prompt caching. When token reporting is enabled, textllm requests usage in the final streaming chunk so cached-token details can be displayed when the provider returns them.

## Anthropic Messages

Anthropic supports automatic caching for a growing conversation through the top-level `cache_control` setting:

```toml
model = "anthropic/claude-sonnet-4-6"
max_tokens = 16000
cache_control = { type = "ephemeral" }

[textllm]
backend = "messages"
tokens = "note"
```

The default ephemeral cache has a short lifetime. For supported models, a one-hour entry can be requested explicitly:

```toml
cache_control = { type = "ephemeral", ttl = "1h" }
```

On the first request, Anthropic writes eligible content through the last cacheable block. On a later request with the same prefix, it reads the old prefix from cache and writes the newly appended turns. The automatic breakpoint moves forward as the conversation grows, so a textllm file does not need block-level cache markers.

Longer retention and cache creation can cost more than ordinary input. Caching is therefore most valuable when the entry will be reused before it expires. See the [Anthropic prompt caching guide](https://platform.claude.com/docs/en/build-with-claude/prompt-caching) for current model thresholds, pricing, breakpoint rules, and supported lifetimes.

## Anthropic Messages Through OpenRouter

The same automatic `cache_control` setting can be used with an Anthropic model through textllm's OpenRouter Messages backend:

```toml
model = "openrouter/anthropic/claude-sonnet-4.6"
max_tokens = 16000
cache_control = { type = "ephemeral" }

[textllm]
backend = "messages"
tokens = "note"
```

OpenRouter attempts to keep a conversation on the provider endpoint holding its cached prefix. Support still depends on the routed model and provider. Top-level automatic Anthropic caching may also constrain which upstream endpoints are eligible. Consult the [OpenRouter prompt caching guide](https://openrouter.ai/docs/guides/best-practices/prompt-caching) before combining caching with explicit provider-routing preferences.

This is provider prompt caching, which reuses input processing while still generating a new answer. It is distinct from OpenRouter response caching, which can return a previously generated response for an identical request.

## Preserving Cacheable Prefixes

Cache matching is based on request prefixes, not just the visible conversation text. For the best chance of a hit:

- Put stable system or developer instructions before changing conversation content.
- Keep tool definitions and their ordering stable between turns.
- Append new turns instead of rewriting earlier ones.
- Keep the model and relevant provider settings stable.
- Expect edits near the beginning of a conversation to invalidate more cached content than edits near the end.

Local `--- Note ---` and `--- Reasoning ---` blocks do not disturb the model-facing prefix because textllm removes them before normal chat and automatic-title requests. An automatic-title request has a different prompt shape and should not be expected to reuse the conversational prefix.

## Checking Cache Usage

Set `tokens = "note"` to append provider usage to a local-only note, or invoke textllm with `--tokens stderr` to report it without changing the conversation body:

```toml
[textllm]
tokens = "note"
```

For OpenAI Responses, a cache hit commonly appears as a nonzero nested value:

```text
Token usage:

- input_tokens: 10500
- input_tokens_details.cached_tokens: 10240
- output_tokens: 180
- total_tokens: 10680
```

Chat Completions providers may use `prompt_tokens_details.cached_tokens` instead. A zero or absent cached-token field can mean that the prompt was too short, the cache expired, the prefix changed, the request reached another provider endpoint, or the selected model does not support caching.

Anthropic reports cache reads and writes separately:

```text
Token usage:

- input_tokens: 120
- cache_creation_input_tokens: 300
- cache_read_input_tokens: 10000
- output_tokens: 180
```

For Anthropic, total input processed is:

```text
input_tokens + cache_creation_input_tokens + cache_read_input_tokens
```

Anthropic's `input_tokens` counts only the input after the final cache breakpoint. When the provider does not return `total_tokens`, textllm currently synthesizes it from `input_tokens + output_tokens`; that synthesized field does not include Anthropic cache-read or cache-creation tokens. Use the individual usage fields above when assessing total context and cache effectiveness.

Token reports cover only the latest conversational model call. They are not accumulated across earlier turns, and automatic-title usage is excluded.

## Privacy and Portability

Caching changes how a provider temporarily handles prompt processing and may have retention or data-control implications. Review the provider's current data policy before enabling extended retention for sensitive conversations.

Do not assume one provider's cache settings work with another provider or backend. textllm deliberately passes settings through without translating them into a common caching schema. Unsupported settings fail at the any-llm or provider layer.

## Related Documentation

- [README models and settings](../readme.md#models-and-settings)
- [Reasoning models and tools](reasoning_and_tools.md)
- [File format specification](format_spec.md)
