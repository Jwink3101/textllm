"""Conversation lifecycle, model calls, and streamed Markdown updates."""

import base64
import json
import mimetypes
import os
import re
import shutil
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
from .config import AUTO_TITLE, CONFIG, TITLE_SYSTEM_PROMPT, _test_mode_enabled, log
from .files import clean_filepath, title2filename, uniqueify_filepath
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
    """

    def __init__(self, filepath):
        self.filepath = self.filepath0 = filepath

        # Read and strip trailing whitespace before parsing.
        with open(self.filepath, "rt") as fp:
            self.text = fp.read().rstrip()

        self.parsed = loads(self.text)
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

        settings = self.settings.copy() | new_settings
        textllm_settings = settings.pop("textllm", {})
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
            settings.update(textllm_settings.get("title", {}))
            settings.pop("tools", None)
            settings.pop("tool_choice", None)
            settings.pop("max_tool_calls", None)
        if backend == "completion" and token_mode != "none":
            stream_options = dict(settings.get("stream_options") or {})
            stream_options["include_usage"] = True
            settings["stream_options"] = stream_options
        log.debug(f"Settings {settings}")

        model = settings.pop("model")  # Will KeyError if not set as expected
        log.debug(f"{model = }")

        content = []
        reasoning_summary = []
        citations = []
        usage_metadata = None
        last_tool_status = None
        if print_stream:
            print("\n", end="", flush=True)
        for event in iter_model_events(
            backend=backend,
            model=model,
            messages=messages,
            settings=settings,
        ):
            if event.kind == "output_text_delta":
                content.append(event.text)
                if print_stream:
                    print(event.text, end="", flush=True)
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
                print(sources, end="", flush=True)
            if stream_writer:
                stream_writer.write(sources)
                stream_writer.flush()
        if print_stream:
            print("\n\n", end="", flush=True)

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
            log.info("Reasoning summary: %s", "".join(reasoning_summary))

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
            stream_writer = self._ResponseBlockWriter(file, header_level)

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
                file.write(
                    f"\n\n{_role_marker('Note', header_level)}  \n\n" + token_usage
                )

            file.write(f"\n\n{_role_marker('User', header_level)}  \n\n")
            file.flush()

        # Not really needed but in case I do more with it later
        self.messages.append({"role": "assistant", "content": response.content})
        log.info(f"Updated {self.filepath!r}")

    class _ResponseBlockWriter:
        """Write answer and reasoning blocks in model-event order."""

        def __init__(self, file, header_level=0):
            self.file = file
            self.header_level = header_level
            self.role = None
            self.at_line_start = True

        def _write_role(self, role, text, *, escape_role_markers=False):
            if not text:
                return
            if self.role != role:
                marker = _role_marker(role, self.header_level)
                self.file.write(f"\n\n{marker}  \n\n")
                self.role = role
                self.at_line_start = True
            if escape_role_markers:
                prefix = "" if self.at_line_start else "x"
                text = Conversation._escape_role_markers(prefix + text)[len(prefix) :]
            self.file.write(text)
            self.at_line_start = text.endswith("\n")

        def write(self, text):
            self._write_role("Assistant", text)

        def write_reasoning(self, text):
            self._write_role("Reasoning", text, escape_role_markers=True)

        def flush(self):
            self.file.flush()

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
            if item["role"] not in LOCAL_ONLY_ROLES
        ]
        new = [
            {"role": "system", "content": TITLE_SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(title_conversation)},
        ]

        if _test_mode_enabled():  # For testing, I don't want to provide this
            del new[1]

        response = self.call_llm(messages=new, title_call=True)
        title = response.content

        top = top.replace(AUTO_TITLE, title)
        self.text = f"{top}\n{rest}"
        with open(self.filepath, "wt") as fp:
            fp.write(self.text)
        log.info(f"Set title to {title!r}")

    @cached_property
    def settings(self):
        """dict: Conversation settings merged onto template defaults."""
        defaults = Conversation.read_settings(CONFIG.TEMPLATE)
        return defaults | self.parsed["settings"]

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
        for item in self.parsed["conversation"]:
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

            conversation.append({"role": item["role"], "content": content})

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
        shutil.move(self.filepath, title_based_filepath)

        log.info(f"Rename by title {self.filepath!r} --> {title_based_filepath!r}")
        self.filepath = title_based_filepath
