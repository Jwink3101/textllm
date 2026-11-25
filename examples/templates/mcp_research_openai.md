# {AUTO_TITLE}

```toml
[llm]
model = "openai/gpt-6-luna"
max_output_tokens = 16000
# Hosted web search and client-executed MCP tools can coexist.
tools = [{ type = "web_search" }]
reasoning = { effort = "high", summary = "auto" }

[textllm]
backend = "responses"
reasoning_summary = "block"
header_level = 2
tokens = "stderr"

[textllm.title]
reasoning = { effort = "low" }

[textllm.tools]
max_rounds = 8
max_calls = 30

[[textllm.mcp]]
# Replace with your Streamable HTTP MCP endpoint.
url = "https://your-server.example/mcp"
name = "remote"
```

Created with {version} at {now}

{header}--- System ---

You are an expert assistant.

{header}--- User ---

