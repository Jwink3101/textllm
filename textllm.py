#!/usr/bin/env python
# -*- coding: utf-8 -*-

import argparse
import base64
import itertools
import json
import logging
import mimetypes
import os
import re
import shlex
import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from datetime import datetime
from functools import cached_property
from pathlib import Path

from dotenv import load_dotenv  # pip install python-dotenv

__version__ = "0.9.0"

log = logging.getLogger("textllm")

AUTO_TITLE = "!!AUTO TITLE!!"
TEMPLATE = """\
# {AUTO_TITLE}

```toml
# Optional Settings
temperature = {temperature}
model = {model!r}
```

Created with {version} at {now}

--- System ---

You are an expert assistant. Provide concise, accurate answers.

--- User ---

"""


TEMPLATE_PLACEHOLDER_PATTERN = re.compile(
    r"\{(AUTO_TITLE|temperature|model|now|version)(!r)?\}"
)


# Environment variable configs for defaults
class _DYNAMIC_ENV_CONFIG:
    @property
    def TEXTLLM_ENV_PATH(self):
        return os.environ.get("TEXTLLM_ENV_PATH", None)

    @property
    def TEXTLLM_EDITOR(self):
        return os.environ.get("TEXTLLM_EDITOR", os.environ.get("EDITOR", "vi"))

    @property
    def TEXTLLM_DEFAULT_MODEL(self):
        return os.environ.get("TEXTLLM_DEFAULT_MODEL", "openai/gpt-5.5")

    @property
    def TEXTLLM_DEFAULT_TEMPERATURE(self):
        return float(os.environ.get("TEXTLLM_DEFAULT_TEMPERATURE", 1.0))

    @property
    def TEXTLLM_TEMPLATE_FILE(self):
        return os.environ.get("TEXTLLM_TEMPLATE_FILE", None)

    @property
    def TEMPLATE_VALUES(self):
        return dict(
            AUTO_TITLE=AUTO_TITLE,
            temperature=float(self.TEXTLLM_DEFAULT_TEMPERATURE),
            model=self.TEXTLLM_DEFAULT_MODEL,
            now=datetime.now().astimezone().isoformat(),
            version=f"textllm-{__version__}",
        )

    @property
    def TEMPLATE(self):
        return TEMPLATE.format(**self.TEMPLATE_VALUES)

    def render_template(self, text):
        """Fill supported placeholders in a custom conversation template."""

        values = self.TEMPLATE_VALUES

        def replace(match):
            value = values[match.group(1)]
            if match.group(2) == "!r":
                return repr(value)
            return str(value)

        return TEMPLATE_PLACEHOLDER_PATTERN.sub(replace, text)


CONFIG = _DYNAMIC_ENV_CONFIG()


TITLE_SYSTEM_PROMPT = """\
Provide an appropriate, concise title for this conversation. The conversation is in JSON form with roles 'system' (or 'developer'), 'user', and 'assistant'.

- Aim for fewer than 5 words but absolutely no more than 10.
- Be as concise as possible without losing the context of the conversation.
- Your goal is to extract the key point and intent of the conversation
- Make sure the title is also appropriate for a filename. Spaces are acceptable.
- Reply with ONLY the title and nothing else!
"""

MAX_FILENAME_CHAR = 240

FLAG2ROLE = {
    "--- system ---": "system",
    "--- developer ---": "developer",
    "--- user ---": "user",
    "--- assistant ---": "assistant",
    "--- note ---": "note",
}

LOCAL_ONLY_ROLES = {"note"}

CONVO_PATTERN = re.compile(
    "(" + "|".join("^" + re.escape(flag) for flag in FLAG2ROLE) + ")",
    flags=re.DOTALL | re.MULTILINE | re.IGNORECASE,
)

DEFAULT_FILEPATH = "New Conversation.md"

TEST_MODE = False


def _test_mode_enabled():
    return TEST_MODE or os.environ.get("TEXTLLM_TEST_MODE") == "1"


def _message_text(message):
    content = message["content"]
    if isinstance(content, str):
        return content

    text = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            text.append(block.get("text", ""))
    return "\n".join(text)


def _message_image_count(message):
    content = message["content"]
    if not isinstance(content, list):
        return 0
    return sum(
        1
        for block in content
        if isinstance(block, dict) and block.get("type") == "image_url"
    )


@dataclass
class LLMResponse:
    """Normalized response returned from the model boundary."""

    content: str
    usage_metadata: dict | None = None
    reasoning_summary: str | None = None
    citations: list[dict] | None = None


@dataclass
class ModelEvent:
    """Provider-independent event emitted by a streaming model backend."""

    kind: str
    text: str = ""
    data: object = None


def _deterministic_response_content(messages):
    user_messages = [msg for msg in messages if msg["role"] == "user"]
    system_text = "\n".join(
        _message_text(msg) for msg in messages if msg["role"] == "system"
    )

    if not user_messages:
        return "title set automatically"

    if 'nothing but "hello"' in system_text.lower():
        return "hello"

    image_count = sum(_message_image_count(msg) for msg in user_messages)
    last_text = _message_text(user_messages[-1]).strip()
    words = re.findall(r"\b[\w'-]+\b", last_text)
    final_word = words[-1].strip("'\"") if words else ""
    response_count = len(user_messages)
    if messages[-1]["role"] != "user":
        response_count += 1

    base = f"{response_count} user messages. " f"Last message ended with: {final_word}"
    if image_count:
        return f"saw {image_count} images. {base}"
    return base


