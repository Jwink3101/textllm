# MCP Support

> [!NOTE]
> This document defines essential capabilities, desired outcomes, and necessary constraints. Unspecified behavior is intentionally flexible. Prefer simple, idiomatic, maintainable implementations over exact behavioral conformity where that behavior is incidental. Do not introduce disproportionate complexity to satisfy minor preferences.

## Status and Scope

Implemented initial scope; see [user documentation](../mcp.md). This document preserves the lightweight design intent. Add MCP tools while preserving textllm's Markdown workflow and leaving the execution/history format open to other tool integrations later.

Initial scope: stdio servers and Streamable HTTP servers with no authentication or a configured bearer token. Support text and structured JSON results. OAuth flows, other media, MCP prompts/resources as standalone features, sampling, and elicitation are deferred. Execution approval policy is also deferred; this implementation adds no command-trust approval flow.

## Configuration

List servers directly in the conversation's local settings. Discover and expose all tools from each listed server; no separate registry or selection table is required.

```toml
[[textllm.mcp]]
url = "https://example.com/mcp"
bearer_token = "${SEARCH_MCP_TOKEN}" # Optional; literal tokens also work

[[textllm.mcp]]
command = ["my-search-server", "--index", "/some/path with spaces"]
```

- Require exactly one of `url` or `command`. Infer Streamable HTTP or stdio respectively.
- Accept `command` as an argument array or a command-line string. Arrays preserve arguments exactly; strings use documented shell-style argument splitting, without shell execution, pipelines, or command substitution.
- An optional `name` identifies a server. Otherwise generate an identifier, disambiguate colliding tool names, and retain the mapping in history.
- Optional `env` values supply environment variables to a stdio server.
- Keep all MCP settings local; do not forward them as provider settings. Existing `[llm].tools` continues to configure provider-hosted tools.
- Preserve existing override semantics: overriding `textllm.mcp` replaces the array.

After environment loading and settings merging, expand `${NAME}` once in every parsed TOML string value, including nested provider and local settings. Use one snapshot of the loaded environment. Keys, comments, non-string values, and conversation text are unchanged. Template-only creation preserves unresolved variables; resolve and validate them when executing the conversation. `$${NAME}` produces literal `${NAME}`. Missing variables fail before server launch or conversation mutation. Parse TOML before expansion, and do not expand substituted values recursively. For a command string, split arguments before expansion so substituted paths containing spaces remain one argument.

`bearer_token` is the HTTP bearer-token value, supplied literally or with `${NAME}` through the ordinary one-pass settings expansion. Require a non-empty string; do not perform a second environment lookup. Redact the field from settings logs and exclude resolved credentials from sidecar history. A literal token remains in the saved conversation settings.

## Sidecar and Markdown References

The sidecar for `conversation.md` is `conversation.md.db`. Use JSONLiteDB with `wal_mode=False` and set `PRAGMA journal_mode=DELETE` directly on open, including for an existing WAL database.

Sidecars are reserved for MCP tool-call history and future client tool integrations. Reasoning alone never creates a sidecar, even with MCP configured. A sidecar is created only after the model requests a tool call. Reasoning returned during that tool exchange, including the final answer after execution, is retained for native replay in all display modes. Reasoning from a turn without tool calls remains display-only and is not replayed. This is the current storage policy, not a requirement of the sidecar format; revisit it if general provider-native state replay is added.

