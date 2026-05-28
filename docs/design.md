# textllm Design

textllm is a small command-line tool for working with LLM conversations as ordinary Markdown files. Its central design choice is that the file is the user interface and the persistent state. The CLI reads the file, converts it into a model request, streams a response, and appends the result back to the same file.

The tool is intentionally narrow. It is not a chat server, workspace manager, notebook, database-backed client, or provider abstraction layer of its own. LiteLLM handles provider integration; textllm handles the text-file workflow around it.

## Goals

- Make LLM conversations easy to edit, review, diff, copy, archive, and version as text.
- Keep the on-disk format readable Markdown with minimal structural syntax.
- Avoid provider-specific code unless it is required for the file workflow.
- Stream responses to stdout while preserving the final response in the conversation file.
- Let users choose models and provider options through LiteLLM-compatible settings.
- Keep tests deterministic and free of provider credentials or live model cost.

## Non-Goals

- Manage multiple conversations in an application database.
- Provide a graphical chat interface.
- Normalize every provider feature into a textllm-specific settings schema.
- Store hidden conversation state outside the Markdown file.
- Preserve rich assistant output that cannot currently be represented as text.
- Implement model routing, retries, pricing policy, or account management beyond what LiteLLM provides.

## Primary User Flow

The normal command flow is:

1. Resolve the conversation path.
2. Create a new conversation file from a template if the path does not exist.
3. Optionally append prompt text from `--prompt` and `--stdin`.
4. Optionally open the file in the user's editor.
5. Parse the Markdown file into settings and messages.
6. Optionally replace `!!AUTO TITLE!!` on the first line with a generated title.
7. Call the configured model through LiteLLM's streaming completion API.
8. Print streamed response chunks to stdout.
9. Append the collected assistant text and a fresh `--- User ---` block to the file.
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

Settings are loaded from the conversation's TOML block and merged over the default template settings. `model` is consumed by textllm to select the LiteLLM model. Other settings are passed to LiteLLM.

This keeps textllm small and avoids maintaining a provider setting allowlist. It also means invalid or unsupported provider settings usually fail at the LiteLLM or provider layer.

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

Supported roles are `system`, `developer`, `user`, and `assistant`. Adjacent messages with the same role are merged with a blank line between their text. This preserves the practical behavior from earlier versions while keeping the model request simple.

When user messages contain standalone Markdown image lines, those lines are converted into multimodal content blocks. Local image paths are resolved relative to the conversation file and encoded as data URLs. HTTP(S) URLs and existing `data:` URLs are passed through.

Assistant output is currently stored as text only.

### Model Boundary

`iter_completion_text()` is the boundary between textllm and LiteLLM. In production, it calls `litellm.completion()` with `stream=True`, the selected model, the parsed messages, and the pass-through settings.

The rest of the code consumes a simple iterator of response text and optional usage metadata. This boundary keeps streaming collection, file writing, tests, and future backend changes isolated from provider-specific response objects.

In deterministic test mode, the same boundary yields fake stream chunks instead of calling LiteLLM. Tests still exercise file creation, parsing, image conversion, message merging, streaming collection, title logic, and CLI subprocess behavior.

## File Update Behavior

textllm appends model output rather than rewriting the whole conversation during normal chat. Before writing the assistant block, it escapes any generated line that looks like a role marker so model output cannot accidentally create new conversation boundaries.

The append format is:

```text
--- Assistant ---

assistant response

--- User ---
```

This leaves the file ready for the next turn.

Title replacement is a separate write because it edits the first line when `!!AUTO TITLE!!` appears there. Rename is also separate and only runs when configured to do so.

## Titles and Renaming

Auto-title generation is deliberately simple. If the first line contains `!!AUTO TITLE!!`, textllm asks the configured model for a concise title based on the parsed conversation. The marker is replaced in place.

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

Richer outputs, tool calls, citations, generated images, and Responses-style APIs should be designed as explicit extensions. They should not be inferred from ordinary assistant text.