def _iter_test_chunks(messages):
    content = _deterministic_response_content(messages)
    parts = re.findall(r"\S+\s*", content)
    if not parts:
        parts = [""]
    yield from parts


def _chunk_text(chunk):
    """Extract streamed text from an OpenAI-style streaming chunk."""
    try:
        delta = chunk["choices"][0]["delta"]
        return delta.get("content") or ""
    except (KeyError, IndexError, TypeError):
        pass

    try:
        delta = chunk.choices[0].delta
        return getattr(delta, "content", None) or ""
    except (AttributeError, IndexError, TypeError):
        return ""


def _chunk_usage(chunk):
    """Extract usage metadata when a provider includes it in a stream chunk."""
    try:
        return chunk.get("usage")
    except AttributeError:
        return getattr(chunk, "usage", None)


def split_model_provider(model):
    """Split a model setting into an any-llm provider and model id."""

    split_at = min(
        (idx for idx in (model.find(":"), model.find("/")) if idx >= 0),
        default=-1,
    )
    if split_at >= 0:
        return model[:split_at], model[split_at + 1 :]
    return None, model


def iter_completion_text(*, model, messages, settings):
    if _test_mode_enabled():
        log.info("Using deterministic test chat model")
        for text in _iter_test_chunks(messages):
            yield text, None
        return

    from any_llm import completion

    provider, model_id = split_model_provider(model)
    completion_kwargs = dict(
        model=model_id,
        messages=messages,
        stream=True,
        **settings,
    )
    if provider:
        completion_kwargs["provider"] = provider

    stream = completion(**completion_kwargs)
    for chunk in stream:
        text = _chunk_text(chunk)
        usage = _chunk_usage(chunk)
        yield text, usage


def _object_get(value, key, default=None):
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def _plain_mapping(value):
    if value is None or isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump()
    try:
        return dict(value)
    except (TypeError, ValueError):
        return None


def _responses_messages(messages):
    """Convert chat-completion image blocks to Responses input blocks."""

    result = []
    for message in messages:
        content = message["content"]
        if not isinstance(content, list):
            result.append(message.copy())
            continue

        blocks = []
        for block in content:
            if block.get("type") == "text":
                blocks.append({"type": "input_text", "text": block.get("text", "")})
            elif block.get("type") == "image_url":
                image_url = block.get("image_url", {})
                if isinstance(image_url, dict):
                    image_url = image_url.get("url")
                blocks.append({"type": "input_image", "image_url": image_url})
            else:
                blocks.append(block.copy())
        result.append({"role": message["role"], "content": blocks})
    return result


def _messages_image_block(block):
    """Convert an OpenAI-style image block to Anthropic Messages format."""

    image_url = block.get("image_url", {})
    if isinstance(image_url, dict):
        image_url = image_url.get("url")
    if not isinstance(image_url, str) or not image_url:
        raise ValueError("Messages image input requires a non-empty URL")

    if image_url.startswith("data:"):
        header, separator, data = image_url.partition(",")
        media_type = header[5:].split(";", 1)[0]
        if not separator or ";base64" not in header or not media_type or not data:
            raise ValueError("Messages image data URL must contain base64 image data")
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type,
                "data": data,
            },
        }

    return {
        "type": "image",
        "source": {"type": "url", "url": image_url},
    }


def _combine_system_content(first, second):
    """Combine optional Anthropic system strings or content-block lists."""

    if first is None:
        return second
    if second is None:
        return first
    if isinstance(first, str) and isinstance(second, str):
        return f"{first}\n\n{second}"

    def blocks(value):
        if isinstance(value, list):
            return value.copy()
        return [{"type": "text", "text": value}]

    return blocks(first) + blocks(second)


def _messages_request(messages):
    """Build the separate system prompt and Anthropic Messages message list."""

    system_parts = []
    result = []
    for message in messages:
        role = message["role"]
        content = message["content"]
        if role in ("system", "developer"):
            system_parts.append(_message_text(message))
            continue

        if not isinstance(content, list):
            result.append({"role": role, "content": content})
            continue

        converted = []
        for block in content:
            if block.get("type") == "text":
                converted.append({"type": "text", "text": block.get("text", "")})
            elif block.get("type") == "image_url":
                converted.append(_messages_image_block(block))
            else:
                converted.append(block.copy())
        result.append({"role": role, "content": converted})

    system = "\n\n".join(part for part in system_parts if part) or None
    return system, result


def _citation(annotation):
    annotation_type = _object_get(annotation, "type")
    if annotation_type not in (
        "url_citation",
        "citation",
        "web_search_result_location",
    ):
        return None
    url = _object_get(annotation, "url")
    if not url:
        return None
    return {"url": url, "title": _object_get(annotation, "title") or url}


