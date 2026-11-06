# {AUTO_TITLE}

```toml
model = "openrouter/anthropic/claude-sonnet-4.6"
# Native Anthropic-format thinking through OpenRouter Messages.
max_tokens = 16000
thinking = { type = "adaptive" }
output_config = { effort = "high" }
# Temperature is omitted for compatibility with reasoning models.

[textllm]
backend = "messages"
reasoning_summary = "block"
header_level = 2
tokens = "stderr"

[textllm.title]
output_config = { effort = "low" }
```

Created with {version} at {now}

{header}--- System ---

You are an expert assistant.

{header}--- User ---

