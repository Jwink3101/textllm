# {AUTO_TITLE}

```toml
[llm]
# Optional Settings
temperature = {temperature} # {{temperature}}
model = 'openrouter/anthropic/claude-haiku-5.5'
tools = [
    { type = "openrouter:web_search", parameters = { max_results = 5 } },
    { type = "openrouter:web_fetch" },
]
reasoning = { effort = "high"}

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

