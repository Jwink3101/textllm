# Web Interface Design

## Status

This document is a product and architecture proposal for a local web interface to textllm. The interface will be developed and distributed as a separate package from textllm. `textllm-web` is used as a provisional name throughout this document; the final project and package names remain to be chosen.

The central design principle is:

> The Markdown file is the conversation. The interface is a view and editor for it, not a separate conversation store.

## Goals

The project should provide a familiar chat interface while preserving textllm's file-based workflow. A user should be able to move freely between the interface, the textllm CLI, and an ordinary text editor without conversion, import, or synchronization steps.

The application should:

- Run as a local web application in an ordinary browser.
- Run in a desktop window using PyWebView or a comparable webview shell.
- Use exactly the same application server and frontend in browser and desktop modes.
- Treat textllm Markdown files as the authoritative conversation state.
- Stream model output to both the interface and the conversation file as it arrives.
- Discover, sort, filter, and search conversations stored in ordinary directories.
- Render textllm roles, Markdown, images, attachments, includes, reasoning summaries, citations, and usage notes appropriately.
- Detect external edits and avoid silently overwriting them.
- Keep textllm usable as a simple CLI package without introducing web or desktop dependencies.

## Non-goals

The first release will not provide:

- Cloud synchronization.
- Multi-user or collaborative editing.
- Remote network serving.
- Conversation storage in a database.
- Branch graphs within a conversation.
- Mobile applications.
- Automatic Git integration.
- Client-executed function tools.
- Multiple application windows.
- Permanent file deletion.

These omissions constrain the security model and keep the initial product centered on local, durable Markdown files.

## Package Boundary

The interface will live in a separate repository and Python distribution. It will depend on textllm, while textllm will not import or depend on it.

textllm remains responsible for:

- Parsing the conversation format.
- Interpreting settings.
- Producing model-facing messages.
- Invoking providers through any-llm.
- Normalizing streamed model events.
- Formatting role blocks.
- Handling reasoning summaries, citations, and token usage.
- Generating titles and filenames.
- Preserving conversation-format invariants during streamed writes.

The web package is responsible for:

- Discovering and organizing conversation files.
- Serving the local HTTP and WebSocket application.
- Rendering the browser interface.
- Managing user drafts and active-generation state.
- Watching files and detecting conflicts.
- Managing application preferences.
- Serving authorized local images and attachments.
- Starting and stopping the PyWebView shell.
- Packaging compiled frontend assets.

The web package must not copy or independently reimplement the textllm parser. Format behavior implemented twice would eventually diverge between the CLI and interface.

## Architecture

The system has four layers:

```text
Browser or PyWebView
        |
        | HTTP and WebSocket
        v
Local application server
        |
        | Public Python API
        v
textllm
        |
        v
Markdown conversation files
```

PyWebView is a desktop shell rather than the application server or primary Python-to-JavaScript transport. The desktop launcher starts the same local server used by browser mode and then points a native webview window at it.

This design provides:

- One backend and frontend for browser and desktop use.
- Standard HTTP and WebSocket testing.
- Browser developer tools during development.
- A clean option to replace or supplement PyWebView later.
- Less coupling between frontend components and a particular desktop shell.

