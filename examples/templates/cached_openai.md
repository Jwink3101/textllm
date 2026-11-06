# {AUTO_TITLE}

```toml
model = "openai/gpt-5-mini"
max_output_tokens = 16000
reasoning = { effort = "medium" }
# OpenAI automatically caches eligible repeated prefixes.
# Keep earlier turns stable and append new turns to reuse the cached prefix.
# A stable workload key helps route requests sharing the same prefix.
prompt_cache_key = "textllm:cached:v1"

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

You are an expert assistant. Provide concise, accurate answers grounded in the supplied reference material. Cite document names and section or page references when available. Distinguish facts in the material from your inferences and identify missing information.

{header}--- User ---

