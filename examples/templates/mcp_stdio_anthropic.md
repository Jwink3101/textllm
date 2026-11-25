# {AUTO_TITLE}

```toml
[llm]
model = "anthropic/claude-haiku-5-5"
max_tokens = 16000
thinking = { type = "adaptive" }
output_config = { effort = "high" }

[textllm]
backend = "messages"
reasoning_summary = "block"
header_level = 2
tokens = "stderr"

[textllm.title]
output_config = { effort = "low" }

[[textllm.mcp]]
# Replace with your MCP server executable and its exact arguments.
# Each argument is a separate string, including paths containing spaces.
command = ["/path/to/your-mcp-server", "--your-option", "your-value"]
name = "local"
```

Created with {version} at {now}

{header}--- System ---

You are an expert assistant.

{header}--- User ---