def _response_citations(response):
    citations = []
    for output_item in _object_get(response, "output", []) or []:
        for part in _object_get(output_item, "content", []) or []:
            for annotation in _object_get(part, "annotations", []) or []:
                if item := _citation(annotation):
                    citations.append(item)
    return citations


def _web_search_output_item_status(event_type, event):
    if event_type not in (
        "response.output_item.added",
        "response.output_item.done",
    ):
        return None
    item = _object_get(event, "item", {})
    identifiers = (
        _object_get(item, "type", ""),
        _object_get(item, "name", ""),
        _object_get(item, "tool_name", ""),
    )
    if not any("web_search" in str(value) for value in identifiers):
        return None
    return "in_progress" if event_type.endswith("added") else "completed"


def iter_responses_events(*, model, messages, settings):
    """Yield normalized events from any-llm's Responses API."""

    from any_llm import responses

    provider, model_id = split_model_provider(model)
    kwargs = dict(
        model=model_id,
        input_data=_responses_messages(messages),
        stream=True,
        **settings,
    )
    if provider:
        kwargs["provider"] = provider

    for event in responses(**kwargs):
        event_type = _object_get(event, "type", "")
        if event_type == "response.output_text.delta":
            yield ModelEvent("output_text_delta", text=_object_get(event, "delta", ""))
        elif event_type == "response.refusal.delta":
            yield ModelEvent("output_text_delta", text=_object_get(event, "delta", ""))
        elif event_type == "response.reasoning_summary_text.delta":
            yield ModelEvent(
                "reasoning_summary_delta",
                text=_object_get(event, "delta", ""),
            )
        elif event_type.startswith("response.web_search_call."):
            yield ModelEvent("tool_status", data=event_type.rsplit(".", 1)[-1])
        elif status := _web_search_output_item_status(event_type, event):
            yield ModelEvent("tool_status", data=status)
        elif event_type == "response.output_text.annotation.added":
            if citation := _citation(_object_get(event, "annotation")):
                yield ModelEvent("citation", data=citation)
        elif event_type == "response.completed":
            response = _object_get(event, "response")
            for citation in _response_citations(response):
                yield ModelEvent("citation", data=citation)
            usage = _plain_mapping(_object_get(response, "usage"))
            if usage:
                yield ModelEvent("usage", data=usage)
            yield ModelEvent("completed", data=response)
        elif event_type in (
            "error",
            "response.error",
            "response.failed",
            "response.incomplete",
        ):
            yield ModelEvent("error", data=event)


def _content_block_citations(content_block):
    if _object_get(content_block, "type") != "text":
        return []
    return [
        citation
        for annotation in _object_get(content_block, "citations", []) or []
        if (citation := _citation(annotation))
    ]


def _merge_usage(usage, update):
    update = _plain_mapping(update)
    if not update:
        return usage
    result = dict(usage or {})
    for key, value in update.items():
        if value is None:
            continue
        if isinstance(result.get(key), dict) and isinstance(value, dict):
            result[key] = _merge_usage(result[key], value)
        else:
            result[key] = value
    return result


def _format_token_usage(usage):
    """Format all provider-returned usage fields as readable counters."""

    usage = _plain_mapping(usage)
    if not usage:
        return ""

    lines = []

    def append_fields(value, prefix=""):
        if isinstance(value, dict) or hasattr(value, "model_dump"):
            value = _plain_mapping(value)
        if isinstance(value, dict):
            for key, item in value.items():
                path = f"{prefix}.{key}" if prefix else str(key)
                append_fields(item, path)
        else:
            if isinstance(value, (list, tuple)):
                value = json.dumps(value, separators=(",", ":"), default=str)
            lines.append(f"- {prefix}: {value}")

    append_fields(usage)
    if not lines:
        return ""
    return "Token usage:\n\n" + "\n".join(lines)


