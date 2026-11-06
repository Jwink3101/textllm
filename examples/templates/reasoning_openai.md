# {AUTO_TITLE}

```toml
model = "openai/gpt-5-mini"
max_output_tokens = 16000
# Effort controls reasoning; summary requests provider-returned summary text.
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

