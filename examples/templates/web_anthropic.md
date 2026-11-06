# {AUTO_TITLE}

```toml
# Optional Settings
# Temperature is omitted because adaptive thinking does not support it.
model = 'anthropic/claude-sonnet-4-6'
max_tokens = 16000
tools = [{ type = "web_search_20260318", name = "web_search", max_uses = 5 }]
thinking = { type = "adaptive" }
output_config = { effort = "high" }

[textllm]
backend = "messages"
#reasoning_summary = "block"
header_level = 2
tokens = "stderr"

[textllm.title]
output_config = { effort = "low" }
```

Created with {version} at {now}

{header}--- System ---

You are an expert assistant.

{header}--- User ---

