# textllm Design

textllm is a small command-line tool for working with LLM conversations as ordinary Markdown files. Its central design choice is that the file is the user interface and the persistent state. The CLI reads the file, converts it into a model request, streams a response, and appends the result back to the same file.

The tool is intentionally narrow. It is not a chat server, workspace manager, notebook, database-backed client, or provider abstraction layer of its own. any-llm handles provider integration; textllm handles the text-file workflow around it.

## Goals

- Make LLM conversations easy to edit, review, diff, copy, archive, and version as text.
- Keep the on-disk format readable Markdown with minimal structural syntax.
- Avoid provider-specific code unless it is required for the file workflow.
- Stream responses to stdout while preserving the final response in the conversation file.
- Let users choose models and provider options through any-llm-compatible settings.
- Keep tests deterministic and free of provider credentials or live model cost.

## Non-Goals

- Manage multiple conversations in an application database.
- Provide a graphical chat interface.
- Normalize every provider feature into a textllm-specific settings schema.
- Store hidden conversation state outside the Markdown file.
- Preserve arbitrary rich assistant output that cannot be represented as readable Markdown.
- Implement model routing, retries, pricing policy, or account management beyond what any-llm provides.

## Primary User Flow

The normal command flow is:

1. Resolve the conversation path.
2. Create a new conversation file from a template if the path does not exist.
3. Optionally append prompt text from `--prompt` and `--stdin`.
4. Optionally open the file in the user's editor.
5. Parse the Markdown file into settings and messages.
6. Optionally replace `!!AUTO TITLE!!` on the first line with a generated title.
7. Call the configured model through any-llm's streaming completion, Responses, or Messages API.
8. Print streamed answer text to stdout and write it into the conversation file.
9. Append a fresh `--- User ---` block to the file.
10. Optionally rename the file from the title.

Creating a new file without edit or prompt input stops after writing the template. This gives users a chance to edit the system prompt and first user message before making a model call.

## Core Concepts

### Conversation File

The conversation file is the durable source of truth. It contains optional preamble content, optional TOML settings, and Markdown role blocks. The file format is described separately in [format_spec.md](format_spec.md).

textllm should avoid keeping meaningful state anywhere else. Environment variables and command-line flags affect the current invocation, but the conversation history itself lives in the file.

### Template

The default template creates a Markdown title line, a TOML settings block, a timestamp note, a system block, and an empty user block. Custom templates can be provided with `$TEXTLLM_TEMPLATE_FILE`.

Template rendering is intentionally limited. Only documented placeholders are expanded, so ordinary braces in prompts, JSON examples, and Markdown content remain safe.

### Settings

Settings are loaded from the conversation's TOML block and merged over the default template settings. `model` is consumed by textllm to select the any-llm provider and model. The reserved `[textllm]` table controls backend selection, reasoning-summary handling, and title-specific overrides. Other settings are passed to any-llm.

This keeps textllm small and avoids maintaining a provider setting allowlist. It also means invalid or unsupported provider settings usually fail at the any-llm or provider layer.

Environment variables supply defaults and provider credentials. Loading order is:

1. `$TEXTLLM_ENV_PATH`, when set.
2. A discovered `.env` file.
3. The file passed with `--env`.

Later loads override earlier values.

### Message Model

The parser converts role blocks into OpenAI-style message dictionaries:

```python
{"role": "user", "content": "..."}
```

Supported model roles are `system`, `developer`, `user`, and `assistant`. The file format also recognizes local-only `note` blocks. Notes remain in the Markdown and parsed file representation but are removed before normal model calls and automatic title generation. Adjacent messages with the same model role are merged with a blank line between their text. This preserves the practical behavior from earlier versions while keeping the model request simple.

When user messages contain standalone Markdown image lines, those lines are converted into multimodal content blocks. Local image paths are resolved relative to the conversation file and encoded as data URLs. HTTP(S) URLs and existing `data:` URLs are passed through. Standalone `[@attach optional description](target)` links represent generic file inputs. Their optional description remains text context and their target becomes a provider file block. `@attachment` is a compatibility alias. `[@include optional description](target)` reads a local UTF-8 text file and adds it after a labelled delimiter in the user text. Reference-link forms work for both. Because the Markdown history is replayed on each call, prior attachments and included text are sent again with later turns.

Assistant output is currently stored as text only.

### Model Boundary

`iter_model_events()` is the main boundary between textllm and any-llm. It selects the `completion`, `responses`, or `messages` adapter and yields a small set of provider-independent `ModelEvent` values for answer text, reasoning summaries, tool status, citations, usage, and completion.

