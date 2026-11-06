# {AUTO_TITLE}

```toml
model = "anthropic/claude-sonnet-4-6"
max_tokens = 16000
thinking = { type = "adaptive" }
output_config = { effort = "medium" }
# Automatic prefix caching for repeated questions about long context.
# Keep earlier turns stable and append new turns to reuse the cached prefix.
cache_control = { type = "ephemeral" }
# For longer pauses, replace the line above with this higher-cost option:
#cache_control = { type = "ephemeral", ttl = "1h" }

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

You are an expert assistant. Provide concise, accurate answers grounded in the supplied reference material. Cite document names and section or page references when available. Distinguish facts in the material from your inferences and identify missing information.

{header}--- User ---

