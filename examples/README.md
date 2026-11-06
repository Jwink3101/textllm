# Example Templates

These optional templates are starting points for new conversations. Review their model IDs and provider settings before use; model availability and hosted-tool schemas can change.

| Template | Backend | Purpose |
| --- | --- | --- |
| [web_openai.md](templates/web_openai.md) | Responses | OpenAI web search with high reasoning effort. |
| [web_anthropic.md](templates/web_anthropic.md) | Messages | Anthropic web search with high-effort adaptive thinking. |
| [web_openrouter.md](templates/web_openrouter.md) | Responses | OpenRouter web search and fetch with high reasoning effort. |
| [reasoning_openai.md](templates/reasoning_openai.md) | Responses | OpenAI reasoning effort and returned summaries. |
| [reasoning_anthropic.md](templates/reasoning_anthropic.md) | Messages | Native Anthropic adaptive thinking and effort. |
| [reasoning_openrouter.md](templates/reasoning_openrouter.md) | Responses | OpenRouter reasoning using the Responses schema. |
| [reasoning_openrouter_messages.md](templates/reasoning_openrouter_messages.md) | Messages | Native Anthropic-format thinking through OpenRouter. |
| [cached_openai.md](templates/cached_openai.md) | Responses | Automatic prefix caching with a stable workload routing key. |
| [cached_anthropic.md](templates/cached_anthropic.md) | Messages | Automatic ephemeral prefix caching for a growing conversation. |
| [cached_openrouter.md](templates/cached_openrouter.md) | Default Chat Completions | Anthropic prefix caching through OpenRouter. |

From the repository root, select a template by path:

```bash
textllm --template examples/templates/web_openrouter.md
```

Alternatively, set `TEXTLLM_TEMPLATE_DIR` to the `examples/templates` directory using an absolute path appropriate to your checkout. You can then use `textllm --template @web_openrouter` or `textllm --list-templates`. Templates apply only when creating a new conversation.

Set the selected provider's API key: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, or `OPENROUTER_API_KEY`. OpenRouter templates use the OpenRouter key even when the routed model is Anthropic. Search and fetch can add provider charges.

All examples use level-2 role headings and report token usage to stderr. Cached templates work best with long, stable prefixes and repeated requests before the cache expires; a cache hit is not guaranteed. Anthropic and OpenRouter cached examples include a commented one-hour retention option, which can increase cache-write cost. OpenAI manages cache eligibility and retention for its selected model automatically.

See [template selection and defaults](../docs/template_defaults.md), [reasoning and tools](../docs/reasoning_and_tools.md), and [prompt caching](../docs/prompt_caching.md) for details.

## Reasoning Examples

The reasoning templates enable reasoning without web tools or cache-specific settings. They omit temperature, save provider-returned reasoning in local-only `Reasoning` blocks, and use lower effort for automatic titles. Set `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, or `OPENROUTER_API_KEY` for the selected provider; both OpenRouter examples use the OpenRouter key.

OpenAI Responses uses `reasoning.effort` and requests a summary with `reasoning.summary`. Anthropic Messages uses `thinking` and `output_config.effort`; the OpenRouter Messages example preserves that native schema. OpenRouter Responses uses the Responses `reasoning` object, with returned reasoning dependent on the routed model and provider. These separate examples make the API differences explicit.

Returned reasoning is written to the conversation file as it arrives; only answer text streams to stdout. Reasoning blocks are excluded from later chat and title requests. An absent block can mean the provider returned no reasoning text, even when usage reports reasoning tokens. textllm does not preserve thinking signatures or replay provider reasoning state. See [reasoning and tools](../docs/reasoning_and_tools.md) for model-specific controls and limitations.

For custom servers and gateways, see [custom API endpoints](../docs/api_base.md).
