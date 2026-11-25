# MCP Tools and Sidecars

textllm uses the MCP Python SDK v2 (`mcp>=2.3.0,<3`) to discover and call tools exposed by MCP servers. Configure servers in the conversation's TOML; all tools from those servers become available to the model. Supports stdio and Streamable HTTP, with no authentication or a configured HTTP bearer token. OAuth login and refresh are not supported.

See the [MCP template examples](../examples/README.md#mcp-examples) for placeholder HTTP, bearer-authenticated HTTP, local stdio, and mixed MCP/web-search starting points.

## Configuration

```toml
[llm]
model = "openai/gpt-6-luna"

[[textllm.mcp]]
url = "https://example.com/mcp"
bearer_token = "${SEARCH_MCP_TOKEN}" # Optional; expanded at execution

[[textllm.mcp]]
command = ["my-search-server", "--index", "/some/path with spaces"]
name = "local_search" # Optional

[textllm.mcp.env]
SEARCH_API_KEY = "${LOCAL_SEARCH_KEY}"
```

Each entry requires exactly one of `url` or `command`. A URL selects Streamable HTTP. A command launches a stdio server and must speak MCP; an arbitrary command-line tool is not an MCP server.

A command can also be a string, such as `command = "my-search-server --index '/some/path with spaces'"`. Strings use POSIX shell-style argument splitting, without invoking a shell. Arrays supply exact arguments and are preferable for unusual filenames. Pipelines, shell substitutions, and shell home expansion do not run. Stdio processes inherit the invoking environment plus explicit `env` entries; HTTP entries do not accept `env`.

Names are optional, unique within the conversation, and contain 1-32 letters, digits, underscores, or hyphens. Defaults are `server_1`, `server_2`, etc. Model-facing tool names include a server prefix and stable tool-name hash to disambiguate tools and fit API naming limits. The sidecar retains original server/tool identities. A supplied name keeps that prefix stable when entries are reordered.

MCP settings are local and never forwarded as provider parameters. Existing `[llm].tools` declarations still configure provider-hosted tools and can coexist with MCP. Overriding `textllm.mcp` replaces the entire array under the usual settings merge rules. Servers are connected afresh on each conversational invocation; automatic-title calls do not connect to them.

## Environment Expansion

MCP configuration uses the same [environment references as all TOML settings](../readme.md#environment-references-in-toml). There is no additional MCP expansion pass. Command strings are split before expansion, preserving spaces inside substituted arguments; arrays supply exact arguments.

`bearer_token` supplies the HTTP token itself: use `bearer_token = "${SEARCH_MCP_TOKEN}"` for environment-backed credentials, or `bearer_token = "your-token"` for a literal value. It must resolve to a non-empty string and is only supported for HTTP servers. It follows the same single expansion pass as other TOML strings, with no extra environment lookup, and becomes the `Authorization: Bearer ...` header. Token fields are redacted from settings logs, and resolved connection credentials are not written to tool history. A literal token remains in the Markdown settings, so use environment references when sharing or versioning conversations. Treat URLs and command arguments as configuration, not as places to embed credentials.

## Conversation History

Tool calls and results are recorded in the adjacent JSONLiteDB sidecar: `conversation.md.db` for `conversation.md`. SQLite WAL is disabled explicitly, including on existing databases; journal mode is `DELETE`.

New `Tools` blocks are wrapped in HTML comments by default, hiding their markers and sidecar references in rendered Markdown while keeping them editable in the source. Set `tools_display = "block"` under `[textllm]` to write visible blocks, or `tools_display = "comment"` for the default. This setting affects newly appended blocks only; visible and commented blocks can coexist and have identical replay semantics. The examples below show the unwrapped contents for readability. Older textllm versions do not reliably read commented blocks; use `"block"` when sharing with those versions. HTML comments hide presentation, not access to the references.

```markdown
--- Assistant ---

I'll check the records.

--- Tools ---

[@sidecar reasoning](b635768e-34f6-4efb-a7cf-b6aa713de358)
[@sidecar tool exchange](827bc021-6da3-45fe-95c1-ec37a876ca9b)

--- Assistant ---

The records show ...
```

References use UUIDv7 keys (a millisecond timestamp plus random bits) within this conversation's sidecar. Generation uses only the Python standard library. Keys sort by timestamp, with random order within a millisecond. They are not file paths or text sent to the model. Tool records contain the model's calls, arguments, results, discovered definitions, original executor identities, native continuation payload, and execution state. Textllm reconstructs native calls and results for the selected backend. Displayed assistant text comes from Markdown, so editing it affects subsequent requests without duplicating original text from the sidecar.

Use standalone `[@sidecar optional description](uuid)` directives under `Tools`. The optional description is informational; the referenced record determines its kind. Generated blocks use one blank line after the marker, consecutive reference lines, and no trailing hard-break spaces. `Reasoning` blocks contain readable display text only, without sidecar directives.

Markdown controls inclusion and order. Reasoning associated with a tool call precedes its exchange reference in the same `Tools` block. Native reasoning returned with the final answer after tool execution has its own `Tools` reference after that answer and needs no exchange reference. A block can hold multiple pairs or exchanges without reasoning. Remove both references to exclude a complete exchange. Unreferenced records remain in the database but are ignored. Copy both files to branch a conversation. Duplicate, missing, malformed, incomplete, or invalidly ordered references fail explicitly. Reading history never executes tools again.

Native tool history is tied to its original backend. Switching between completion, Responses, and Messages with referenced tool history fails rather than attempting a lossy conversion. Changing provider/model within a backend may still fail if the new model rejects previous reasoning signatures, native items, or tool capabilities. Start a fresh conversation or explicitly remove incompatible exchanges when needed.

## Reasoning

Sidecars are reserved for MCP tool-call history and future client tool integrations. Reasoning alone never creates a sidecar, even with MCP configured. A sidecar is created only after the model requests a tool call. Reasoning returned during that tool exchange, including the final answer after execution, is retained for native replay in all display modes. Reasoning from a turn without tool calls remains display-only and is not replayed. This is the current storage policy, not a requirement of the sidecar format; revisit it if general provider-native state replay is added.

This policy trades general reasoning continuity for a simpler Markdown-only workflow outside tool use. Providers can support broader reuse: [OpenAI documents cross-turn native reasoning](https://developers.openai.com/api/docs/guides/reasoning#preserve-reasoning-across-calls), while [Anthropic requires intact thinking blocks when returning tool results](https://platform.claude.com/docs/en/build-with-claude/thinking-tool-workflows). Textllm currently preserves that state only for tool exchanges; readable Markdown summaries are not a substitute for opaque or signed native state.

The existing `reasoning_summary` modes remain: `omit` (default), `stderr`, and `block`. They control readable display. `block` streams returned text into local-only `Reasoning` blocks, `stderr` writes it directly to standard error after the response completes, and `omit` displays none. Once a tool call is requested, its associated provider-returned reasoning is retained in the sidecar in every mode. Standard output contains assistant answers only.

During a tool exchange, completed responses that return native reasoning receive a reasoning directive, including the final answer after execution. Final-answer reasoning uses a standalone `Tools` reference after the visible assistant answer:

```markdown
--- Assistant ---

The answer is 10.

--- Tools ---

[@sidecar reasoning](b635768e-34f6-4efb-a7cf-b6aa713de358)

--- User ---
```

The record preserves native reasoning, original ordering, and the remaining assistant response metadata. Reasoning-token usage alone does not imply a payload was returned. Readable summary events without native continuation data stay local; textllm does not turn displayed summaries into provider-native reasoning.

When a model response requests tools, its native reasoning is stored separately from the calls/results. The `Tools` block references the reasoning record followed by the tool-exchange record, as shown above. Replay combines those records once, preserving native order, returned signatures, and encrypted content. This also applies in `block` mode: the visible reasoning text remains display-only while the `Tools` reference selects the native reasoning sent to the provider. It is never read back as reasoning input.

Editing or deleting displayed reasoning does not change replay. Removing the reasoning directive from `Tools` excludes that reasoning from model input; textllm does not silently restore it from the exchange record. Providers that require reasoning for tool continuation may reject such edited history. Keep each reasoning/exchange pair together when moving or removing complete exchanges. A final-answer reference also restores native reasoning on later turns. Removing that entire reference block excludes its state. Model/provider compatibility and context policy determine how returned reasoning is reused.

Textllm only stores content returned by the provider. For Responses MCP requests with configured reasoning, textllm also requests returned encrypted reasoning content for stateless continuation. Automatic-title calls exclude tools and reasoning and do not write sidecar history.

## Results and Limits

Initial support covers text and structured JSON results. When structured JSON is present, send it once rather than also sending its duplicate text serialization. Original results remain in the sidecar. Unsupported media/resource content or oversized output produces an explicit error result for the model; textllm does not automatically fetch resource links. MCP execution errors are also returned to the model. Transport interruption is different: an external action may have succeeded, so textllm stops with an unknown outcome and does not retry it automatically.

Calls execute sequentially after complete arguments pass the discovered JSON Schema. Tool definitions are sent in stable order. History is not automatically summarized or truncated. Optional positive execution limits are:

```toml
[textllm.tools]
max_rounds = 10
max_calls = 50
timeout = 60
max_result_chars = 100000
max_context_chars = 1000000
```

`max_rounds` bounds tool-execution rounds before a final model response; `max_calls` bounds total calls in an invocation. `timeout` is the per-MCP-operation timeout in seconds. `max_result_chars` bounds the model-facing result, retaining the original in the sidecar. `max_context_chars` bounds serialized history and provider settings, including tool definitions, before each model request. This is a character budget, not a provider token-window estimator. Provider output/token limits still belong under `[llm]`. Usage reports aggregate the conversational model calls in the tool loop and exclude automatic titles.

## Call Logging

At the default verbosity, each MCP execution logs its start and completion to stderr. Records identify the original server/tool, the model's call ID, and the round at start; completion records include elapsed seconds. Tool error results and unsupported/oversized outputs produce warnings. An interrupted call logs an error with an unknown outcome and is not automatically retried. The CLI adds timestamps to these records.

With `-v`, DEBUG records also show connection/session initialization (including SDK-managed protocol negotiation), each `tools/list` page, and discovery totals. Setup operations log starts, completions or failures, server names, and elapsed seconds; discovery records include tool and page counts. URLs, command arguments, pagination cursors, credentials, and raw protocol payloads are excluded.

```text
INFO: MCP call started: 'deepwiki.ask_wiki_question' (call 'call_123', round 1)
INFO: MCP call completed: 'deepwiki.ask_wiki_question' (call 'call_123', 1.25s)
```

Arguments, result bodies, bearer tokens, and raw transport exception details are excluded from call logs, including at debug verbosity. Logs start on their own line if they interrupt streamed answer text, without inserting logging content into the Markdown response. Existing `-q`/`-v` verbosity controls apply; `-q` suppresses successful-call progress while retaining warnings and errors. Reading recorded history and automatic-title requests do not log tool execution because they do not execute tools.

## File Operations and Recovery

Automatic title renames move the Markdown and sidecar together, checking collisions for both. The public Python helpers `textllm.copy_conversation(source, destination)` and `textllm.move_conversation(source, destination)` preserve the pair and reject existing destinations. Use these helpers only while the conversation is idle. To keep a temporary conversation, copy both files; ordinary shell `cp` on the Markdown alone does not include its sidecar.

Manual moves, renames, and backups must retain the naming convention: renaming `old.md` to `new.md` requires renaming `old.md.db` to `new.md.db`. Related images/includes/attachments still need separate handling. Do not run multiple writers against the same conversation. Paired file operations and Markdown/database writes are not jointly atomic across crashes.

Textllm saves call intent before execution, commits sidecar records before publishing references, and updates results promptly. A failed/interrupted invocation preserves streamed text and recorded progress and leaves a fresh `User` block, but can leave an incomplete exchange. An empty or reasoning-only final response after tools is reported as an error, rather than treating an intermediate preamble as the answer. When a provider supplies answer text only in its completed response payload, textllm recovers it without duplicating text already streamed. Inspect its database state and the external system before deciding how to proceed. Textllm refuses to replay incomplete exchanges and never guesses that a write is safe to retry. Explicitly removing its reference excludes the exchange from future context; that does not undo external effects. Database failures stop the invocation without claiming that an external action was rolled back.

JSONLiteDB's JSONL export facilities can be used to inspect or back up records as text. A database can contain sensitive tool arguments, results, and reasoning even when that content is absent from Markdown. Removing a reference is exclusion from replay, not deletion from storage.

## Security and Scope

This is a local CLI. Configured stdio commands run with the invoking user's privileges and environment. MCP outputs and tool descriptions are untrusted data. No additional command-trust or execution-approval flow is introduced. Configure servers appropriate for the permissions you intend to expose to the model.

A future hosted solution must revisit command execution, credentials, and user isolation and would likely disable stdio. OAuth, standalone resource/prompt workflows, server-requested model sampling/elicitation, other result media, and other tool executors remain outside this initial implementation.