The completion adapter retains `iter_completion_text()`, which calls `any_llm.completion()` with `stream=True`. This preserves the original and more portable backend. The Responses adapter calls `any_llm.responses()` with `stream=True`, converts image inputs to Responses content blocks, and normalizes its typed stream events. The Messages adapter calls `any_llm.messages()` with `stream=True`, hoists system and developer content into the separate system parameter, converts image inputs to Anthropic content blocks, and normalizes Messages stream events. OpenRouter-prefixed Messages calls select any-llm's Anthropic transport with OpenRouter's Anthropic-compatible base URL because any-llm 1.27.0's OpenRouter provider otherwise uses its generic Chat Completions bridge. Provider-specific response objects do not escape these adapters.

In deterministic test mode, the same boundaries yield fake chunks or events instead of calling any-llm. The Responses and Messages fakes can emit reasoning-summary, web-search status, citation, and answer events. Tests still exercise file creation, parsing, image conversion, message merging, streaming collection, title logic, and CLI subprocess behavior.

### Reasoning and Hosted Tools

The completion backend passes `reasoning_effort` through to any-llm. The Responses backend passes the native `reasoning` object and hosted-tool declarations through. The Messages backend passes Anthropic-style parameters such as `thinking`, `output_config`, and `tools`; it requires `max_tokens`, as the underlying interface does. Anthropic and OpenRouter handle this request through Messages endpoints, while other any-llm providers may bridge it through Chat Completions. Web search runs at the provider; textllm does not implement a search client or execute arbitrary local function tools.

Only answer deltas are written as assistant text while they arrive. URL citation annotations are collected, deduplicated, and appended as a Markdown `Sources` section after the answer. Provider-returned reasoning content may be omitted, logged, or persisted as a local-only `Note` block. Messages `thinking_delta` events use this same normalized path. Unreturned hidden reasoning is not accessible or stored, and Anthropic thinking signatures are not persisted.

The Markdown history remains the source of truth. Responses calls replay that history rather than relying on `previous_response_id` or provider-side conversation storage.

## File Update Behavior

textllm appends model output rather than rewriting the whole conversation during normal chat. It starts the assistant block when the model emits the first text chunk, then writes response chunks into that block as they arrive. This avoids leaving an empty assistant block when a provider fails before producing text, while still preserving partial assistant output after streaming has started.

The append format is:

```text
--- Assistant ---

assistant response

--- User ---
```

This leaves the file ready for the next turn.

Role markers such as `--- User ---` are reserved syntax at the start of a line. textllm keeps read-side support for backslash-escaped markers, but generated assistant output is written directly rather than auto-escaped.

Title replacement is a separate write because it edits the first line when `!!AUTO TITLE!!` appears there. Rename is also separate and only runs when configured to do so.

## Titles and Renaming

Auto-title generation is deliberately simple. If the first line contains `!!AUTO TITLE!!`, textllm asks the configured model for a concise title based on the parsed conversation. The marker is replaced in place. Title calls use the selected backend but remove tools, tool choice, and tool-call limits, and may apply provider overrides from `[textllm.title]`.

Renaming derives a filesystem-safe Markdown filename from the title. If the target path already exists, a numeric suffix is added. Renaming is default behavior for newly created default-path conversations, but not for explicitly named files unless requested.

## Error Handling

The CLI favors clear command failure over partial hidden recovery. Examples include:

- Requiring a final user message by default before chat.
- Failing when the model stream does not include text.
- Failing when edit mode leaves the file unchanged.
- Letting invalid TOML, missing image files, missing model settings, or provider errors surface as command errors.

Verbose mode re-raises errors for debugging. Normal mode logs the error and exits nonzero.

## Testing Design

The default test suite must not call live providers. `TEXTLLM_TEST_MODE=1` activates a deterministic fake model at the same boundary used for live streaming. This lets tests assert behavior that matters to textllm without depending on provider availability, account credentials, model behavior, network access, latency, or cost.

The testing strategy is documented in [testing_strategy.md](testing_strategy.md).

## Extension Principles

Future work should preserve the text-file workflow:

- Prefer explicit Markdown-compatible syntax for new persistent data.
- Keep ordinary conversation files readable without textllm.
- Keep provider-specific behavior behind small boundaries.
- Avoid adding dependencies unless they serve a concrete workflow need.
- Document format changes in [format_spec.md](format_spec.md) before relying on them.

Additional richer outputs, client-executed function tools, generated images, and provider state should be designed as explicit extensions. They should not be inferred from ordinary assistant text.
