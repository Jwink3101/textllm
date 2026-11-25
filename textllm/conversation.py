"""Conversation lifecycle, model calls, and streamed Markdown updates."""

import base64
from contextlib import closing
import mimetypes
import os
import re
import sys
from functools import cached_property
from urllib.parse import urlsplit

from .backends import (
    LLMResponse,
    _format_token_usage,
    _merge_usage,
    _object_get,
    _sources_markdown,
    iter_model_events,
)
from .config import (
    AUTO_TITLE,
    CONFIG,
    TITLE_SYSTEM_PROMPT,
    _merge_settings,
    _resolve_settings,
    _normalize_settings,
    _has_legacy_settings,
    _LEGACY_SETTINGS_WARNING,
    _LOCAL_SETTINGS_TABLES,
    _test_mode_enabled,
    log,
)
from .history import Sidecar, references, replay_exchange, replay_reasoning
from .tools import mcp_config, tool_limits
from .tool_loop import iter_tool_events
from .output import StreamOutput
from .files import clean_filepath, title2filename, uniqueify_filepath, move_conversation
from .markdown import (
    LOCAL_ONLY_ROLES,
    ROLE_MARKER_RE,
    _role_marker,
    loads,
    merge_message_runs,
    process_msg_for_attachments,
    read_settings,
)


class NoUserMessageError(ValueError):
    """Error when a conversation does not end with a user message."""


NoHumanMessageError = NoUserMessageError


