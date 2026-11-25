# {AUTO_TITLE}

```toml
[llm]
model = "openai/gpt-6-luna"
max_output_tokens = 16000
reasoning = { effort = "medium", summary = "auto" }

[textllm]
backend = "responses"
# Keep returned reasoning in the sidecar without displaying its text.
reasoning_summary = "omit"
header_level = 2
tokens = "stderr"

[textllm.title]
reasoning = { effort = "low" }

[textllm.tools]
max_rounds = 6
max_calls = 20
timeout = 60
max_result_chars = 50000
max_context_chars = 1000000

[[textllm.mcp]]
# Set both variables in your shell or an environment file.
url = "${MCP_URL}"
bearer_token = "${MCP_BEARER_TOKEN}"
name = "remote"
```

Created with {version} at {now}

{header}--- System ---

You are an expert assistant.

{header}--- User ---

