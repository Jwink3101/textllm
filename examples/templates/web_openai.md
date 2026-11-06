# {AUTO_TITLE}

```toml
# Optional Settings
temperature = {temperature} # {{temperature}}
model = 'openai/gpt-6.1-sol'
tools = [{ type = "web_search" }]
reasoning = { effort = "high"}
#reasoning = { effort = "high", summary = "auto" }

[textllm]
backend = "responses"
#reasoning_summary = "block"
header_level = 2
tokens = "stderr"

[textllm.title]
reasoning = { effort = "low" }
```

Created with {version} at {now}

{header}--- System ---

You are an expert assistant.

{header}--- User ---

