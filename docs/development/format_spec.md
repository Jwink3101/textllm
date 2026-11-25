# textllm File Format Specification

This document describes the textllm conversation file format. The format is Markdown-first: a valid file should remain useful to read and edit as plain Markdown, while giving textllm enough structure to build a model request and append the model response.

This specification describes the intended stable format. Some current parser details are noted where they matter for compatibility, but producers should prefer the simple canonical forms shown here.

## Design Goals

- Keep conversations readable and editable in ordinary text editors.
- Use a small amount of visible structure instead of hidden metadata.
- Preserve provider flexibility by passing model settings through without maintaining a large textllm-owned schema.
- Allow Markdown content, including code blocks, without requiring special escaping in normal prose.
- Leave room for future extensions without changing the basic role block model.

## File Structure

A textllm file has three logical parts:

1. Optional preamble.
2. Optional settings block inside the preamble.
3. Conversation role blocks.

The canonical shape is:

````markdown
# Conversation Title

```toml
[llm]
model = "openai/gpt-6-luna"
temperature = 1.0
```

Optional human-readable notes may appear here.

--- System ---

System instructions.

--- User ---

User message.

--- Assistant ---

Assistant response.

--- Note ---

A local note that is not sent to the model.

--- Reasoning ---

A local reasoning summary that is not sent to the model.
````

The conversation begins at the first recognized role marker. Everything before that marker is preamble.

## Preamble

The preamble is optional. It may contain a title, settings, and human-readable notes.

The first line of the preamble is treated as the title. A leading Markdown heading marker is optional, so `# My Title` and `My Title` both represent `My Title`. If the first line contains `!!AUTO TITLE!!`, textllm may replace it with a generated title.

Text after the settings block and before the first role marker is kept as human-readable preamble content. It is not sent as a model message unless it is also inside a role block.

## Settings

Settings are written as TOML in a Markdown fenced code block in the preamble:

````markdown
```toml
[llm]
model = "openai/gpt-6-luna"
temperature = 1.0
max_tokens = 2000
```
````

The first fenced code block in the preamble whose info string is empty or `toml` is the settings block. Producers should use `toml` for clarity. The block must contain valid TOML.

The `model` setting selects the any-llm provider and model. Producers should write `provider/model`, which is the preferred textllm form. Readers also accept `provider:model` for compatibility. Provider settings under `[llm]` are unpacked and passed through to any-llm as call settings. The reserved `[textllm]` table remains local. Legacy root-level provider settings remain supported with one deprecation warning per invocation; explicit `[llm]` values win conflicts within a source.

The `[textllm]` table defines local behavior. Supported keys are:

- `backend`: `completion`, the default, `responses`, or `messages`.
- `header_level`: an integer from `0` through `6` controlling newly written role markers. `0` writes plain separators; levels `1` through `6` write ATX Markdown headings. The `--header-level` CLI option overrides this setting. When neither is present, `$TEXTLLM_DEFAULT_HEADER_LEVEL` supplies the default and falls back to `0`; it may be loaded from the process environment, `.env`, `$TEXTLLM_ENV_PATH`, or `--env`.
- `reasoning_summary`: `omit` (default) hides returned reasoning; `block` streams readable reasoning into local-only blocks; `stderr` writes the summary directly to stderr after a successful response, independently of logging verbosity. This setting controls display, not whether a tool exchange retains native reasoning.
- `mcp`: an array of server tables, each specifying exactly one `url` or `command`, with optional name, stdio environment, or HTTP `bearer_token` string (literal or `${NAME}` reference). All discovered tools are exposed.
- `tools`: a local table of finite tool-loop limits (`max_rounds`, `max_calls`, `timeout`, `max_result_chars`, and `max_context_chars`), separate from provider tool declarations under `[llm].tools`.
- `tokens`: `none`, the default, `stderr`, or `note`. Reports provider-returned usage for the latest successful conversational call to stderr or a local-only `Note` block. The `--tokens` CLI option overrides this setting. Earlier calls and automatic-title usage are excluded; failed or interrupted calls do not produce a report.
- `title`: a nested table of settings that override normal settings during automatic-title generation, including an optional separate `model`. The conversation's backend is retained. Overrides replace entire top-level values, including nested provider objects such as `reasoning`. Tools are removed from title calls even if specified here. See [Automatic Titles](../reasoning_and_tools.md#automatic-titles) for examples.

The format does not validate provider settings or guarantee that a provider implements the selected backend or feature.

