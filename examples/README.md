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
| [mcp_http_openai.md](templates/mcp_http_openai.md) | Responses | Placeholder HTTP MCP endpoint with OpenAI, without MCP authentication. |
| [mcp_http_openrouter.md](templates/mcp_http_openrouter.md) | Chat Completions | Placeholder HTTP MCP endpoint, without MCP authentication. |
| [mcp_bearer_openai.md](templates/mcp_bearer_openai.md) | Responses | Your HTTP MCP server with an environment-backed bearer token and explicit limits. |
| [mcp_stdio_anthropic.md](templates/mcp_stdio_anthropic.md) | Messages | Placeholder local MCP server command over stdio. |
| [mcp_research_openai.md](templates/mcp_research_openai.md) | Responses | Placeholder HTTP MCP endpoint combined with hosted web search and visible reasoning. |

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

Returned reasoning text is written to the conversation file as it arrives in `block` mode; only answer text streams to stdout. Returned reasoning and native continuation data are also retained in the sidecar, including when display is omitted. Displayed reasoning remains local-only; native reasoning is replayed from its own explicit sidecar directive grouped with the exchange directive under `Tools`. No readable reasoning text can mean the provider returned no summary, even when usage reports reasoning tokens. See [reasoning and tools](../docs/reasoning_and_tools.md) for model-specific controls and limitations.

## MCP Examples

These examples use the MCP SDK v2 support in textllm. All tools exposed by each configured server become available to the model. The model API key is separate from MCP authentication. Calls, results, and returned reasoning are stored in an adjacent `.md.db` sidecar; keep it with the Markdown when copying or moving a conversation. Automatic-title calls do not connect to MCP servers or use tools.

### HTTP Server

Replace `https://your-server.example/mcp` in the selected template with your Streamable HTTP MCP endpoint. These examples omit MCP authentication. Set `OPENAI_API_KEY` for the OpenAI example or `OPENROUTER_API_KEY` for the OpenRouter example, then create a conversation:

```bash
textllm --template examples/templates/mcp_http_openai.md --prompt "Describe the information available through the connected tools."
# Or use --template examples/templates/mcp_http_openrouter.md with OpenRouter.
```

### HTTP Server with a Bearer Token

Set `OPENAI_API_KEY` for the model and supply your own MCP endpoint and token through the shell or an environment file:

```bash
export MCP_URL="https://your-server.example/mcp"
export MCP_BEARER_TOKEN="your-token"
textllm --template examples/templates/mcp_bearer_openai.md --prompt "Describe the information available through the connected tools."
```

The URL example is a placeholder; replace it with a Streamable HTTP endpoint that accepts a static bearer token. OAuth-only servers are not supported. `url = "${MCP_URL}"` and `bearer_token = "${MCP_BEARER_TOKEN}"` are expanded once at execution. A literal `bearer_token` value is also supported. You can create the conversation first with `textllm --template @mcp_bearer_openai chat.md` before setting either variable; they are required when executing it. This example omits readable reasoning from Markdown while retaining tool-associated reasoning in the sidecar, and demonstrates optional tool limits.

### Local Stdio Server

Set `ANTHROPIC_API_KEY` and replace the placeholder `command` array in `mcp_stdio_anthropic.md` with your MCP server executable and its exact arguments. Install any dependencies required by your chosen server.

```bash
textllm --template examples/templates/mcp_stdio_anthropic.md --prompt "Describe the information available through the connected tools."
```

Each array element is one argument, so a path containing spaces stays in a single string. An ordinary CLI command does not become an MCP server just by putting it in this array. Relative paths resolve from textllm's working directory; use absolute paths where needed when continuing elsewhere.

### MCP with Hosted Web Search

Set `OPENAI_API_KEY` and replace the placeholder URL in `mcp_research_openai.md` with your Streamable HTTP MCP endpoint to combine its tools with OpenAI-hosted web search. This example displays returned reasoning and reports aggregate token usage to stderr. Hosted web search can add provider charges. Local `[textllm.tools]` limits bound MCP execution; provider-specific hosted-tool limits remain separate settings under `[llm]`.

All five templates also work with named selection, such as `--template @mcp_bearer_openai`, after setting `TEXTLLM_TEMPLATE_DIR` as described above. For connection settings, environment expansion, outputs, sidecars, and recovery, see [MCP tools and sidecars](../docs/mcp.md).

The default stderr logs show when each MCP call starts and finishes, identifying the server/tool and call ID with elapsed time on completion. Tool errors and uncertain outcomes are reported separately. Use `-q` to suppress successful-call progress.

For custom servers and gateways, see [custom API endpoints](../docs/api_base.md).
