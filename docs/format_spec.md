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
model = "openai/gpt-5.5"
temperature = 1.0
```

Optional human-readable notes may appear here.

--- System ---

System instructions.

--- User ---

User message.

--- Assistant ---

Assistant response.
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
model = "openai/gpt-5.5"
temperature = 1.0
max_tokens = 2000
```
````

The first fenced code block in the preamble whose info string is empty or `toml` is the settings block. Producers should use `toml` for clarity. The block must contain valid TOML.

The `model` setting selects the LiteLLM model. Other settings are passed through to LiteLLM as call settings. The format does not define or validate provider-specific keys.

Settings are expected to be flat enough to pass directly to LiteLLM, but TOML itself may express nested data if a provider setting needs it and LiteLLM accepts the resulting value. Avoid using settings keys for textllm-owned behavior unless they are documented by textllm.

If no settings block is present, textllm uses its configured defaults.

## Role Blocks

Conversation messages are written as role blocks. A role marker is a line containing one of these markers:

```text
--- System ---
--- Developer ---
--- User ---
--- Assistant ---
```

Markers are case-insensitive when read. Producers should write the canonical capitalization above.

The message content is all text after a role marker up to the next recognized role marker or the end of the file. Leading and trailing blank space around a message block is not significant.

The supported roles map to model roles as follows:

| Marker | Role |
| --- | --- |
| `--- System ---` | `system` |
| `--- Developer ---` | `developer` |
| `--- User ---` | `user` |
| `--- Assistant ---` | `assistant` |

Adjacent blocks with the same role are equivalent to one message with the block contents joined by a blank line. Producers should usually emit one block per role turn, but readers should tolerate adjacent same-role blocks.

## Message Content

Message content is Markdown text. textllm does not parse general Markdown structure for model text; it preserves the block content as text except for recognized image lines in user messages.

Code fences, lists, headings, tables, links, and ordinary Markdown image syntax are valid message text. A line that would otherwise be a role marker can be escaped by prefixing it with a backslash:

```text
\--- User ---
```

Readers should treat that as literal message text:

```text
--- User ---
```

Producers should escape literal role marker lines inside message content so they are not confused with message boundaries.

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

Assistant messages are persisted as text. The current format does not define assistant-generated image output, tool calls, citations, or hidden reasoning data.

## Appending Responses

A normal model call expects the conversation to end with a `User` block. textllm appends the assistant response as an `Assistant` block and then writes a new empty `User` block for the next turn.

Tools may allow calls where the file does not end with a user message, but that is operational behavior rather than a different file format.

## Compatibility Rules

Readers should:

- Ignore empty message blocks.
- Accept role marker capitalization differences.
- Accept files with no preamble.
- Accept files with no settings block when defaults are available.
- Preserve unrecognized Markdown in preamble and message content.

Producers should:

- Use a Markdown title on the first line when a title is wanted.
- Use a fenced `toml` block for settings.
- Use canonical role marker capitalization.
- Escape literal role marker lines inside messages.
- Keep image references on their own line when they should be sent as image input.

## Extension Guidance

Future extensions should keep the file readable as Markdown and avoid changing the meaning of existing role blocks. New model capabilities should prefer explicit syntax or clearly documented settings over inference from ordinary prose.

When adding textllm-owned metadata, choose names that are unlikely to collide with LiteLLM provider settings and document whether they belong in TOML settings, preamble text, or message content.