The `messages` backend calls any-llm's Anthropic-style Messages interface. It requires a `max_tokens` setting. Before the call, textllm joins `System` and `Developer` role content into the separate Messages `system` parameter, retains `User` and `Assistant` entries in the message list, and converts image inputs to Anthropic base64 or URL image-source blocks. If a TOML `llm.system` value is present, it is placed before system-role content from the conversation. Native-only Messages settings require a provider endpoint that implements the Anthropic Messages API rather than any-llm's compatibility bridge.

For a model beginning with `openrouter/`, the Messages backend uses any-llm's Anthropic transport with OpenRouter's Anthropic-compatible base URL. `$OPENROUTER_API_KEY` supplies authentication unless an explicit `api_key` setting is present. An explicit `api_base` setting overrides the default `https://openrouter.ai/api`. The remaining model id, including its author namespace, is preserved.

Settings are expected to be flat enough to pass directly to any-llm, but TOML itself may express nested data if a provider setting needs it and any-llm accepts the resulting value. Avoid using settings keys for textllm-owned behavior unless they are documented by textllm.

If no settings block is present, textllm uses its configured defaults.

## Role Blocks

Conversation messages are written as role blocks. A role marker is a line containing one of these markers:

```text
--- System ---
--- Developer ---
--- User ---
--- Assistant ---
--- Note ---
--- Reasoning ---
--- Tools ---
```

Each marker may optionally be prefixed by an ATX heading marker and a space, from `# --- User ---` through `###### --- User ---`. Plain and heading-prefixed markers may be mixed in one file. Markers are case-insensitive when read. Producers should use canonical role capitalization and a heading level from `0` through `6`.

The message content is all text after a role marker up to the next recognized role marker or the end of the file. Leading and trailing blank space around a message block is not significant.

The supported roles map to model roles as follows:

| Marker | Role |
| --- | --- |
| `--- System ---` | `system` |
| `--- Developer ---` | `developer` |
| `--- User ---` | `user` |
| `--- Assistant ---` | `assistant` |
| `--- Note ---` | Not sent; local-only content |
| `--- Reasoning ---` | Local display text; never sent |
| `--- Tools ---` | Sidecar references expanded into native tool exchanges and associated reasoning |

`Note` and `Reasoning` blocks are retained in the parsed file representation but omitted from every model request. This includes normal chat calls and the conversation content used for automatic title generation. Local-only blocks may appear between any role blocks, and a trailing local-only block does not change which model message is considered last.

Adjacent blocks with the same role are equivalent to one message with the block contents joined by a blank line. Producers should usually emit one block per role turn, but readers should tolerate adjacent same-role blocks.

## Message Content

Message content is Markdown text. textllm does not parse general Markdown structure for model text; it preserves the block content as text except for recognized image lines in user messages.

Code fences, lists, headings, tables, links, and ordinary Markdown image syntax are valid message text. Plain and heading-prefixed role markers are reserved syntax when they appear at the start of a line. This is unlikely to matter in normal assistant responses, but a literal role marker line can be escaped by prefixing it with a backslash:

```text
\--- User ---
\## --- Assistant ---
```

Readers should treat that as literal message text:

```text
--- User ---
```

Readers should continue to support escaped role markers for compatibility and for users who need literal marker text.

## Images

User messages may include Markdown image references:

```markdown
![alt text](image.png)
![remote](https://example.com/image.png)
![embedded](data:image/png;base64,...)
```

A Markdown image is treated as model image input when it appears alone on a line outside a fenced code block. The image line is removed from the text portion of the message and converted into an image input block.

Supported image targets are:

- Relative or local paths, resolved relative to the conversation file.
- `http://` and `https://` URLs.
- `data:` URLs.

Image alt text and optional image titles are allowed for Markdown readability, but they are not semantic model input in the current format. If the alt text matters, include it as normal prose near the image.

Markdown image syntax inside fenced code blocks remains literal text. Inline images embedded in the middle of a prose line are also treated as text, not image input.

## Attachments

User messages can attach non-image files with a Markdown link whose link text begins with `@attach`. The directive can appear inline in prose or on its own line:

```markdown
[@attach optional description](report.pdf)
```

The optional description is retained as model text. The attachment link is removed from model text and converted into a file content block. Local targets are resolved relative to the conversation file and encoded as data URLs. HTTP(S) and `data:` targets are passed through. MIME types for local files are inferred from the filename, with `application/octet-stream` as a fallback.