This policy trades general reasoning continuity for a simpler Markdown-only workflow outside tool use. Providers can support broader reuse: [OpenAI documents cross-turn native reasoning](https://developers.openai.com/api/docs/guides/reasoning#preserve-reasoning-across-calls), while [Anthropic requires intact thinking blocks when returning tool results](https://platform.claude.com/docs/en/build-with-claude/thinking-tool-workflows). Textllm currently preserves that state only for tool exchanges; readable Markdown summaries are not a substitute for opaque or signed native state.

Use UUIDv7 record keys (Unix milliseconds plus random bits), independent of filenames and database row IDs. Generate them with the standard library. IDs sort by timestamp, with random order within a millisecond. A generic marker keeps MCP out of the persistent role vocabulary:

```markdown
--- Assistant ---

I'll check the records.

--- Tools ---

[@sidecar reasoning](b635768e-34f6-4efb-a7cf-b6aa713de358)
[@sidecar tool exchange](827bc021-6da3-45fe-95c1-ec37a876ca9b)

--- Assistant ---

The records show ...
```

The reference resolves only against the conversation's conventional sidecar. It is not a link sent to the model. Preserve existing heading-level and escaped-marker conventions.

Markdown determines which records participate in replay and their order. Removing or reordering references changes replay, including referenced reasoning. Unreferenced sidecar records are ignored. Editing displayed summaries does not rewrite the underlying native history. Copying Markdown and its sidecar produces an independent branch.

textllm must track paired renames, moves, and copies, including automatic title renames and keeping temporary conversations. Manual file operations must preserve the pair. Never overwrite an existing sidecar on a naming collision. Missing sidecars, unknown keys, or incompatible reference ordering fail clearly; never guess, silently omit history, or rerun tools.

## Stored History and Reasoning

Keep the record schema versioned and independent of MCP. One referenced tool exchange represents a model response requesting one or more tools and the associated results. It may be persisted as incomplete while execution progresses.

Records retain:

- Local identity, explicit sequence/grouping, executor type, server/tool identity, and provider call IDs.
- Arguments, results, completion/error state, and the model-facing representation if different from the original result.
- Tool definitions or a snapshot reference sufficient to interpret historical calls.
- Provider-native response structure and required continuation data, including reasoning items and signatures. Preserve assistant text/block boundaries without duplicating visible text during replay.

Keep the existing reasoning settings (`omit`, `stderr`, and `block`) and their display behavior, including ignoring reasoning for display. Retain provider-returned reasoning in the sidecar when it belongs to a tool exchange, even when omitted from Markdown. Buffer the first response's reasoning until a completed tool request establishes that a sidecar is needed; reasoning-only responses do not open a database. Do not request or invent reasoning the provider does not return.

Whenever reasoning is used for continuation, read it from a separate sidecar reasoning record. `Reasoning` blocks contain display text only. A `Tools` block groups `[@sidecar reasoning](uuid)` followed by `[@sidecar tool exchange](uuid)`. The exchange records native reasoning positions, allowing explicit references to reconstruct the original payload without duplicating reasoning. Removing its `Tools` reasoning reference excludes it from replay.

Human-readable reasoning remains local-only under the existing rules. Replay provider-required reasoning/continuation items in their native form for tool exchanges, not as extra user or assistant prose. Automatic-title requests continue to exclude reasoning and tools. Native reasoning in the final answer after tool execution has a standalone `Tools` directive associated with that exchange. Its record retains the remaining native assistant payload and reasoning positions for later-turn replay. Displayed reasoning uses `Reasoning` blocks only when `block` mode is selected; those blocks are never a replay source. Only the Markdown directive reference syntax is supported. Native tool history is backend-specific; see the user documentation for provider-change restrictions.

## Execution, Replay, and Recovery

Model adapters expose tool calls and consume native tool results. A generic orchestration loop invokes an executor; MCP is the first executor and handles server connection, discovery, and calls. Continue streaming assistant text to stdout and Markdown.

Log each execution start and outcome with server/tool identity, call ID, round, and elapsed time, using normal CLI verbosity. Exclude arguments, result bodies, credentials, and raw transport exceptions. Console records interrupting streamed stdout start on a separate line without altering the Markdown answer.

Preserve call/result pairing and message ordering, including multiple calls in one response. Start with sequential execution. Include available tool definitions in requests offering tools, in a stable order. Replay referenced history faithfully; do not automatically summarize it or send transport logs and credentials. Avoid duplicate copies of the same structured result in model context.

Validate complete arguments before calling a tool. Set finite call/round, timeout, output, and context limits. Report tool errors as results where appropriate; explicitly report unsupported output content rather than silently dropping it. Do not automatically fetch resource links.

Persist call intent before execution and results promptly afterward. Commit sidecar data before publishing Markdown references; database transactions do not make Markdown and SQLite updates jointly atomic. Preserve partial exchanges on interruption. An interrupted call can have an unknown outcome: replay never executes history, and recovery must not automatically retry an uncertain operation. Storage failures must be reported without losing already streamed text or implying that an external action was rolled back.

## Security and Required Documentation

This is a local CLI: configured stdio commands run with the invoking user's privileges and access. MCP output is untrusted data. A future hosted service must revisit command execution and credential isolation and would likely disable stdio. Execution approval policy remains deferred.

Implementation must document configuration and interpolation, tool exposure, reference syntax and editing, sidecar storage/backup and paired file operations, reasoning persistence versus display/replay, supported outputs, limits, recovery, backend restrictions, and the local/hosted security distinction. Keep current user and development documentation aligned with the implemented behavior.
