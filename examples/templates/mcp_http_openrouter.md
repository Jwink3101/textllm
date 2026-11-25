# {AUTO_TITLE}

```toml
[llm]
model = "openrouter/anthropic/claude-haiku-5.5"
max_tokens = 16000

[textllm]
backend = "completion"
header_level = 2
tokens = "stderr"

[[textllm.mcp]]
# Replace with your Streamable HTTP MCP endpoint.
url = "https://your-server.example/mcp"
name = "remote"
```

Created with {version} at {now}

{header}--- System ---

You are an expert assistant.

{header}--- User ---

