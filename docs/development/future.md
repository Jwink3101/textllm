# Future Design Considerations

These are decisions to revisit, not committed features or implementation proposals.

## Native Reasoning Persistence Beyond Tool Calls

The current policy creates a sidecar only when the model requests a client tool call. Reasoning alone does not create a sidecar, even when MCP is configured. The policy keeps the reasoning related to a tool exchange, including its final answer, for native replay. All other reasoning is for display only. Refer to the [MCP design](design_mcp.md#stored-history-and-reasoning) and [file format](format_spec.md#sidecar-references).

Review this restriction when you extend support for provider-native conversation continuity. Some providers can reuse native reasoning across user turns without tool calls. If textllm discards this reasoning, it cannot use this capability. For example, [OpenAI documents cross-turn reasoning reuse](https://developers.openai.com/api/docs/guides/reasoning#preserve-reasoning-across-calls). textllm stores reasoning only for tool exchanges to keep its design simple. This restriction is not a universal provider requirement or best practice.

A future design must balance conversation continuity with the need to keep ordinary conversations self-contained in Markdown. Decide whether to make broader persistence optional. Specify how to handle changes to the provider or model, and how to handle opaque or signed state. Specify how users can explicitly include or exclude this history. Keep reasoning display settings separate from native replay. Never use editable `Reasoning` blocks as a replacement for provider-native state.