PyWebView supports both local-server and JavaScript-bridge application architectures. Its GUI loop is blocking and runs on the main thread, so the application server must run in a background thread or process when desktop mode is active. See the PyWebView documentation on [application architecture](https://pywebview.flowrl.com/guide/architecture) and [usage](https://pywebview.flowrl.com/guide/usage).

## Commands

The package should initially expose two commands:

```text
textllm-web serve [DIRECTORY]
textllm-web app [DIRECTORY]
```

`serve` starts the application on a loopback address and opens an ordinary browser unless instructed otherwise. `app` starts the same server on a random loopback port and opens it in PyWebView.

Both commands should accept a conversation root explicitly and fall back to a saved preference when appropriate. Remote binding should not be supported initially.

## Required textllm API

The current textllm implementation already provides conversation parsing, a normalized model event iterator, streamed file writing, and deterministic test behavior. The existing `Conversation.chat()` method, however, combines model execution, stdout output, file mutation, formatting, and finalization. A separate interactive application needs those capabilities through a smaller and more composable public API.

The web project should depend only on documented textllm APIs. Any necessary textllm changes should be made upstream rather than implemented through private classes or copied logic.

### Conversation Snapshot

textllm should expose a structured, read-only snapshot containing:

- The conversation path.
- The first-line title.
- Parsed settings.
- Parsed role blocks.
- Model-facing messages.
- The effective header level.
- Whether the trailing user block contains a draft.
- File revision information.
- Referenced images, attachments, and includes.
- Original text or block source ranges when required for document editing.

The snapshot must preserve unknown provider settings and content that the interface does not understand.

### Turn Execution

textllm should expose a turn operation that accepts a conversation, prompt, expected file revision, and local overrides, then yields typed events while maintaining the file.

The operation must:

- Validate that the caller's file revision is current.
- Persist the user prompt.
- Create an assistant block upon the first response content.
- Yield normalized model and lifecycle events.
- Flush response chunks to disk as they arrive.
- Write reasoning summaries, sources, and token notes according to the conversation settings.
- Append the next user marker on successful completion.
- Report title and filename changes.
- Distinguish completed, stopped, partial, and failed turns.
- Avoid printing streamed content to stdout unless explicitly requested.

The web package should not reconstruct this workflow from `iter_model_events()` because textllm owns the rules governing valid conversation files.

### Cancellation

Cancellation must be part of the public turn contract. A cancellation signal should be checked between streamed events and should close the provider stream when supported.

Cancellation should:

- Preserve all content already written.
- Leave the Markdown file syntactically usable.
- Mark the operation as partial in the returned state or event stream.
- Apply an explicit policy for appending the next user block.
- Never discard a partial response merely to restore a pre-generation state.

### Revision Checking

Every file mutation should accept an expected revision. A revision can incorporate:

- File identity.
- Modification time.
- Size.
- A content hash.

The hash is important because timestamps alone are not sufficient for reliable conflict detection. A stale client must receive a conflict response rather than overwrite newer content.

## Local Application Server

An ASGI application is a good fit for the backend. It can serve compiled frontend assets, expose a small REST API, and deliver generation and filesystem events over WebSockets.

FastAPI or a similarly lightweight Starlette-based application would provide ordinary request validation and WebSocket support. The design does not require a specific framework, but the selected framework must support graceful startup and shutdown in both command-line and PyWebView modes.

### Conversation Catalog

The catalog scans one configured root directory recursively and maintains lightweight records for recognized Markdown conversation files.

Each catalog record should include:

- An opaque runtime conversation identifier.
- A path relative to the configured root.
- The document title.
- The filename and containing directory.
- Modification time.
- Configured model and backend.
- Draft status.
- Generation or interruption status.
- A short search excerpt when inexpensive to produce.

Initial virtual views should include:

- All conversations.
- Recent conversations.
- Drafts.
- Active or interrupted conversations.
- Favorites.
- An archive directory.
- Search results.

Directories remain the durable organization mechanism. Favorites, the last-opened conversation, display preferences, and similar interface state may live in a separate application preferences file.

The initial catalog can scan at startup and update through a filesystem watcher. It does not require a persistent database. A later disposable cache is acceptable if deleting it loses no conversation data.

### Session Manager

The session manager owns active operations and coordinates the model stream, file writes, and connected clients.

A conversation can be in one of these application states:

```text
idle
dirty
generating
cancelling
partial
conflicted
failed
```

The first release should allow only one generation per conversation. Limiting the entire application to one active generation is an acceptable MVP simplification. The session manager should retain enough recent event state for a browser refresh to reconnect to an active turn without corrupting the display.

### File Watcher

The file watcher should:

- Notice files created, changed, renamed, and removed.
- Debounce duplicate filesystem notifications.
- Recognize writes made by the application itself.
- Re-index externally edited files.
- Detect conflicts with unsaved browser drafts.
- Track title-based renames initiated by textllm.
- Avoid following directory symlinks by default.
- Handle filenames containing spaces, symbols, and other valid characters.

External changes while idle can normally be reloaded. External changes while the browser has unsaved edits must produce a conflict. External changes during generation should stop or isolate the generation rather than merge silently.

### Local Asset Service

Markdown can reference local images and attachments, but the browser must not receive an unrestricted filesystem API.

The backend should expose authorized assets through opaque URLs, for example:

```text
/api/assets/{opaque-id}
```

An asset identifier must map to a path discovered through a parsed conversation reference. The service must reject arbitrary paths, parent traversal, and symlink escape.

Downloads and previews should use restrictive content types and appropriate `Content-Disposition` headers. Active content such as HTML and SVG requires special handling and should not execute in the application's origin.

## Application API

The precise route names may change, but the server needs operations equivalent to:

```text
GET    /api/app
GET    /api/conversations
POST   /api/conversations
GET    /api/conversations/{id}
PUT    /api/conversations/{id}/document
POST   /api/conversations/{id}/turns
POST   /api/conversations/{id}/cancel
POST   /api/conversations/{id}/rename
POST   /api/conversations/{id}/duplicate
POST   /api/conversations/{id}/reveal
GET    /api/assets/{id}
GET    /api/preferences
PUT    /api/preferences
```

Permanent deletion is not part of the MVP. A later delete operation should move files to the platform trash rather than unlink them directly.

### WebSocket Events

One authenticated WebSocket connection can carry:

- Assistant text deltas.
- Reasoning deltas.
- Citation events.
- Tool status.
- Usage information.
- Turn state changes.
- File and catalog changes.
- Conflicts.
- Title and filename changes.
- Backend errors.

Each event should contain fields equivalent to:

```text
event_id
conversation_id
turn_id
kind
sequence
payload
```

Sequence numbers allow the client to detect missing, duplicated, or out-of-order events after reconnecting. FastAPI documents direct support for text, binary, and JSON WebSocket messages in its [WebSocket guide](https://fastapi.tiangolo.com/advanced/websockets/).

## Frontend

The frontend should be written in TypeScript and compiled into static assets included in the Python wheel. A small component framework is appropriate, but Node.js must remain a development and build dependency rather than a runtime requirement.

The primary layout is:

```text
+------------------------+---------------------------------------------+
| Conversations          | Chat | Document | Settings                  |
|                        |                                             |
| New conversation       | Rendered conversation                       |
| Search                 |                                             |
| Recent                 | Streaming assistant response                |
| Drafts                 |                                             |
| Directories            |                                             |
|                        +---------------------------------------------+
| Conversation list      | Prompt composer                       Send  |
+------------------------+---------------------------------------------+
```

### Chat View

The chat view should render:

- System and developer blocks.
- User and assistant blocks.
- Collapsible local notes.
- Collapsible reasoning summaries.
- Markdown and code blocks.
- Images.
- Attachment and include indicators.
- Sources and citations.
- Token usage.
- Tool status.
- Partial-response status.

All rendered Markdown must be sanitized. Model output and conversation files are untrusted HTML inputs even when they reside locally.

### Composer

The composer should provide:

- Multiline editing.
- A send keyboard shortcut.
- A stop button during generation.
- Visible model and generation status.
- File-picker and drag-and-drop support for attachment insertion.
- Image insertion using relative Markdown paths where practical.
- Recoverable draft persistence.
- A clear indication of which conversation owns the draft.

The preferred draft model is to update the trailing user block after a short debounce. That preserves the file-first promise and makes drafts visible to the CLI and external editors. This behavior must use revision checks and stop while generation owns the file.

### Document View

The MVP document view can be read-only, with syntax highlighting, an external-editor action, and an indication when the file changes.

A later editable document view should use a capable browser text editor and optimistic concurrency:

```text
load revision A
edit
save with expected revision A
accept as revision B or report a conflict
```

The initial editor should not attempt collaborative character-level merging. A conversation being actively generated should be read-only in Document view.

### Settings View

The settings view should combine:

- Friendly controls for common settings such as model, temperature, backend, token reporting, and reasoning display.
- A raw TOML editor for all advanced and provider-specific settings.

Unknown settings must survive a round trip. The friendly form must not serialize settings through a reduced allowlist and discard provider options it does not recognize.

## Local Security

A service bound to localhost still requires authentication. A hostile page open in the user's browser may attempt to call local services.

The application must:

- Bind only to loopback addresses.
- Select a random port by default.
- Generate a strong session secret at startup.
- Authenticate every HTTP mutation and WebSocket connection.
- Validate `Origin` and `Host`.
- Apply CSRF protection to state-changing requests.
- Set a restrictive Content Security Policy.
- Sanitize rendered Markdown.
- Keep environment variables and API keys out of frontend state and logs.
- Prevent unrestricted path reads, parent traversal, and symlink escape.
- Avoid logging conversation contents by default.
- Open external links outside the application webview.
- Prevent the embedded window from navigating to arbitrary remote pages.

PyWebView provides a session-unique token that can protect local API calls, and its documentation warns that local REST APIs require CSRF protection. The application should nevertheless own an authentication scheme that behaves consistently in both PyWebView and ordinary-browser modes. See the PyWebView [security guidance](https://pywebview.flowrl.com/guide/security).

## PyWebView Shell

The desktop launcher should remain thin:

1. Select an available random loopback port.
2. Generate the application session secret.
3. Start the local application server in a background thread or process.
4. Wait for the server health check.
5. Create a PyWebView window pointed at the authenticated local URL.
6. Provide native file and directory pickers where useful.
7. Shut down the server after the final window closes.

PyWebView should be an optional dependency so browser-only installations do not acquire native GUI requirements:

```text
textllm-web
textllm-web[desktop]
```

Linux installations may require an explicit GTK or Qt PyWebView extra, so platform-specific installation instructions will be necessary. See the PyWebView [installation documentation](https://pywebview.flowrl.com/guide/installation).

## Proposed Repository Layout

```text
textllm-web/
|-- pyproject.toml
|-- README.md
|-- changelog.md
|-- license
|-- src/
|   `-- textllm_web/
|       |-- __init__.py
|       |-- cli.py
|       |-- app.py
|       |-- server.py
|       |-- security.py
|       |-- catalog.py
|       |-- sessions.py
|       |-- watcher.py
|       |-- assets.py
|       |-- preferences.py
|       |-- textllm_adapter.py
|       |-- desktop.py
|       `-- static/
|           `-- compiled frontend
|-- frontend/
|   |-- package.json
|   |-- src/
|   `-- tests/
|-- tests/
|   |-- test_catalog.py
|   |-- test_sessions.py
|   |-- test_api.py
|   |-- test_streaming.py
|   |-- test_conflicts.py
|   |-- test_security.py
|   `-- test_desktop.py
`-- docs/
    |-- architecture.md
    |-- conversation_lifecycle.md
    |-- security.md
    `-- packaging.md
```

## Testing Strategy

The web package should use textllm's deterministic test mode for backend and end-to-end tests. Automated tests must not require provider credentials or spend money.

Coverage should include:

- Catalog discovery, sorting, filtering, and search.
- Filenames containing spaces and symbols.
- Conversation parsing through the public textllm API.
- New conversation creation.
- Debounced draft persistence.
- Exact streamed event order.
- Interleaved reasoning and assistant blocks.
- Sources and usage notes.
- Cancellation.
- Provider failure after partial output.
- Browser reconnection during generation.
- External edits while idle.
- External edits during generation.
- Title-driven renaming.
- Optimistic-concurrency conflicts.
- Parent traversal and symlink escape.
- Markdown cross-site scripting.
- Dangerous attachment types.
- CSRF, host, origin, and WebSocket authentication.
- Missing or invalid provider configuration.

Frontend end-to-end tests should run against the real local server with deterministic model behavior. Desktop-shell smoke testing will need per-platform CI or a documented release checklist.

## Packaging and Distribution

The initial distribution should be a normal Python package whose wheel contains the compiled frontend:

```text
pip install textllm-web[desktop]
textllm-web app ~/Conversations
```

Standalone desktop bundles should follow only after the Python package is stable. PyWebView documents py2app for macOS and PyInstaller for Windows and Linux, which implies separate platform builds and release testing. See the PyWebView documentation on [freezing applications](https://pywebview.flowrl.com/guide/freezing).

Possible later artifacts include:

- A PyPI package.
- A signed and notarized macOS application.
- A signed Windows installer.
- A Linux package or AppImage.

Application bundling introduces platform renderers, signing, notarization, installer behavior, and operating-system CI. It should not delay an initial PyPI release.

## Delivery Plan

### Phase 1: Contract and Prototype

- Specify the public textllm snapshot, turn, cancellation, and revision APIs.
- Implement the smallest necessary upstream textllm changes.
- Create the separate web package and frontend build.
- Scan one conversation root.
- Render a conversation in the browser.
- Submit a prompt and stream the answer.
- Flush that answer to the Markdown file.
- Launch the same interface in PyWebView.

The prototype may omit external editing and rich settings, but it must prove that the browser and file observe the same streamed turn.

### Phase 2: MVP

- Conversation catalog views and sorting.
- New conversation creation.
- Search and directory browsing.
- Draft persistence.
- Cancellation and partial-response handling.
- File watching and conflict detection.
- External-editor action.
- Read-only Document view.
- Common settings controls and raw TOML access.
- Images, attachments, includes, citations, notes, reasoning, and usage rendering.
- Local authentication and security controls.
- Bundled frontend assets in the Python wheel.

### Phase 3: Desktop Release

- Editable Document view.
- Conflict-resolution interface.
- Duplicate or fork operation.
- Favorites and archive behavior.
- Native file and directory pickers.
- Keyboard customization and accessibility review.
- Cross-platform desktop testing.
- Application icons, installers, signing, and notarization.
- Crash recovery and user-facing diagnostics.

## Effort and Risk

For one developer familiar with textllm, a rough estimate is:

- Public textllm API contract and upstream work: about one week.
- Browser and PyWebView proof of concept: about one week.
- Useful MVP: another four to eight weeks.
- Polished cross-platform desktop release: another one to three months.

The main technical risks are:

- Cancellation across different providers.
- External edits during streamed writes.
- Securely exposing referenced local assets.
- Maintaining file-format behavior in one place.
- Packaging and testing native webviews across operating systems.
- Recovering cleanly from application or provider failure after partial output.

Chat rendering itself is comparatively conventional. The first implementation work should therefore establish the textllm integration contract and conversation state machine before investing heavily in visual polish.

## Initial Decisions

The following decisions are recommended for the first implementation:

- Keep the project in a separate repository and Python distribution.
- Use textllm as a normal runtime dependency.
- Add a small public interactive-session API to textllm rather than relying on private helpers.
- Use a local ASGI server for both browser and PyWebView modes.
- Use REST for discrete operations and one WebSocket for streamed events.
- Store conversations only as Markdown files.
- Use directories as durable organization and a disposable in-memory catalog for virtual views.
- Ship a read-only Document view in the MVP and defer built-in full-document editing.
- Bind only to loopback and authenticate every session.
- Distribute through PyPI before producing frozen desktop applications.