Attachment and include targets may contain spaces and parentheses, for example `[@include](meeting notes (final).md)`.

Use a normal Markdown reference link to name a file once and attach it where needed:

```markdown
[@attach Q3 financial report][q3-report]

[q3-report]: docs/q3-report.pdf
```

The matching reference definition is metadata and is not sent as model text. An undefined attachment reference is an error. If a recognized attachment or include cannot be loaded, the model call fails before it is sent. `@attachment` is retained as an alias for `@attach`. Attachment links in non-user blocks and syntax inside fenced code blocks remain ordinary Markdown.

An attachment remains in the user message where it appears. Since textllm replays the Markdown conversation for each model call, attachments from prior user turns are sent again on later calls. The selected model backend determines whether it accepts a particular file type.

Local attachments are reread from disk on each invocation. The Markdown stores the reference, not a snapshot of the file contents. Changing the file changes the context sent for its earlier turn; removing the file causes loading to fail.

### Included Files

`@include` uses the same Markdown syntax but reads a local UTF-8 text file into the user message instead of creating a provider file input:

```markdown
[@include optional description](notes.md)
[@include optional description][notes]

[notes]: docs/notes.md
```

The description remains text context. textllm appends the included contents after a labelled delimiter that preserves the filename. URLs and `data:` URLs are not allowed for `@include`; non-UTF-8 files are rejected. Included text is replayed on later calls like any other message text, so keep included files reasonably sized.

As with local attachments, included files are reread on each invocation. Their expanded contents are not persisted in the conversation file, so later requests use the current file contents even for directives in earlier turns.

Assistant messages are persisted as text. Responses and Messages URL citations are appended to the assistant text as a conventional Markdown `### Sources` section. The sources are therefore ordinary assistant content on later turns. MCP calls/results and their associated reasoning/continuation data are persisted in the adjacent sidecar, as described in [MCP tools and sidecars](../mcp.md). Hosted search data is not generally replayed; native payloads retained for tool continuation may contain additional provider items. Assistant-generated images and unreturned hidden reasoning are not supported.

When `[textllm].reasoning_summary = "block"`, provider-returned reasoning-summary content is written as local-only `Reasoning` blocks in stream-event order. For the Messages backend, this includes text from Anthropic `thinking` blocks. A response may contain alternating `Reasoning` and `Assistant` blocks. Reasoning remains visible to the reader but is excluded from future model and automatic-title requests; assistant blocks that become adjacent after that filtering are merged with a blank line. This is distinct from hidden chain-of-thought that a provider does not return, which textllm cannot request or store.

## Appending Responses

A normal model call expects the conversation to end with a `User` block. textllm starts an `Assistant` block when the model emits an answer-text chunk and, when block-mode reasoning is enabled, starts a `Reasoning` block when the model emits a reasoning chunk. It opens a new block whenever the persisted event role changes, preserving event order, and writes a new empty `User` block when the chat attempt exits, including on model failure, empty output, or interruption. Partial event transcripts remain before that final marker. Validation failures before opening the conversation for a chat attempt do not mutate it.

Tools may allow calls where the file does not end with a user message, but that is operational behavior rather than a different file format.

## Compatibility Rules

Readers should:

- Ignore empty message blocks.
- Accept role marker capitalization differences.
- Accept plain role markers and role markers expressed as Markdown headings from level 1 through 6.
- Accept files with no preamble.
- Accept files with no settings block when defaults are available.
- Preserve unrecognized Markdown in preamble and message content.

Producers should:

- Use a Markdown title on the first line when a title is wanted.
- Use a fenced `toml` block for settings.
- Use canonical role marker capitalization.
- Keep image references on their own line when they should be sent as image input.

## Extension Guidance

Future extensions should keep the file readable as Markdown and avoid changing the meaning of existing role blocks. New model capabilities should prefer explicit syntax or clearly documented settings over inference from ordinary prose. Provider-hosted tools are configured explicitly in TOML; MCP tools use explicit local settings and sidecar references; other client-executed tool types remain future extensions.

When adding textllm-owned metadata, choose names that are unlikely to collide with any-llm provider settings and document whether they belong in TOML settings, preamble text, or message content.


## Environment References

