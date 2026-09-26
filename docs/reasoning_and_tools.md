# Reasoning Models and Tools

textllm supports three any-llm model APIs:

- `completion`, the default, supports reasoning-effort controls for providers such as Anthropic and OpenRouter.
- `responses` supports typed output events and provider-hosted tools from OpenAI and OpenRouter.
- `messages` exposes any-llm's Anthropic-style Messages API. Anthropic uses its native API, OpenRouter uses its native Anthropic-compatible endpoint, and remaining any-llm providers may use a compatibility conversion.

Provider capabilities vary. An unsupported model, setting, or backend fails at the any-llm or provider layer rather than being silently changed by textllm.

Reasoning models may reject sampling settings such as `temperature`. Remove settings inherited from the default template when the selected provider or model does not support them.

Provider-side prompt caching can reduce the cost and latency of replaying long, unchanged context. Configuration, cache-preserving conversation structure, usage fields, and provider-specific limitations are documented in [Prompt Caching](prompt_caching.md).

## Completion Reasoning

The completion backend accepts any-llm's normalized `reasoning_effort` setting for providers that advertise completion reasoning. The model performs reasoning, but textllm stores only its final text because the completion adapter does not persist provider thinking content. With any-llm 1.27.0, OpenAI reasoning should use the Responses backend described below.

### Anthropic

```toml
model = "anthropic/claude-sonnet-4-6"
reasoning_effort = "medium"
max_tokens = 4000
```

For current adaptive-thinking models, any-llm maps an explicit `reasoning_effort` to native `thinking = { type = "adaptive" }` and `output_config.effort`. Exact supported effort levels remain model-dependent.

Claude 4.5 models use legacy budget-based thinking and do not support adaptive thinking. Configure those models directly instead of setting an explicit normalized effort:

```toml
model = "anthropic/claude-sonnet-4-5"
reasoning_effort = "auto"
max_tokens = 16000
thinking = { type = "enabled", budget_tokens = 8000 }
```

### OpenRouter

The part after `openrouter/` is the OpenRouter model identifier:

```toml
model = "openrouter/openai/gpt-5.5"
reasoning_effort = "high"
max_completion_tokens = 4000
```

OpenRouter support depends on the routed model. textllm passes the normalized setting to any-llm, which maps it to OpenRouter's reasoning configuration.

## Responses Reasoning

The Responses API uses a `reasoning` object rather than `reasoning_effort`:

```toml
model = "openai/gpt-5.5"
reasoning = { effort = "high" }

[textllm]
backend = "responses"
```

The backend must be selected explicitly. This avoids surprising behavior when switching to a provider without Responses support.

To request and retain a provider-supplied reasoning summary as a local annotation:

```toml
model = "openai/gpt-5.5"
reasoning = { effort = "high", summary = "auto" }

[textllm]
backend = "responses"
reasoning_summary = "block"
```

`reasoning_summary` accepts:

- `omit`, the default, which does not retain the summary.
- `block`, which writes it to local-only `--- Reasoning ---` blocks in stream-event order.
- `stderr`, which logs it when the selected CLI verbosity displays informational logs.

These are summaries intentionally returned by the provider. textllm does not request or persist raw hidden chain-of-thought.

With `block`, reasoning and answer deltas are persisted as they arrive. A stream can therefore produce `Reasoning`, `Assistant`, `Reasoning`, and `Assistant` blocks in sequence. Reasoning remains excluded from later requests, while assistant segments separated by reasoning are merged into one model-facing assistant message on replay. Only assistant text is streamed to standard output.

### OpenRouter Responses

any-llm 1.27.0 and newer supports OpenRouter's native OpenResponses endpoint. Keep the full OpenRouter catalog identifier after the `openrouter/` provider prefix:

```toml
model = "openrouter/anthropic/claude-sonnet-4.6"
reasoning = { effort = "medium" }

[textllm]
backend = "responses"
reasoning_summary = "block"
```

OpenRouter reads `$OPENROUTER_API_KEY` normally. Its Responses request schema fixes `store` to false and also exposes `previous_response_id`. textllm does not rely on either field; it replays the Markdown conversation on every call so the file remains the source of truth.

## Anthropic Messages

Use `backend = "messages"` when you want native Anthropic Messages settings and stream events rather than the completion compatibility layer. `max_tokens` is required by this API.

Claude Sonnet 4.6 with adaptive thinking and medium effort:

```toml
model = "anthropic/claude-sonnet-4-6"
max_tokens = 16000
thinking = { type = "adaptive" }
output_config = { effort = "medium" }

[textllm]
backend = "messages"
reasoning_summary = "block"
```