def iter_messages_events(*, model, messages, settings):
    """Yield normalized events from any-llm's Anthropic-style Messages API."""

    from any_llm import messages as create_message

    kwargs = settings.copy()
    try:
        max_tokens = kwargs.pop("max_tokens")
    except KeyError:
        raise ValueError("The Messages backend requires max_tokens") from None

    configured_system = kwargs.pop("system", None)
    conversation_system, request_messages = _messages_request(messages)
    system = _combine_system_content(configured_system, conversation_system)
    provider, model_id = split_model_provider(model)
    call_kwargs = dict(
        model=model_id,
        messages=request_messages,
        max_tokens=max_tokens,
        stream=True,
        **kwargs,
    )
    if provider:
        call_kwargs["provider"] = provider
    if system is not None:
        call_kwargs["system"] = system

    if provider == "openrouter":
        call_kwargs["provider"] = "anthropic"
        call_kwargs.setdefault("api_base", "https://openrouter.ai/api")
        if "api_key" not in call_kwargs:
            api_key = os.environ.get("OPENROUTER_API_KEY")
            if not api_key:
                raise ValueError(
                    "OpenRouter Messages requires OPENROUTER_API_KEY or an api_key setting"
                )
            call_kwargs["api_key"] = api_key

    usage = None
    for event in create_message(**call_kwargs):
        event_type = _object_get(event, "type", "")
        if event_type == "message_start":
            usage = _merge_usage(
                usage, _object_get(_object_get(event, "message"), "usage")
            )
        elif event_type == "message_delta":
            usage = _merge_usage(usage, _object_get(event, "usage"))
        elif event_type == "content_block_start":
            content_block = _object_get(event, "content_block")
            block_type = _object_get(content_block, "type")
            if block_type == "text" and (
                text := _object_get(content_block, "text", "")
            ):
                yield ModelEvent("output_text_delta", text=text)
            elif block_type == "thinking" and (
                thinking := _object_get(content_block, "thinking", "")
            ):
                yield ModelEvent("reasoning_summary_delta", text=thinking)
            elif (
                block_type == "server_tool_use"
                and _object_get(content_block, "name") == "web_search"
            ):
                yield ModelEvent("tool_status", data="in_progress")
            elif block_type == "web_search_tool_result":
                yield ModelEvent("tool_status", data="completed")
            for citation in _content_block_citations(content_block):
                yield ModelEvent("citation", data=citation)
        elif event_type == "content_block_delta":
            delta = _object_get(event, "delta")
            delta_type = _object_get(delta, "type")
            if delta_type == "text_delta":
                yield ModelEvent(
                    "output_text_delta", text=_object_get(delta, "text", "")
                )
            elif delta_type == "thinking_delta":
                yield ModelEvent(
                    "reasoning_summary_delta",
                    text=_object_get(delta, "thinking", ""),
                )
            elif delta_type == "citations_delta":
                if citation := _citation(_object_get(delta, "citation")):
                    yield ModelEvent("citation", data=citation)
        elif event_type == "content_block_stop":
            content_block = _object_get(event, "content_block")
            for citation in _content_block_citations(content_block):
                yield ModelEvent("citation", data=citation)
        elif event_type == "message_stop":
            message = _object_get(event, "message")
            usage = _merge_usage(usage, _object_get(message, "usage"))
            if usage:
                if "total_tokens" not in usage:
                    input_tokens = usage.get("input_tokens")
                    output_tokens = usage.get("output_tokens")
                    if input_tokens is not None and output_tokens is not None:
                        usage["total_tokens"] = input_tokens + output_tokens
                yield ModelEvent("usage", data=usage)
            yield ModelEvent("completed", data=message)


def iter_model_events(*, backend, model, messages, settings):
    """Yield normalized model events from the configured API backend."""

    if backend == "completion":
        for text, usage in iter_completion_text(
            model=model,
            messages=messages,
            settings=settings,
        ):
            if text:
                yield ModelEvent("output_text_delta", text=text)
            if usage:
                yield ModelEvent("usage", data=_plain_mapping(usage))
        yield ModelEvent("completed")
        return

    if backend == "responses":
        if _test_mode_enabled():
            log.info("Using deterministic test Responses model")
            tools = settings.get("tools", [])
            uses_web_search = any(
                tool.get("type") in ("web_search", "openrouter:web_search")
                for tool in tools
            )
            if uses_web_search:
                yield ModelEvent("tool_status", data="in_progress")
                yield ModelEvent("tool_status", data="completed")
            reasoning = settings.get("reasoning")
            if reasoning and reasoning.get("summary"):
                yield ModelEvent(
                    "reasoning_summary_delta",
                    text="Deterministic reasoning summary.",
                )
            for text in _iter_test_chunks(messages):
                yield ModelEvent("output_text_delta", text=text)
            if uses_web_search:
                yield ModelEvent(
                    "citation",
                    data={
                        "title": "Deterministic source",
                        "url": "https://example.com/",
                    },
                )
            yield ModelEvent("completed")
            return
        yield from iter_responses_events(
            model=model,
            messages=messages,
            settings=settings,
        )
        return

    if backend == "messages":
        if _test_mode_enabled():
            log.info("Using deterministic test Messages model")
            tools = settings.get("tools", [])
            uses_web_search = any(
                tool.get("name") == "web_search"
                or str(tool.get("type", "")).startswith("web_search_")
                for tool in tools
            )
            if uses_web_search:
                yield ModelEvent("tool_status", data="in_progress")
                yield ModelEvent("tool_status", data="completed")
            thinking = settings.get("thinking", {})
            if thinking.get("type") in ("adaptive", "enabled"):
                yield ModelEvent(
                    "reasoning_summary_delta",
                    text="Deterministic reasoning summary.",
                )
            for text in _iter_test_chunks(messages):
                yield ModelEvent("output_text_delta", text=text)
            if uses_web_search:
                yield ModelEvent(
                    "citation",
                    data={
                        "title": "Deterministic source",
                        "url": "https://example.com/",
                    },
                )
            yield ModelEvent("completed")
            return
        yield from iter_messages_events(
            model=model,
            messages=messages,
            settings=settings,
        )
        return

    raise ValueError(f"Unknown textllm backend: {backend!r}")


def _sources_markdown(citations):
    seen = set()
    lines = []
    for citation in citations:
        url = str(citation["url"])
        if url in seen:
            continue
        seen.add(url)
        url = url.replace("<", "%3C").replace(">", "%3E").replace(" ", "%20")
        title = " ".join(str(citation.get("title") or url).split())
        title = title.replace("[", "\\[").replace("]", "\\]")
        lines.append(f"- [{title}](<{url}>)")
    if not lines:
        return ""
    return "\n\n### Sources\n\n" + "\n".join(lines)


