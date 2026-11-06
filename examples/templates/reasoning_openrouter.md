# {AUTO_TITLE}

```toml
model = "openrouter/anthropic/claude-sonnet-4.6"
max_output_tokens = 16000
# Returned reasoning depends on the routed model and provider.
reasoning = { effort = "high", summary = "auto" }
# Temperature is omitted for compatibility with reasoning models.

[textllm]
backend = "responses"
reasoning_summary = "block"
header_level = 2
tokens = "stderr"

[textllm.title]
reasoning = { effort = "low" }
```

Created with {version} at {now}

{header}--- System ---

You are an expert assistant.

{header}--- User ---

