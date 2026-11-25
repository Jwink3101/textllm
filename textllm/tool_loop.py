"""Bounded, incremental model/tool orchestration with durable history."""

import json
from contextlib import nullcontext
from time import monotonic

from .backends import ModelEvent, native_turn, _merge_usage
from .config import log
from .history import Sidecar, split_reasoning
from .tools import MCPExecutor, model_result, tool_definitions


def iter_tool_events(
    *,
    events,
    backend,
    model,
    messages,
    settings,
    servers,
    limits,
    filepath,
    persist=True,
):
    """Stream model turns and execute requested tools with durable replay data.

    Parameters
    ----------
    events : callable
        Model event adapter, supplied by the conversation for patchable tests.
    backend, model : str
        Selected API and model.
    messages : list of dict
        Existing model-facing conversation.
    settings : dict
        Provider settings, without local settings.
    servers : list of dict
        Validated MCP servers.
    limits : dict
        Validated local tool limits.
    filepath : str or path-like
        Markdown path for the adjacent sidecar.
    persist : bool, optional
        Write history when updating a conversation file.

    Yields
    ------
    ModelEvent
        Model events and durable ``history_reference`` events for the writer.
    """
    # Sidecars belong to client tool history, not standalone reasoning. Buffer
    # reasoning until the first tool request establishes that history is needed.
    persist = persist and bool(servers)
    sidecar = Sidecar(filepath)
    context = MCPExecutor(servers, limits["timeout"]) if servers else nullcontext(None)
    working = list(messages)
    call_count = 0
    total_usage = None
    with context as executor:
        settings = settings.copy()
        if executor and executor.tools:
            definitions = tool_definitions(executor.tools, backend)
            existing = settings.get("tools", [])
            existing_names = {
                t.get("name", t.get("function", {}).get("name")) for t in existing
            }
            if existing_names & {t["name"] for t in executor.tools}:
                raise ValueError(
                    "MCP tool name collides with a configured provider tool"
                )
            settings["tools"] = existing + definitions
            if backend == "responses" and settings.get("reasoning"):
                settings["include"] = list(
                    dict.fromkeys(
                        settings.get("include", []) + ["reasoning.encrypted_content"]
                    )
                )
        for round_index in range(limits["max_rounds"] + 1):
            if (
                len(json.dumps([working, settings], ensure_ascii=True))
                > limits["max_context_chars"]
            ):
                raise ValueError(
                    "Conversation exceeded textllm.tools.max_context_chars"
                )
            text = []
            completed_text = []
            reasoning_record = None
            response = None
            completed = False
            round_usage = None
            for event in events(
                backend=backend, model=model, messages=working, settings=settings
            ):
                if event.kind == "usage":
                    round_usage = _merge_usage(round_usage, event.data)
                    continue
                if event.kind == "completed":
                    response = event.data
                    completed = True
                elif event.kind == "output_text_delta":
                    text.append(event.text)
                    # These chunks share the completed payload with native
                    # reasoning. Write its reference before the answer, while
                    # leaving genuinely streamed chunks in arrival order.
                    if event.data == {"from_completed": True}:
                        completed_text.append(event)
                        continue
                elif event.kind == "reasoning_summary_delta" and event.text and persist:
                    if reasoning_record is None:
                        reasoning_record = {
                            "kind": "reasoning",
                            "backend": backend,
                            "status": "partial",
                            "text": "",
                        }
                    reasoning_record["text"] += event.text
                    if call_count:
                        sidecar.save(reasoning_record)
                # Preserve partial text/reasoning before surfacing stream failures.
                yield event
            if not completed and servers:
                raise ValueError("Model stream ended without a completed response")
            if round_usage:
                total_usage = _sum_usage(total_usage or {}, round_usage)
                yield ModelEvent("usage", data=total_usage)
            native, calls = native_turn(backend, response)
            tool_native, native_reasoning, reasoning_positions = split_reasoning(
                native, backend
            )
            if persist and (calls or call_count) and (
                reasoning_record is not None or native_reasoning
            ):
                if reasoning_record is None:
                    reasoning_record = {
                        "kind": "reasoning",
                        "backend": backend,
                        "text": "",
                    }
                reasoning_record.update(status="complete", native=native_reasoning)
                sidecar.save(reasoning_record)
            if not calls:
                if persist and call_count and native_reasoning:
                    reasoning_record["assistant_native"] = tool_native
                    reasoning_record["reasoning_positions"] = reasoning_positions
                    sidecar.save(reasoning_record)
                    yield ModelEvent(
                        "history_reference",
                        data={
                            "role": "Tools",
                            "key": reasoning_record["id"],
                            "description": "reasoning",
                        },
                    )
                yield from completed_text
                if call_count and not "".join(text).strip():
                    raise ValueError(
                        "Model completed without answer text after tool execution"
                    )
                return
            yield from completed_text
            if not persist:
                raise ValueError(
                    "Tool execution requires a persistent conversation writer"
                )
            if executor is None:
                raise ValueError(
                    "Model requested a local tool but no MCP server is configured"
                )
            if (
                round_index >= limits["max_rounds"]
                or call_count + len(calls) > limits["max_calls"]
            ):
                raise ValueError(
                    "MCP tool round/call limit exceeded; requested calls were not executed"
                )
            arguments = [executor.validate(call) for call in calls]
            call_count += len(calls)
            record = {
                "kind": "tools",
                "backend": backend,
                "model": model,
                "sequence": round_index,
                "status": "pending",
                "native": tool_native,
                "calls": [call | executor.identity(call) for call in calls],
                "definitions": executor.tools,
                "results": [],
            }
            if reasoning_record is not None:
                record["reasoning_key"] = reasoning_record["id"]
                record["reasoning_positions"] = reasoning_positions
            sidecar.save(record)
            if reasoning_record is not None:
                yield ModelEvent(
                    "history_reference",
                    data={
                        "role": "Tools",
                        "key": reasoning_record["id"],
                        "description": "reasoning",
                    },
                )
            yield ModelEvent(
                "history_reference",
                data={
                    "role": "Tools",
                    "key": record["id"],
                    "description": "tool exchange",
                },
            )
            working.append(
                {
                    "role": "assistant",
                    "content": "".join(text),
                    "_native": native,
                    "_backend": backend,
                }
            )
            for index, (call, parsed) in enumerate(zip(calls, arguments)):
                record.update(status="running", running_call=call["id"])
                sidecar.save(record)
                identity = record["calls"][index]
                target = ".".join(
                    part
                    for part in (
                        identity.get("server"),
                        identity.get("tool", call["name"]),
                    )
                    if part
                )
                log.info(
                    "MCP call started: %r (call %r, round %d)",
                    target,
                    call["id"],
                    round_index + 1,
                )
                started = monotonic()
                try:
                    result = executor.execute(call, parsed)
                except BaseException:
                    log.error(
                        "MCP call outcome unknown: %r (call %r, %.2fs); not retried automatically",
                        target,
                        call["id"],
                        monotonic() - started,
                    )
                    record["status"] = "unknown"
                    sidecar.save(record)
                    raise
                output, is_error = model_result(result, limits["max_result_chars"])
                if is_error:
                    status = (
                        "returned an error"
                        if result.get("isError")
                        else "returned unsupported or oversized output"
                    )
                    log.warning(
                        "MCP call %s: %r (call %r, %.2fs)",
                        status,
                        target,
                        call["id"],
                        monotonic() - started,
                    )
                else:
                    log.info(
                        "MCP call completed: %r (call %r, %.2fs)",
                        target,
                        call["id"],
                        monotonic() - started,
                    )
                record["results"].append(
                    {
                        "call_id": call["id"],
                        "result": result,
                        "output": output,
                        "is_error": is_error,
                    }
                )
                record["status"] = "pending"
                sidecar.save(record)
                working.append(
                    {
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "content": output,
                        "is_error": is_error,
                    }
                )
            record.pop("running_call", None)
            record["status"] = "complete"
            sidecar.save(record)


def _sum_usage(first, second):
    result = first.copy()
    for key, value in second.items():
        if isinstance(value, dict):
            result[key] = _sum_usage(result.get(key, {}), value)
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            result[key] = result.get(key, 0) + value
        else:
            result[key] = value
    return result
