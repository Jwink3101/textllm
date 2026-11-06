# Custom API Endpoints

Set `api_base` in the conversation's top-level TOML settings to send requests to a custom server, proxy, or gateway. textllm passes it to any-llm alongside the selected provider and model. It belongs outside the reserved `[textllm]` table.

## OpenAI-Compatible Servers

For a server implementing streamed Chat Completions, use the `openai/` provider prefix and the model identifier advertised by that server:

```toml
model = "openai/your-served-model"
api_base = "http://localhost:8000/v1"

[textllm]
backend = "completion"
```

Replace the address and model with your server's values. Here, `openai/` selects the any-llm OpenAI adapter; it does not mean the model is hosted by OpenAI. The adapter removes that prefix before sending the model identifier.

Use the server's documented API root, including `/v1` when required. Do not append `/chat/completions` or `/responses`: the selected client constructs the operation URL. Base-path conventions depend on the provider adapter and server, so `/v1` is not a universal suffix.

Changing the address does not translate API formats. A Chat Completions-compatible server need not support Responses, Messages, reasoning controls, images, or hosted tools. Start with minimal settings and select a backend the endpoint actually implements. textllm always requests streaming.

## Credentials

Use the environment variable expected by the selected adapter, such as `OPENAI_API_KEY` for the OpenAI adapter or `ANTHROPIC_API_KEY` for the Anthropic adapter. A gateway may require its own credential in that variable. Some clients require a nonempty key even when a local server does not authenticate; use a non-secret placeholder only if the server permits it.

A top-level `api_key` setting can override the credential, but it is stored in the readable conversation file. Prefer environment variables for secrets. The configured endpoint receives the conversation and supplied inputs, along with the credential used for that request; use credentials intended for that endpoint.

## Responses and Messages

To use a Responses-compatible endpoint, set `backend = "responses"` under `[textllm]` and retain the top-level `api_base`. For native Anthropic-compatible Messages, use the `anthropic/` prefix, `backend = "messages"`, and a top-level `max_tokens` setting. Follow the endpoint's documented base URL and authentication requirements.

OpenRouter Messages is a special case already handled by textllm:

```toml
model = "openrouter/anthropic/claude-sonnet-4.6"
max_tokens = 16000

[textllm]
backend = "messages"
```

This selects Anthropic transport with the base URL `https://openrouter.ai/api` and `OPENROUTER_API_KEY`, reaching `/api/v1/messages`. No explicit `api_base` is needed. A top-level `api_base` or `api_key` overrides the corresponding default. Other OpenRouter backends use their normal any-llm provider adapter.

## Automatic Titles and Existing Conversations

Automatic-title requests inherit `api_base` and other provider settings. If you override the title model, make sure that model is served at the inherited endpoint, or override `api_base` under `[textllm.title]` as well. Titles keep the conversation's selected backend, so both endpoints must support it. See [automatic titles](reasoning_and_tools.md#automatic-titles).

Edit an existing conversation's TOML directly to change its endpoint. Changing a template affects only new conversations.

## Troubleshooting

- Connection errors: check the address, port, server availability, and whether HTTP or HTTPS is required.
- HTTP 404: check the base path and whether the server implements the selected backend's operation.
- Authentication errors: check which adapter supplies credentials and whether the endpoint accepts that key.
- Unknown model errors: use the model identifier served by the endpoint, following textllm's provider prefix.
- Unsupported parameter errors: remove settings inherited from another provider, especially reasoning, sampling, and tool settings.

See [any-llm documentation](https://docs.mozilla.ai/any-llm/) for provider configuration and [reasoning and tools](reasoning_and_tools.md) for textllm's backend differences.
