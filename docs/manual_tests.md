# Manual Tests

These are some behaviors that are not tested automatically but should be confirmed.

- Using --stdin with --prompt and/or --edit
- Edit mode without modifying file
- Multi-image description
- OpenAI Responses reasoning with a requested summary
- OpenAI Responses web search with at least one Markdown source
- OpenRouter Responses reasoning and `openrouter:web_search` with at least one Markdown source
- Anthropic and OpenRouter completion reasoning with `reasoning_effort`
- Anthropic Messages adaptive thinking with `thinking`, `output_config.effort`, and a local reasoning note
- Anthropic Messages manual thinking on a 4.5 model with `budget_tokens`
- Anthropic Messages `web_search_20260318` with at least one Markdown source
- Anthropic-format Messages through an `openrouter/anthropic/...` model, confirming thinking and native hosted-tool events use `$OPENROUTER_API_KEY`
- Anthropic Messages image input for one local image and one HTTP(S) image
- An automatic title using the Messages backend, confirming that configured tools and `tool_choice` are absent from the title request

Live checks must be deliberate and opt-in. Confirm the intended provider credentials, cost, and network use before running them. Anthropic hosted tools may also require organization-level enablement. Example settings are in [Reasoning Models and Tools](reasoning_and_tools.md).