def _content_blocks(content):
    if isinstance(content, list):
        return content.copy()
    return [{"type": "text", "text": content}]


def _merge_content(first, second):
    if isinstance(first, str) and isinstance(second, str):
        return f"{first}\n\n{second}"

    blocks = _content_blocks(first)
    if blocks and blocks[-1].get("type") == "text":
        blocks[-1] = blocks[-1].copy()
        blocks[-1]["text"] = f"{blocks[-1].get('text', '')}\n\n"
    else:
        blocks.append({"type": "text", "text": "\n\n"})
    blocks.extend(_content_blocks(second))
    return blocks


def merge_message_runs(messages):
    """Merge adjacent messages with the same role.

    This mirrors the behavior from earlier backends while keeping messages as
    provider-friendly dictionaries.
    """
    merged = []
    for message in messages:
        if merged and merged[-1]["role"] == message["role"]:
            merged[-1] = {
                "role": merged[-1]["role"],
                "content": _merge_content(
                    merged[-1]["content"],
                    message["content"],
                ),
            }
            continue
        merged.append(message.copy())
    return merged


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
            Writer that receives response chunks as they arrive.
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
        if reasoning_summary_mode not in ("omit", "note", "stderr"):
            raise ValueError(
                "textllm.reasoning_summary must be 'omit', 'note', or 'stderr'"
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
                if reasoning_summary_mode == "note" and reasoning_summary
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

    def chat(self, require_user_prompt=True, tokens=None):
        """Append one assistant response to the conversation file.

        Parameters
        ----------
        require_user_prompt : bool, optional
            Require the current conversation to end with a user message.
        tokens : {None, "none", "stderr", "note"}, optional
            Override the conversation's token-reporting setting.

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

        with open(self.filepath, "r+") as file:
            self._seek_append_point(file)
            stream_writer = self._AssistantBlockWriter(file)

            response = self.call_llm(
                messages=self.messages,
                print_stream=True,
                stream_writer=stream_writer,
                tokens=tokens,
            )

            if response.reasoning_summary:
                file.write(
                    "\n\n--- Note ---  \n\nReasoning summary:\n\n"
                    + self._escape_role_markers(response.reasoning_summary)
                )

            token_mode = self.settings.get("textllm", {}).get("tokens", "none")
            if tokens is not None:
                token_mode = tokens
            token_usage = _format_token_usage(response.usage_metadata)
            if token_mode == "note" and token_usage:
                file.write("\n\n--- Note ---  \n\n" + token_usage)

            file.write("\n\n--- User ---  \n\n")
            file.flush()

        # Not really needed but in case I do more with it later
        self.messages.append({"role": "assistant", "content": response.content})
        log.info(f"Updated {self.filepath!r}")

    class _AssistantBlockWriter:
        """Write the assistant heading only when response text exists."""

        def __init__(self, file):
            self.file = file
            self.started = False

        def write(self, text):
            if not text:
                return
            if not self.started:
                self.file.write("\n\n--- Assistant ---  \n\n")
                self.started = True
            self.file.write(text)

        def flush(self):
            self.file.flush()

    @staticmethod
    def _escape_role_markers(text):
        """Escape reserved role markers at the start of summary lines."""

        lines = []
        for line in text.splitlines(keepends=True):
            if any(line.lower().startswith(flag) for flag in FLAG2ROLE):
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

    @staticmethod
    def read_settings(text):
        """Read the first TOML fenced code block from Markdown text.

        Parameters
        ----------
        text : str
            Markdown text that may contain a TOML fenced code block.

        Returns
        -------
        dict
            Parsed TOML settings, or an empty dictionary when none are found.
        """
        pattern = re.compile(
            r"""
                ^```          # Start of line with fenced code block
                \s*           # Optional whitespace
                (?:toml)?     # Optional designation as TOML
                \s*$          # Optional whitespace to end of line
                \n            # At least one new line
                (.*?)         # Actual TOML code (non-greedy)
                \n            # New line before closing fence
                ^```          # Closing fence on its own line
                \s*$          # Optional whitespace to end of line
            """,
            flags=re.VERBOSE | re.DOTALL | re.MULTILINE | re.IGNORECASE,
        )

        if match := pattern.search(text):  # First one only
            toml_content = match.group(1).strip()
            return tomllib.loads(toml_content)

        return {}

    @staticmethod
    def loads(text):
        """Parse textllm Markdown into title, settings, top matter, and messages.

        Parameters
        ----------
        text : str
            Markdown conversation text.

        Returns
        -------
        dict
            Parsed conversation data with `title`, `settings`, `top`, and
            `conversation` keys.
        """
        res = {}

        split_text = CONVO_PATTERN.split(text)

        # Split will split at the flags. If the first item is a flag, then there is no
        # top matter. If it isn't a flag, the first item is top matter.
        if split_text[0].lower() not in FLAG2ROLE:
            top = split_text.pop(0)
        else:
            top = ""

        res["title"] = top.split("\n")[0].strip().strip("#").strip()
        res["settings"] = Conversation.read_settings(top)
        res["top"] = top

        re_role = re.compile("--- (.*) ---")
        res["conversation"] = conversation = []
        for flag, msg in grouper(split_text, 2):
            msg = msg.strip()
            if not msg:
                continue  # Empty or blank

            # Clean up and unescape
            msg_lines = []
            for line in msg.strip().split("\n"):
                if any(line.lower().startswith(rf"\{flag}") for flag in FLAG2ROLE):
                    line = line[1:]
                msg_lines.append(line)

            role = re_role.findall(flag)[0].lower()  # Must be a flag from initial split
            content = "\n".join(msg_lines)

            conversation.append({"role": role, "content": content})

        return res

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

            flag = f"--- {item['role']} ---"
            msg = item["content"]

            # Clean up and unescape
            msg_lines = []
            for line in msg.strip().split("\n"):
                if any(line.lower().startswith(rf"\{flag}") for flag in FLAG2ROLE):
                    line = line[1:]
                msg_lines.append(line)

            msg, img_urls = process_msg_for_images(msg_lines)

            if img_urls:
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
                    with open(img_path, "rb") as fp:
                        data = fp.read()
                        img_data = base64.b64encode(data).decode("utf-8")
                        content_item["image_url"] = {
                            "url": f"data:{mime_type};base64,{img_data}"
                        }
                    log.debug(f"Found image {img_path!r}, {len(data)} bytes")
                content.append(content_item)

            conversation.append({"role": FLAG2ROLE[flag.lower()], "content": content})

        return merge_message_runs(conversation)

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


loads = Conversation.loads


def file_edit(filepath, *, prompt, editor):
    """Apply prompt/editor changes to a conversation file.

    Parameters
    ----------
    filepath : str or path-like
        File to update.
    prompt : str
        Prompt text to append before opening an editor.
    editor : bool
        Whether to open `$TEXTLLM_EDITOR`.

    Returns
    -------
    bool
        True if the file changed; false otherwise.
    """
    size0 = os.path.getsize(filepath)
    mtime0 = os.path.getmtime(filepath)

    if prompt:
        with open(filepath, "rb+") as fp:
            # Need to be in binary mode for seek
            fp.seek(0, 2)  # Move the cursor to the end of the file
            if fp.tell() > 0:  # Check if the file is not empty
                fp.seek(-1, 2)  # Move the cursor to the last character
                last_char = fp.read(1).decode()
            else:
                last_char = ""

            if last_char and last_char != "\n":
                log.debug("Adding a new line")
                fp.write(b"\n")
            fp.write(prompt.encode())

    if editor:
        # Use shlex.split in case there are flags with the environment variable
        editcmd = shlex.split(CONFIG.TEXTLLM_EDITOR) + [filepath]
        log.debug(f"Calling: {editcmd!r}")
        if _test_mode_enabled():
            subprocess.run(editcmd, check=True)
        else:
            with open("/dev/tty", "r") as tty:
                subprocess.run(editcmd, stdin=tty, check=True)

    size1 = os.path.getsize(filepath)
    mtime1 = os.path.getmtime(filepath)
    if size1 == size0 and abs(mtime1 - mtime0) <= 0.5:
        return False
    return True


def process_msg_for_images(lines):
    """Separate Markdown image references from message text.

    Parameters
    ----------
    lines : list of str
        Message lines to inspect.

    Returns
    -------
    tuple
        `(text, image_urls)` where `text` excludes standalone Markdown image
        lines outside fenced code blocks.
    """
    # Regex to capture markdown images
    image_regex = re.compile(
        r"""
        ^               # Start of a line
        !\[             # Literal '![', start of markdown image
        (.*?)           # Non-greedy capture for the alt text (optional)
        \]              # Literal closing bracket
        \(\s*           # Literal '(', start of URL, allowing optional whitespace
        (.*?)           # Non-greedy capture for the URL
        \s*             # Allow optional whitespace
        (?:             # Non-capturing group for optional title
            "           # Opening quote for title
            (.*?)       # Non-greedy capture for the title text
            "           # Closing quote for title
        )?              # Title is optional
        \)              # Literal closing parenthesis
        \s*$            # Allow optional whitespace till the end of the line
        """,
        re.VERBOSE,
    )

    in_code_block = False
    final_lines = []
    image_urls = []

    for line in lines:
        # Check for start or end of a fenced code block
        if line.strip().startswith("```"):
            in_code_block = not in_code_block
            final_lines.append(line)
            continue

        # If inside a code block, just keep the line
        if in_code_block:
            final_lines.append(line)
            continue

        # If not inside a code block, check for images
        match = image_regex.match(line)
        if match:
            # Capture the URL from the image line
            url = match.group(2)
            image_urls.append(url)
        else:
            # Keep lines that are not image lines
            final_lines.append(line)

    # Return the processed text and list of image URLs
    return "\n".join(final_lines), image_urls


########################################
############ Filename Utils ############
########################################
def clean_filepath(filepath):
    """Remove a numeric duplicate suffix from a path.

    Parameters
    ----------
    filepath : str or path-like
        Path that may end with a duplicate suffix like ` (1)`.

    Returns
    -------
    str
        Path with the duplicate suffix removed before the extension.
    """
    base, ext = os.path.splitext(filepath)
    cleaned_filepath = re.sub(r" \(\d+\)$", "", base) + ext
    return cleaned_filepath


def title2filename(title, ext=".md"):
    """Convert a Markdown title to a filesystem-safe filename.

    Parameters
    ----------
    title : str
        Conversation title, with or without a leading Markdown heading marker.
    ext : str, optional
        Extension to append to the filename.

    Returns
    -------
    str
        Sanitized filename.
    """
    invalid_chars = set(
        "\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b\x0c\r\x0e\x0f\x10\x11\x12\x13"
        '\x14\x15\x16\x17\x18\x19\x1a\x1b\x1c\x1d\x1e\x1f"*/:<>?\\|'
    )
    title = title.strip().strip("#").strip()
    title = "".join(c for c in title if c not in invalid_chars)
    title = title[: (MAX_FILENAME_CHAR - len(ext))]
    title = title + ext
    return title


def uniqueify_filepath(filepath):
    """Return a non-existing path by adding a numeric suffix when needed.

    Parameters
    ----------
    filepath : str or path-like
        Desired path.

    Returns
    -------
    str
        `filepath`, or a suffixed variant, that does not already exist.

    Raises
    ------
    ValueError
        If no unique path is found within 99 suffix attempts.
    """
    filepath0 = filepath
    dirname, filename = os.path.split(filepath)
    base, ext = os.path.splitext(filename)

    c = 0
    while os.path.exists(filepath):
        c += 1
        if c >= 100:
            raise ValueError(f"Too many for {filepath0!r}")

        new = f"{base} ({c}){ext}"
        filepath = os.path.join(dirname, new)
    log.debug(f"{filepath0!r} required {c} iterations for unique name: {filepath!r}")
    return filepath


########################################
########## END Filename Utils ##########
########################################


def grouper(iterable, n, *, fillvalue=None):
    """Collect data into fixed-length chunks.

    Parameters
    ----------
    iterable : iterable
        Values to group.
    n : int
        Chunk size.
    fillvalue : object, optional
        Value used to pad the last chunk.

    Returns
    -------
    iterator
        Iterator of `n`-tuples.
    """
    iterators = [iter(iterable)] * n
    return itertools.zip_longest(*iterators, fillvalue="")


class NoUserMessageError(ValueError):
    """Error when a conversation does not end with a user message."""


NoHumanMessageError = NoUserMessageError


def cli(argv=None):
    """Run the textllm command-line interface.

    Parameters
    ----------
    argv : list of str, optional
        Command-line arguments. Defaults to `sys.argv[1:]`.
    """
    if argv is None:
        argv = sys.argv[1:]

    parser = argparse.ArgumentParser(
        description="Simple LLM interface that reads and writes to a text file",
        epilog="See readme.md for details on format description",
        # formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "filepath",
        nargs="?",
        default=None,
        help=f"""
            Specifies the input file in the noted format. If not provided, the default
            file {DEFAULT_FILEPATH!r} will be used, with an incremented filename to
            ensure uniqueness. If you specify an existing directory, {DEFAULT_FILEPATH!r}
            will be created in that directory.
            """,
    )

    parser.add_argument(
        "--env",
        help="""
            Specify an additional environment file to load. Note, %(prog)s will
            also look for a .env file and from $TEXTLLM_ENV_PATH.

            Useful for storing API keys""",
    )
    parser.add_argument(
        "--template",
        help="""
            Specify a template file for new chats. Overrides $TEXTLLM_TEMPLATE_FILE,
            including values loaded via --env.
            """,
    )

    parser.add_argument(
        "--title",
        choices=["auto", "only", "off"],
        default="auto",
        help=f"""
            [%(default)s] How to set the title. If 'auto', will replace {AUTO_TITLE!r}
            with the generated title. If 'only', will only replace the title and
            not continue the chat. If 'off', will not update the title (or rename).
            The title is the first line.
            """,
    )

    parser.add_argument(
        "--tokens",
        choices=["none", "stderr", "note"],
        default=None,
        help="""
            Where to report all provider-returned token usage fields. Defaults to
            [textllm].tokens, or 'none' when that setting is absent.
            """,
    )

    parser.add_argument(
        "--require-user-prompt",
        dest="require_user_prompt",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="""
            [%(default)s] Whether or not to require there be a user prompt at the end of
            the messages.
        """,
    )

    parser.add_argument(
        "--rename",
        "--move",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=f"""
            Rename the file based on the title. The title must NOT have {AUTO_TITLE!r}
            in it. Will increment the filename as needed if one already exists.
            If a filename is specified, default is False. If filename is not specified, default is True.
        """,
    )

    parser.add_argument(
        "--version",
        action="version",
        version="%(prog)s-" + __version__,
    )

    verb = parser.add_argument_group("Verbosity Settings:")
    verb.add_argument(
        "-q", "--quiet", action="count", default=0, help="Decrease Verbosity"
    )
    verb.add_argument(
        "-v", "--verbose", action="count", default=0, help="Increase Verbosity"
    )

    edit = parser.add_argument_group(
        title="Edit Settings",
        description="""
            These options let you add the prompt and/or edit the file directly
            before calling the LLM. Note it is assumed that a '--- User ---'
            heading is present (as it should be by default).
            """,
    )
    edit.add_argument(
        "--prompt",
        metavar="text",
        default="",
        help="""
            Prompt text to add. Will be included if --edit.
        """,
    )
    edit.add_argument(
        "--stdin",
        action="store_true",
        help="""
            Read stdin for prompt. Will go *after* --prompt. Will be included if --edit.
        """,
    )

    edit.add_argument(
        "--edit",
        action="store_true",
        help="""
            Open an interactive editor with the file. Will try $TEXTLLM_EDITOR, then
            $EDITOR, then finally fallback to 'vi'.
        """,
    )

    args = parser.parse_args(argv)

    # Define logging levels
    levels = [logging.ERROR, logging.WARNING, logging.INFO, logging.DEBUG]
    level_index = args.verbose - args.quiet + 2  # +1: WARNING, +2: INFO
    level_index = max(0, min(level_index, len(levels) - 1))  # Always keep ERROR

    log.setLevel(logging.DEBUG)  # Highest. Handler will set lower
    fmt = logging.Formatter(
        "%(asctime)s:%(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(fmt)
    console_handler.setLevel(levels[level_index])

    log.handlers.clear()
    log.addHandler(console_handler)
    if _test_mode_enabled():
        logfile = f"{args.filepath}.log" if args.filepath else "log"
        try:
            os.makedirs(os.path.dirname(logfile))
        except OSError:
            pass
        file_handler = logging.FileHandler(logfile, mode="w")
        file_handler.setFormatter(fmt)
        file_handler.setLevel(logging.DEBUG)
        log.addHandler(file_handler)

    log.debug(f"{argv = }")
    log.debug(f"{args = }")

    # Load the environment. Can be in three possible places (a,b,c below)
    if CONFIG.TEXTLLM_ENV_PATH:  # (a) Specified environment variable with the path
        if load_dotenv(CONFIG.TEXTLLM_ENV_PATH, override=True):
            log.debug(f"Loaded env from ${CONFIG.TEXTLLM_ENV_PATH = }")
        else:
            log.info(f"Could not load env from specified ${CONFIG.TEXTLLM_ENV_PATH = }")
    if load_dotenv(override=True):  # (b) a .env file
        log.debug(f"Loaded env from a found '.env' file")
    if args.env:  # (c) specified --env at the command line
        if load_dotenv(args.env, override=True):
            log.debug(f"Loaded env from args {args.env!r}")
        else:
            log.info(f"env file {args.env!r} not loaded or found")

    # Handle default --rename
    if args.rename is None:
        args.rename = args.filepath is None or os.path.isdir(args.filepath)
        log.debug(f"Settings --rename to {args.rename}.")

    # Handle edit modes.
    args.prompt = args.prompt.strip()
    if args.prompt == "-":
        log.warning("To read stdin, use --stdin")
    if args.stdin:
        log.debug("reading stdin")
        args.prompt = args.prompt + "\n\n" + sys.stdin.read().strip()
        args.prompt = args.prompt.strip()
    edit_mode = bool(args.edit or args.prompt or args.stdin)
    log.debug(f"{edit_mode = }")

    try:
        if args.filepath is None:
            args.filepath = uniqueify_filepath(DEFAULT_FILEPATH)
            log.debug(f"No filepath specified. Set {args.filepath!r}")
        elif os.path.isdir(args.filepath):
            fp = os.path.join(args.filepath, DEFAULT_FILEPATH)
            args.filepath = uniqueify_filepath(fp)
            log.debug(f"Directory specified. Set {args.filepath!r}")

        filepath = args.filepath
        if not os.path.exists(filepath):
            Path(filepath).parent.mkdir(parents=True, exist_ok=True)
            with open(filepath, "xt") as fp:
                template_file = args.template or CONFIG.TEXTLLM_TEMPLATE_FILE
                if template_file:
                    with open(template_file, "rt") as fp2:
                        fp.write(CONFIG.render_template(fp2.read()))
                else:
                    fp.write(CONFIG.TEMPLATE)
            log.info(f"{filepath!r} does not exist. Created template.")

            if not edit_mode:
                return
        else:
            log.debug(f"{filepath!r} exists")

        if edit_mode and not file_edit(filepath, prompt=args.prompt, editor=args.edit):
            # edit returns True iff it was modified.
            raise ValueError("File not modified")

        convo = Conversation(filepath)

        if args.title != "off":
            convo.set_title()  # Will do nothing if AUTO_TITLE not in the top line
        if args.title == "only":
            if _test_mode_enabled():
                return convo
            return

        convo.chat(require_user_prompt=args.require_user_prompt, tokens=args.tokens)

        if args.rename:
            convo.rename_by_title()

        if _test_mode_enabled():
            return convo

    except Exception as E:
        log.error(E)
        if levels[level_index] == logging.DEBUG or _test_mode_enabled():
            raise
        sys.exit(1)


if __name__ == "__main__":
    cli()