class Conversation:
    """Read, parse, update, and optionally rename a textllm conversation file.

    Parameters
    ----------
    filepath : str or path-like
        Path to a Markdown conversation file in textllm format.
    overrides : dict, optional
        Settings recursively merged for this invocation without changing the
        file's TOML. Arrays and non-dictionary values replace earlier values.
    """

    def __init__(self, filepath, *, overrides=None):
        self.filepath = self.filepath0 = filepath
        self._legacy_overrides = _has_legacy_settings(overrides or {})
        self._settings_overrides = _normalize_settings(overrides or {})

        # Read and strip trailing whitespace before parsing.
        with open(self.filepath, "rt") as fp:
            self.text = fp.read().rstrip()

        self.parsed = loads(self.text)
        self._settings_source = _merge_settings(
            {"model": CONFIG.TEXTLLM_DEFAULT_MODEL}
            | _normalize_settings(self.parsed["settings"]),
            self._settings_overrides,
        )
        self._resolved_settings = _resolve_settings(self._settings_source)
        local = self._resolved_settings.get("textllm", {})
        if local.get("tools_display", "comment") not in ("comment", "block"):
            raise ValueError("textllm.tools_display must be 'comment' or 'block'")
        self._local_settings = local
        self.mcp_servers = mcp_config(local.get("mcp", []), expand=False)
        self.tool_limits = tool_limits(local.get("tools", {}))
        self.messages = self.process_conversation()

    def call_llm(
        self,
        messages,
        *,
        print_stream=False,
        stream_writer=None,
        title_call=False,
        tokens=None,
        **new_settings,
    ):
        """Call the configured chat model.

        Parameters
        ----------
        messages : list
            OpenAI-style message dictionaries to send to the model. Local-only
            roles are ignored.
        print_stream : bool, optional
            Whether to print response chunks to stdout while collecting them.
        stream_writer : file-like, optional
            Writer that receives response chunks as they arrive. When
            ``reasoning_summary`` is ``block``, a writer-provided
            ``write_reasoning()`` method also receives reasoning-summary
            chunks in event order.
        title_call : bool, optional
            Disable tools and apply optional title-specific settings.
        tokens : {None, "none", "stderr", "note"}, optional
            Where to report provider-returned token usage. ``None`` uses the
            conversation's ``[textllm].tokens`` setting.
        **new_settings
            Settings that override the conversation settings for this call.

        Returns
        -------
        LLMResponse
            Normalized model response.
        """
        messages = [
            message
            for message in messages
            if message.get("role") not in LOCAL_ONLY_ROLES
        ]

        effective = self.settings  # Preserve one legacy warning per invocation.
        source = getattr(self, "_settings_source", effective)
        raw_settings = _merge_settings(source, new_settings)
        settings = (
            _resolve_settings(raw_settings)
            if new_settings
            else _merge_settings(effective, {})
        )
        local_settings = {
            name: settings.pop(name, {}) for name in _LOCAL_SETTINGS_TABLES
        }
        textllm_settings = local_settings["textllm"]
        backend = textllm_settings.get("backend", "completion")
        reasoning_summary_mode = textllm_settings.get("reasoning_summary", "omit")
        if reasoning_summary_mode not in ("omit", "block", "stderr"):
            raise ValueError(
                "textllm.reasoning_summary must be 'omit', 'block', or 'stderr'"
            )
        token_mode = (
            textllm_settings.get("tokens", "none") if tokens is None else tokens
        )
        if token_mode not in ("none", "stderr", "note"):
            raise ValueError("textllm.tokens must be 'none', 'stderr', or 'note'")
        if title_call:
            token_mode = "none"
        if title_call:
            settings.pop("tools", None)
            settings.pop("tool_choice", None)
            settings.pop("max_tool_calls", None)
            settings = _merge_settings(settings, textllm_settings.get("title", {}))
            settings.pop("tools", None)
            settings.pop("tool_choice", None)
            settings.pop("max_tool_calls", None)
        if backend == "completion" and token_mode != "none":
            stream_options = dict(settings.get("stream_options") or {})
            stream_options["include_usage"] = True
            settings["stream_options"] = stream_options
        # Log unresolved values, so interpolation cannot expose credentials
        # embedded in arbitrary provider settings. Local settings stay omitted.
        log_settings = {
            k: v for k, v in raw_settings.items() if k not in _LOCAL_SETTINGS_TABLES
        }
        if title_call:
            for key in ("tools", "tool_choice", "max_tool_calls"):
                log_settings.pop(key, None)
            log_settings = _merge_settings(
                log_settings, raw_settings.get("textllm", {}).get("title", {})
            )
            for key in ("tools", "tool_choice", "max_tool_calls"):
                log_settings.pop(key, None)
        log.debug("Settings %s", _settings_log_view(log_settings))

        model = settings.pop("model")  # Will KeyError if not set as expected
        log.debug("model = %r", log_settings.get("model"))

        with StreamOutput(print_stream) as output:
            content = []
            reasoning_summary = []
            citations = []
            usage_metadata = None
            last_tool_status = None
            if print_stream:
                output.write("\n")
            event_stream = (
                iter_model_events(
                    backend=backend, model=model, messages=messages, settings=settings
                )
                if title_call or stream_writer is None
                else iter_tool_events(
                    events=iter_model_events,
                    backend=backend,
                    model=model,
                    messages=messages,
                    settings=settings,
                    servers=mcp_config(textllm_settings.get("mcp", []), expand=False),
                    limits=tool_limits(textllm_settings.get("tools", {})),
                    filepath=self.filepath,
                    persist=hasattr(stream_writer, "write_reference"),
                )
            )
            with closing(event_stream):
                for event in event_stream:
                    if event.kind == "output_text_delta":
                        content.append(event.text)
                        if print_stream:
                            output.write(event.text)
                        if stream_writer:
                            stream_writer.write(event.text)
                            stream_writer.flush()
                    elif event.kind == "reasoning_summary_delta":
                        reasoning_summary.append(event.text)
                        if (
                            reasoning_summary_mode == "block"
                            and stream_writer
                            and hasattr(stream_writer, "write_reasoning")
                        ):
                            stream_writer.write_reasoning(event.text)
                            stream_writer.flush()
                    elif event.kind == "history_reference":
                        if hasattr(stream_writer, "write_reference"):
                            stream_writer.write_reference(
                                event.data["role"],
                                event.data["key"],
                                event.data.get("description"),
                            )
                            stream_writer.flush()
                    elif event.kind == "citation":
                        citations.append(event.data)
                    elif event.kind == "usage":
                        usage_metadata = _merge_usage(usage_metadata, event.data)
                    elif event.kind == "tool_status":
                        if event.data != last_tool_status:
                            log.info(f"Web search {event.data}")
                            last_tool_status = event.data
                    elif event.kind == "error":
                        error = _object_get(event.data, "error", event.data)
                        response = _object_get(event.data, "response")
                        if response is not None:
                            error = _object_get(response, "error") or _object_get(
                                response, "incomplete_details", error
                            )
                        message = _object_get(error, "message") or _object_get(
                            error, "reason", error
                        )
                        raise RuntimeError(f"Model response failed: {message}")

            response_text = "".join(content)
            if not response_text:
                raise ValueError("Model stream did not include text content")

            sources = _sources_markdown(citations)
            if sources:
                content.append(sources)
                if print_stream:
                    output.write(sources)
                if stream_writer:
                    stream_writer.write(sources)
                    stream_writer.flush()
            if print_stream:
                output.write("\n\n")

        response_text = "".join(content)

        response = LLMResponse(
            content=response_text,
            usage_metadata=usage_metadata,
            reasoning_summary=(
                "".join(reasoning_summary)
                if reasoning_summary_mode == "block" and reasoning_summary
                else None
            ),
            citations=citations,
        )

        if reasoning_summary_mode == "stderr" and reasoning_summary:
            print("Reasoning summary: " + "".join(reasoning_summary), file=sys.stderr)

        token_usage = _format_token_usage(response.usage_metadata)
        if token_mode == "stderr" and token_usage:
            print(token_usage, file=sys.stderr)

        return response

    def chat(self, require_user_prompt=True, tokens=None, header_level=None):
        """Append one assistant response to the conversation file.

        Parameters
        ----------
        require_user_prompt : bool, optional
            Require the current conversation to end with a user message.
        tokens : {None, "none", "stderr", "note"}, optional
            Override the conversation's token-reporting setting.
        header_level : int or None, optional
            Markdown heading level for newly written role markers. ``None``
            uses ``[textllm].header_level``, then
            ``$TEXTLLM_DEFAULT_HEADER_LEVEL``, then 0.

        Raises
        ------
        NoUserMessageError
            If `require_user_prompt` is true and the conversation does not end
            with a user message.
        """
        if require_user_prompt and (
            not self.messages or self.messages[-1]["role"] != "user"
        ):
            raise NoUserMessageError("Must have a new user message")

        if header_level is None:
            textllm_settings = self.settings.get("textllm", {})
            if "header_level" in textllm_settings:
                header_level = textllm_settings["header_level"]
            else:
                header_level = CONFIG.TEXTLLM_DEFAULT_HEADER_LEVEL
        # Validate before opening the file for mutation.
        _role_marker("User", header_level)

        with open(self.filepath, "r+") as file:
            self._seek_append_point(file)
            stream_writer = self._ResponseBlockWriter(
                file,
                header_level,
                tools_display=self.settings.get("textllm", {}).get(
                    "tools_display", "comment"
                ),
            )

            try:
                response = self.call_llm(
                    messages=self.messages,
                    print_stream=True,
                    stream_writer=stream_writer,
                    tokens=tokens,
                )

                token_mode = self.settings.get("textllm", {}).get("tokens", "none")
                if tokens is not None:
                    token_mode = tokens
                token_usage = _format_token_usage(response.usage_metadata)
                if token_mode == "note" and token_usage:
                    stream_writer.finish()
                    file.write(
                        f"\n\n{_role_marker('Note', header_level)}\n\n" + token_usage
                    )
            finally:
                stream_writer.finish()
                # A completed, failed, or interrupted invocation always leaves
                # an explicit place for the next user prompt.
                file.write(f"\n\n{_role_marker('User', header_level)}\n\n")
                file.flush()

        # Not really needed but in case I do more with it later
        with open(self.filepath, "rt") as file:
            self.text = file.read().rstrip()
        self.parsed = loads(self.text)
        self.messages = self.process_conversation()
        log.info(f"Updated {self.filepath!r}")

    class _ResponseBlockWriter:
        """Write answer and reasoning blocks in model-event order."""

        def __init__(self, file, header_level=0, *, tools_display="comment"):
            self.file = file
            self.header_level = header_level
            self.role = None
            self.at_line_start = True
            self.after_reference = False
            self.tools_display = tools_display
            self.comment_open = False

        def _write_role(self, role, text, *, escape_role_markers=False):
            if not text:
                return
            if self.role != role:
                self.finish()
                marker = _role_marker(role, self.header_level)
                self.comment_open = role == "Tools" and self.tools_display == "comment"
                wrapper = "<!--\n" if self.comment_open else ""
                self.file.write(f"\n\n{wrapper}{marker}\n\n")
                self.role = role
                self.at_line_start = True
            elif self.after_reference:
                self.file.write("\n\n")
                self.at_line_start = True
            self.after_reference = False
            if escape_role_markers:
                prefix = "" if self.at_line_start else "x"
                text = Conversation._escape_role_markers(prefix + text)[len(prefix) :]
            self.file.write(text)
            self.at_line_start = text.endswith("\n")

        def write(self, text):
            self._write_role("Assistant", text)

        def write_reasoning(self, text):
            self._write_role("Reasoning", text, escape_role_markers=True)

        def write_reference(self, role, key, description=None):
            label = "@sidecar" + (f" {description}" if description else "")
            reference = f"[{label}]({key})"
            if self.role == role and self.after_reference:
                self.file.write("\n" + reference)
                self.at_line_start = False
            else:
                self._write_role(role, reference)
            self.after_reference = True

        def flush(self):
            self.file.flush()

        def finish(self):
            """Close any hidden Tools block before visible content or exit."""

            if self.comment_open:
                self.file.write("\n-->")
                self.comment_open = False

    @staticmethod
    def _escape_role_markers(text):
        """Escape reserved role markers at the start of generated text lines."""

        lines = []
        for line in text.splitlines(keepends=True):
            if ROLE_MARKER_RE.match(line):
                line = "\\" + line
            lines.append(line)
        return "".join(lines)

    @staticmethod
    def _seek_append_point(file):
        """Seek to the end of existing content, ignoring trailing whitespace."""
        file.seek(0, 2)
        file_length = file.tell()

        MX = 100
        for _ in range(MX):
            if file_length == 0:
                break
            file.seek(file_length - 1)
            if not file.read(1).isspace():
                file.seek(file_length)
                break
            file_length -= 1
        else:
            log.debug(
                f"Did not find a non-whitespace character within the last {MX} "
                "characters."
            )
        file.truncate()

    def set_title(self):
        """Replace the auto-title marker with a generated title when present."""
        top, rest = self.text.split("\n", 1)
        if AUTO_TITLE not in top:
            log.debug(f"{AUTO_TITLE!r} not found in first line.")
            return  # This will happen nearly every time but the first

        title_conversation = [
            item
            for item in self.parsed["conversation"]
            if item["role"] in {"user", "assistant"}
        ]
        new = [
            {"role": "system", "content": TITLE_SYSTEM_PROMPT},
            *title_conversation,
            {"role": "user", "content": "Generate a title for the conversation above."},
        ]

        if _test_mode_enabled():  # For testing, I don't want to provide this
            new = new[:1]

        response = self.call_llm(messages=new, title_call=True)
        title = response.content

        top = top.replace(AUTO_TITLE, title)
        self.text = f"{top}\n{rest}"
        with open(self.filepath, "wt") as fp:
            fp.write(self.text)
        log.info(f"Set title to {title!r}")

    @cached_property
    def settings(self):
        """dict: Effective settings, including invocation overrides and fallback model."""
        saved = self.parsed["settings"]
        if self._legacy_overrides or _has_legacy_settings(saved):
            log.warning(_LEGACY_SETTINGS_WARNING)
        return _merge_settings(self._resolved_settings, {})

    read_settings = staticmethod(read_settings)

    loads = staticmethod(loads)

    def process_conversation(self):
        """Convert model-facing conversation blocks into OpenAI-style messages.

        Local-only blocks remain in the parsed representation but are omitted
        here.

        Returns
        -------
        list
            Merged message dictionaries with Markdown images converted to image
            URL content blocks.
        """
        conversation = []
        seen_references = set()
        pending_final_assistant = None
        backend = self._local_settings.get("backend", "completion")
        for item in self.parsed["conversation"]:
            if item["role"] == "reasoning":
                continue
            if item["role"] == "tools":
                keys = references(item["content"])
                if item["role"] == "tools" and not keys:
                    raise ValueError("Tools block requires a sidecar reference")
                pending_reasoning = None
                for key in keys:
                    if key in seen_references:
                        raise ValueError("Duplicate sidecar reference")
                    seen_references.add(key)
                    record = Sidecar(self.filepath).read(key)
                    if record.get("kind") == "reasoning":
                        if "assistant_native" in record:
                            if pending_reasoning is not None:
                                raise ValueError(
                                    "Tool reasoning must precede its tool exchange"
                                )
                            replay_reasoning(conversation, record, backend)
                            pending_final_assistant = conversation[-1]
                            continue
                        if pending_reasoning is not None:
                            raise ValueError(
                                "Multiple reasoning records before one tool exchange"
                            )
                        pending_reasoning = record
                    elif record.get("kind") == "tools":
                        replay_exchange(
                            conversation, record, backend, pending_reasoning
                        )
                        pending_reasoning = None
                    else:
                        raise ValueError(
                            "Tools reference must identify reasoning or a tool exchange"
                        )
                if pending_reasoning is not None:
                    raise ValueError(
                        "Tool reasoning must precede its tool exchange in the same Tools block"
                    )
                continue
            if item["role"] in LOCAL_ONLY_ROLES:
                continue

            msg = item["content"]

            # Clean up and unescape
            msg_lines = []
            for line in msg.strip().split("\n"):
                if line.startswith("\\") and ROLE_MARKER_RE.match(line[1:]):
                    line = line[1:]
                msg_lines.append(line)

            msg, img_urls, attachments, includes = process_msg_for_attachments(
                msg_lines, attachments=item["role"] == "user"
            )
            if attachments:
                log.debug(
                    "Found %d attachment directive(s) in %s message",
                    len(attachments),
                    item["role"],
                )

            if img_urls or attachments or includes:
                content = [{"type": "text", "text": msg}]
            else:
                content = msg

            for img_url in img_urls:
                content_item = {"type": "image_url"}
                if re.match("https?://.*", img_url, flags=re.IGNORECASE):
                    content_item["image_url"] = {"url": img_url}
                    log.debug(f"Found image with URL: {img_url!r}")
                elif img_url.lower().startswith("data:"):
                    content_item["image_url"] = {"url": img_url}
                    log.debug(f"Found 'data:<...>' URL")
                else:
                    # Need to load it relative to the file
                    img_path = os.path.join(os.path.dirname(self.filepath), img_url)
                    mime_type, _ = mimetypes.guess_type(img_path)
                    mime_type = mime_type or "application/octet-stream"
                    with open(img_path, "rb") as fp:
                        data = fp.read()
                        img_data = base64.b64encode(data).decode("utf-8")
                        content_item["image_url"] = {
                            "url": f"data:{mime_type};base64,{img_data}"
                        }
                    log.debug(f"Found image {img_path!r}, {len(data)} bytes")
                content.append(content_item)

            for attachment in attachments:
                attachment_url, filename = self._attachment_url(attachment)
                content.append(
                    {
                        "type": "file",
                        "file": {"file_data": attachment_url, "filename": filename},
                    }
                )

            for include in includes:
                content[0]["text"] += self._included_attachment_text(include)

            if (
                item["role"] == "assistant"
                and conversation
                and conversation[-1] is pending_final_assistant
                and not pending_final_assistant["content"]
            ):
                # A completed payload can place its native reference before
                # the visible answer. Keep both in the same replay message.
                pending_final_assistant["content"] = content
            else:
                conversation.append({"role": item["role"], "content": content})
            pending_final_assistant = None

        return merge_message_runs(conversation)

    def _included_attachment_text(self, attachment):
        """Read a UTF-8 attachment for an ``@include`` Markdown directive."""

        if re.match(r"(?:https?|data):", attachment, flags=re.IGNORECASE):
            raise ValueError("Included attachments must be local UTF-8 text files")

        filepath = os.path.join(os.path.dirname(self.filepath), attachment)
        mime_type, _ = mimetypes.guess_type(filepath)
        mime_type = mime_type or "text/plain"
        log.debug("Including text attachment %r with MIME type %s", filepath, mime_type)
        with open(filepath, "rb") as fp:
            data = fp.read()
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError(
                f"Included attachment {attachment!r} is not UTF-8 text"
            ) from error

        log.debug("Included attachment %r, %d bytes", filepath, len(data))
        return f"\n\n--- Included file: {os.path.basename(filepath)} ---\n\n{text}"

    def _attachment_url(self, attachment):
        """Resolve an attachment target to a data or remote URL and filename."""

        if re.match(
            r"https?://.*", attachment, flags=re.IGNORECASE
        ) or attachment.lower().startswith("data:"):
            path = urlsplit(attachment).path
            filename = os.path.basename(path) or "attachment"
            log.debug("Using attachment URL %r as %r", attachment, filename)
            return attachment, filename

        filepath = os.path.join(os.path.dirname(self.filepath), attachment)
        mime_type, _ = mimetypes.guess_type(filepath)
        mime_type = mime_type or "application/octet-stream"
        log.debug("Loading attachment %r with MIME type %s", filepath, mime_type)
        with open(filepath, "rb") as fp:
            data = fp.read()
        encoded = base64.b64encode(data).decode("utf-8")
        log.debug(f"Found attachment {filepath!r}, {len(data)} bytes")
        return f"data:{mime_type};base64,{encoded}", os.path.basename(filepath)

    def rename_by_title(self):
        """Rename the conversation file from its title when safe to do so."""
        dirname = os.path.dirname(self.filepath)
        ext = os.path.splitext(self.filepath)[1]

        # Compute the new name without worrying about duplicates
        title, *_ = self.text.split("\n", 1)

        if AUTO_TITLE in title:  # BEFORE cleaning it
            log.warning(f"{AUTO_TITLE!r} in title. Not renaming!")
            return

        # Clean the current for possible "<name> (n).<ext>"
        cleaned_filepath = clean_filepath(self.filepath)
        cleaned_filename = os.path.basename(cleaned_filepath)
        log.debug(f"{cleaned_filename = }")

        # Create a filename from the title
        title_based_filename = title2filename(title, ext=ext)
        title_based_filepath = os.path.join(dirname, title_based_filename)
        log.debug(f"{title_based_filename = }")
        if cleaned_filename == title_based_filename:
            log.debug("Already named by title. No action needed")
            return

        title_based_filepath = uniqueify_filepath(title_based_filepath)
        move_conversation(self.filepath, title_based_filepath)

        log.info(f"Rename by title {self.filepath!r} --> {title_based_filepath!r}")
        self.filepath = title_based_filepath


def _settings_log_view(value):
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            name = key.lower()
            sensitive = name in {
                "api_key",
                "token",
                "authorization",
                "password",
                "secret",
            } or name.endswith(("_api_key", "_token", "_password", "_secret"))
            result[key] = "<redacted>" if sensitive else _settings_log_view(item)
        return result
    if isinstance(value, list):
        return [_settings_log_view(item) for item in value]
    return value