Claude 4.5 and older supported reasoning models require manual extended thinking with a token budget:

```toml
model = "anthropic/claude-sonnet-4-5"
max_tokens = 16000
thinking = { type = "enabled", budget_tokens = 8000 }

[textllm]
backend = "messages"
reasoning_summary = "block"
```

Do not combine an adaptive `thinking` object with `budget_tokens`. Effort belongs in `output_config`, not in `thinking`. Model support and valid effort levels vary, and thinking consumes the same `max_tokens` output allowance as answer text.

Anthropic sends thinking content in `thinking_delta` stream events. textllm treats provider-returned thinking text as its normalized reasoning-summary stream: `omit` discards it, `stderr` logs it, and `block` writes it to local-only `Reasoning` blocks in event order. This is content deliberately exposed by the provider, not access to unreturned hidden chain-of-thought. Reasoning blocks are excluded from later requests, and textllm does not preserve Anthropic thinking signatures or build a client-side tool loop.

The Markdown request is adapted to the Messages shape before any-llm is called:

- `System` and `Developer` blocks are joined, in order, into the separate Messages `system` parameter. A top-level TOML `system` setting is placed before them.
- `User` and `Assistant` blocks become the Messages `messages` list.
- Local and embedded images become Anthropic base64 image sources. HTTP(S) images become URL image sources.
- `Note` and `Reasoning` blocks remain local and are removed before conversion.

any-llm can expose its Messages interface for other providers through a Chat Completions compatibility bridge. That can be useful for a uniform application interface, but native-only parameters and exact hosted-tool behavior require a provider with a Messages API. Prefer `responses` for OpenAI features documented here.

### Messages Through OpenRouter

OpenRouter exposes an Anthropic-compatible `/api/v1/messages` endpoint. Select it with an OpenRouter-prefixed model and `$OPENROUTER_API_KEY`:

```toml
model = "openrouter/anthropic/claude-sonnet-4.6"
max_tokens = 16000
thinking = { type = "adaptive" }
output_config = { effort = "medium" }
tools = [{ type = "web_search_20260318", name = "web_search", max_uses = 5 }]

[textllm]
backend = "messages"
reasoning_summary = "block"
```

With any-llm 1.27.0, the ordinary `provider="openrouter"` Messages implementation uses any-llm's Chat Completions compatibility bridge even though OpenRouter now has a native Messages endpoint. textllm avoids that downgrade: for this backend and model prefix, it configures any-llm's Anthropic transport with OpenRouter's `https://openrouter.ai/api` base URL and the OpenRouter key. The request therefore reaches `https://openrouter.ai/api/v1/messages`, while the model id remains `anthropic/claude-sonnet-4.6`. Anthropic-format thinking, image, and tool fields are preserved; actual model and routed-provider support still determines which features succeed.

An explicit TOML `api_key` or `api_base` overrides the corresponding default, although keeping credentials in `$OPENROUTER_API_KEY` is recommended. OpenRouter-specific body fields that conflict with any-llm parameter names can be placed in `extra_body`; for example, routing preferences can use `extra_body = { provider = { sort = "price" } }`.

## Web Search

OpenAI-hosted web search uses the `web_search` Responses tool:

```toml
model = "openai/gpt-5.5"
reasoning = { effort = "medium", summary = "auto" }
tools = [{ type = "web_search" }]
max_tool_calls = 5

[textllm]
backend = "responses"
reasoning_summary = "block"
```

OpenRouter provides a namespaced `openrouter:web_search` server tool that can be used with models from different upstream providers:

```toml
model = "openrouter/anthropic/claude-sonnet-4.6"
reasoning = { effort = "medium" }
tools = [{ type = "openrouter:web_search", parameters = { max_results = 5 } }]
max_tool_calls = 5

[textllm]
backend = "responses"
reasoning_summary = "block"
```

Anthropic-hosted web search is available through the Messages backend. Anthropic versions server-tool schemas by date; this example selects the `20260318` schema:

```toml
model = "anthropic/claude-sonnet-4-6"
max_tokens = 16000
thinking = { type = "adaptive" }
output_config = { effort = "medium" }
tools = [{ type = "web_search_20260318", name = "web_search", max_uses = 5 }]

[textllm]
backend = "messages"
reasoning_summary = "block"
```

Older active Anthropic web-search tool versions may be preferable when their model coverage or feature set better matches the request. Check Anthropic's tool reference before selecting a dated type.

Search is opt-in and may add provider cost and latency. Search lifecycle events are logged. The final answer continues to stream to stdout and the conversation file. URL citation annotations are deduplicated and appended to the assistant response as ordinary Markdown:

```markdown
### Sources

- [Source title](<https://example.com/page>)
```

The source list becomes part of the assistant message and is therefore visible on later turns. Search queries and raw results are not persisted.

## Automatic Titles

Automatic-title calls use the selected backend but always remove `tools`, `tool_choice`, and `max_tool_calls`. A title will therefore never trigger web search. Optional title-specific provider settings can reduce reasoning cost:

```toml
model = "openai/gpt-5.5"
reasoning = { effort = "high", summary = "auto" }
tools = [{ type = "web_search" }]

[textllm]
backend = "responses"

[textllm.title]
reasoning = { effort = "low" }
```

Values under `[textllm.title]` override provider settings only for automatic-title generation. Tool settings in that table are also ignored.

For a Messages conversation, use native title overrides such as:

```toml
[textllm.title]
thinking = { type = "disabled" }
```

Only use a title override accepted by the selected model. For models where thinking is always enabled, lower `output_config.effort` instead.

## Equivalent any-llm Calls

These Python examples show the provider calls behind the textllm adapters. textllm itself reads equivalent settings from the conversation TOML and always streams.

OpenAI Responses reasoning:

```python
from any_llm import responses

events = responses(
    provider="openai",
    model="gpt-5.5",
    input_data="Analyze the trade-offs.",
    reasoning={"effort": "high", "summary": "auto"},
    stream=True,
)
for event in events:
    if event.type == "response.output_text.delta":
        print(event.delta, end="", flush=True)
```

Native Anthropic Messages adaptive thinking:

```python
from any_llm import messages

events = messages(
    provider="anthropic",
    model="claude-sonnet-4-6",
    messages=[{"role": "user", "content": "Analyze the trade-offs."}],
    max_tokens=16000,
    thinking={"type": "adaptive"},
    output_config={"effort": "medium"},
    stream=True,
)
for event in events:
    if event.type == "content_block_delta" and event.delta.type == "text_delta":
        print(event.delta.text, end="", flush=True)
```

OpenRouter Responses reasoning keeps the upstream model namespace in the model id:

```python
from any_llm import responses

events = responses(
    provider="openrouter",
    model="anthropic/claude-sonnet-4.6",
    input_data="Analyze the trade-offs.",
    reasoning={"effort": "medium", "summary": "auto"},
    stream=True,
)
```

Native Anthropic-format Messages through OpenRouter use any-llm's Anthropic transport because any-llm 1.27.0 does not yet select OpenRouter's Messages endpoint itself:

```python
import os

from any_llm import messages

events = messages(
    provider="anthropic",
    api_base="https://openrouter.ai/api",
    api_key=os.environ["OPENROUTER_API_KEY"],
    model="anthropic/claude-sonnet-4.6",
    messages=[{"role": "user", "content": "Analyze the trade-offs."}],
    max_tokens=16000,
    thinking={"type": "adaptive"},
    output_config={"effort": "medium"},
    stream=True,
)
```

## Limitations

- Responses support depends on the any-llm provider. The completion backend remains the portable default.
- The Messages backend only persists final assistant text, URL citations, and an optional local `Reasoning` block. It does not preserve thinking signatures, tool-use blocks, arbitrary content blocks, or provider-side state for later turns.
- textllm replays the readable Markdown conversation on every call. It does not use `previous_response_id`, remote conversation storage, encrypted reasoning state, or provider-side persistent state.
- Only provider-hosted tools are transported. textllm does not execute arbitrary local function tools or implement a client-side agent loop.
- Citations received before interruption may not have reached the completed response, so a partial streamed answer can lack its final source list.

## Further Reading

- [textllm prompt caching guide](prompt_caching.md)
- [any-llm documentation](https://docs.mozilla.ai/any-llm/)
- [OpenAI reasoning model guidance](https://developers.openai.com/api/docs/guides/latest-model?model=gpt-5.5)
- [OpenAI web search guide](https://developers.openai.com/api/docs/guides/tools-web-search)
- [OpenRouter Responses reference](https://openrouter.ai/docs/api/api-reference/responses/create-responses)
- [OpenRouter web search guide](https://openrouter.ai/docs/guides/features/server-tools/web-search)
- [OpenRouter Anthropic Messages reference](https://openrouter.ai/docs/api/api-reference/anthropic-messages/create-messages)
- [Anthropic effort guide](https://platform.claude.com/docs/en/build-with-claude/effort)
- [Anthropic adaptive thinking guide](https://platform.claude.com/docs/en/build-with-claude/adaptive-thinking)
- [Anthropic web search tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-search-tool)
- [Anthropic tool reference](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-reference)
