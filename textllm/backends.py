"""Streaming model adapters and provider-independent response events."""

import base64
import json
import os
import re
from dataclasses import dataclass

from .config import _test_mode_enabled, log


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


def _message_attachment_count(message):
    """Return the number of generic file attachment blocks in a message."""

    content = message["content"]
    if not isinstance(content, list):
        return 0
    return sum(
        1
        for block in content
        if isinstance(block, dict) and block.get("type") == "file"
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
    attachment_count = sum(_message_attachment_count(msg) for msg in user_messages)
    last_text = _message_text(user_messages[-1]).strip()
    words = re.findall(r"\b[\w'-]+\b", last_text)
    final_word = words[-1].strip("'\"") if words else ""
    response_count = len(user_messages)
    if messages[-1]["role"] != "user":
        response_count += 1

    base = f"{response_count} user messages. " f"Last message ended with: {final_word}"
    if image_count:
        prefix = f"saw {image_count} images"
        if attachment_count:
            prefix += f" and {attachment_count} attachments"
        return f"{prefix}. {base}"
    if attachment_count:
        return f"saw {attachment_count} attachments. {base}"
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
    """Convert chat-completion multimodal blocks to Responses input blocks."""

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
            elif block.get("type") == "file":
                file = block.get("file", {})
                log.debug(
                    "Converting attachment %r to a Responses input_file block",
                    file.get("filename"),
                )
                blocks.append(
                    {
                        "type": "input_file",
                        "file_data": file.get("file_data"),
                        "filename": file.get("filename"),
                    }
                )
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


def _messages_attachment_block(block):
    """Convert an OpenAI-style file block to an Anthropic document block."""

    file = block.get("file", {})
    file_data = file.get("file_data")
    if not isinstance(file_data, str) or not file_data:
        raise ValueError("Messages attachment input requires non-empty file data")

    if file_data.startswith("data:"):
        header, separator, data = file_data.partition(",")
        media_type = header[5:].split(";", 1)[0]
        if not separator or ";base64" not in header or not media_type or not data:
            raise ValueError(
                "Messages attachment data URL must contain base64 file data"
            )
        log.debug(
            "Converting attachment %r to a Messages base64 document block (%s)",
            file.get("filename"),
            media_type,
        )
        return {
            "type": "document",
            "source": {"type": "base64", "media_type": media_type, "data": data},
        }

    log.debug(
        "Converting attachment %r to a Messages URL document block",
        file.get("filename"),
    )
    return {"type": "document", "source": {"type": "url", "url": file_data}}


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
            elif block.get("type") == "file":
                converted.append(_messages_attachment_block(block))
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