Parsed TOML string values are resolved once against the loaded environment at execution, after source merging. `${NAME}` expands and `$${NAME}` escapes a literal reference. This applies throughout nested provider/local settings, not to keys, non-string types, comments, or conversation text. Template-only creation preserves unresolved references. Missing variables fail before execution mutates the conversation. See [configuration-wide environment references](../../readme.md#environment-references-in-toml).

## Sidecar References

Sidecars are reserved for MCP tool-call history and future client tool integrations. Reasoning alone never creates a sidecar, even with MCP configured. A sidecar is created only after the model requests a tool call. Reasoning returned during that tool exchange, including the final answer after execution, is retained for native replay in all display modes. Reasoning from a turn without tool calls remains display-only and is not replayed. This is the current storage policy, not a requirement of the sidecar format; revisit it if general provider-native state replay is added.

The conventional sidecar is the Markdown path plus `.db`, storing versioned JSONLiteDB records in SQLite `DELETE` journal mode with `wal_mode=False` and a direct `PRAGMA journal_mode=DELETE` on each open, including existing WAL databases. `Tools` blocks require standalone `[@sidecar optional description](uuid)` directives. The description is informational. Each key identifies a reasoning record or tool exchange; reasoning attached to a tool call must precede its exchange in the same block. Reasoning attached to the completed final answer after tool execution is a standalone reference whose record includes the rest of that native assistant response and the original reasoning positions. Consecutive directives share one block. When answer text and native reasoning arrive together in a completed Responses or Messages payload, the reference precedes the Assistant block; earlier streamed text remains in arrival order. A standalone final-answer reference can precede or follow its visible assistant text, and both restore one native assistant response. `Reasoning` blocks contain display text and are never expanded into native history. References resolve against this sidecar only; they are not model-facing text. Missing, malformed, duplicated, incompatible, or incomplete tool references fail explicitly. Unreferenced records are ignored.

`[textllm].tools_display` selects `"comment"` (default) or `"block"` for newly written Tools blocks. Comment mode places a standalone `<!--` line before the Tools marker and a standalone `-->` line after its last reference, retaining heading levels and grouped directives. The reader accepts either form regardless of the setting, including mixed files, and consumes only this reserved wrapper without adding its delimiters to neighboring messages. Unclosed comments, nested comments, and multiple role blocks inside a Tools comment fail explicitly. The writer closes comments before another role, token notes, and the trailing User marker on success, failure, or interruption. Existing files are not rewritten; older readers require visible blocks. Ordinary HTML comments keep their existing behavior.

Markdown owns reference order/inclusion and displayed assistant text. Native tool call/result grouping and continuation data come from the referenced record, with no duplicate assistant text. Removing a reference excludes its record; it does not execute or undo any operation. Displayed reasoning remains local-only. Native reasoning is restored from its explicit directive under `Tools`, either combined with the following tool exchange or restored as a completed assistant response. Its original positions and assistant phase are preserved, and it is sent once. Deleting that directive excludes the reasoning; deleting an exchange requires deleting its associated reasoning directive too. There is no implicit reasoning fallback or alternative reference syntax. Title requests use ordinary user/assistant text and exclude both reference roles.

See [MCP tools and sidecars](../mcp.md) for connection settings, limits, supported outputs, and paired file lifecycle rules.


### Sidecar Record Version 1

Each JSONLiteDB record has an `id` UUID string (UUIDv7 for new records), `version = 1`, and a `kind`. Unknown versions fail explicitly. The record kind determines replay behavior; the directive description does not.

| Kind | Stored data |
| --- | --- |
| `tools` | Backend/model, round sequence, native assistant payload without reasoning, calls with executor/server/tool identities, definitions, results, and execution status. Optional `reasoning_key` and `reasoning_positions` associate a separate reasoning record. |
| `reasoning` | Backend, readable `text` when returned, native reasoning payload, and completion status. Final reasoning after tool execution also includes `assistant_native` and `reasoning_positions` to restore that assistant response. |

Tool records progress through `pending`, `running`, and `complete`; uncertain execution is marked `unknown`. `running_call` identifies the active call until successful completion. Only complete exchanges with matching call/result IDs are replayable. Each result retains the original `result`, the model-facing `output`, `call_id`, and `is_error`; these are persisted before the next model request.

Reasoning records are `partial` while streaming after tool execution starts and `complete` when a response finishes. Before the first tool request, reasoning is buffered without creating a database. Completion native reasoning uses provider reasoning fields; Responses and Messages retain native item/block lists and their original positions. Sidecar records are committed before references are written; incomplete and unreferenced records can remain after interruption. Markdown and database writes are not jointly atomic.
