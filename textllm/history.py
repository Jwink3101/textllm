"""Versioned conversation sidecars and Markdown-controlled native replay."""

import copy
import re
import secrets
import sqlite3
import time
from pathlib import Path
from uuid import UUID

from .markdown import _merge_content

REFERENCE = re.compile(
    r"^[ \t]*\[@sidecar(?:[ \t]+[^\]\n]+)?\]\(([0-9a-f-]{36})\)[ \t]*$",
    re.MULTILINE | re.IGNORECASE,
)


def _uuid7():
    """Return a UUIDv7 with Unix milliseconds and 74 random bits (RFC 9562).

    IDs sort by timestamp; order within a millisecond is random. Use only
    standard-library APIs available on Python 3.11 and later.
    """
    timestamp_ms = time.time_ns() // 1_000_000
    random_bits = secrets.randbits(74)
    return UUID(
        int=(timestamp_ms << 80)
        | (7 << 76)
        | ((random_bits >> 62) << 64)
        | (2 << 62)
        | (random_bits & ((1 << 62) - 1))
    )


class Sidecar:
    """Persist JSON records in the conversation's adjacent SQLite database.

    Parameters
    ----------
    filepath : str or path-like
        Markdown conversation path; ``.db`` is appended literally.
    """

    def __init__(self, filepath):
        self.path = Path(str(filepath) + ".db")

    def _open(self, *, create=False):
        from jsonlitedb import JSONLiteDB

        if not create and not self.path.is_file():
            raise ValueError(f"Missing conversation sidecar: {self.path}")
        connection = sqlite3.connect(str(self.path))
        try:
            db = JSONLiteDB(connection, wal_mode=False)
            db.db.execute("PRAGMA journal_mode=DELETE").fetchone()
            db.create_index("id", unique=True)
        except BaseException:
            connection.close()
            raise
        return db

    def save(self, record):
        """Commit a record before publishing or updating its reference.

        Parameters
        ----------
        record : dict
            Versioned history record; receives a UUIDv7 if not already assigned.

        Returns
        -------
        str
            Persistent record key.
        """
        if "id" not in record:
            record["id"] = str(_uuid7())
        record.setdefault("version", 1)
        db = self._open(create=True)
        try:
            db.insert(record, duplicates="replace")
        finally:
            db.close()
        return record["id"]

    def read(self, key):
        """Read a referenced record without executing it.

        Parameters
        ----------
        key : str
            UUID record key.

        Returns
        -------
        dict
            Versioned record.
        """
        if str(UUID(key)) != key:
            raise ValueError("Invalid sidecar UUID")
        db = self._open()
        try:
            record = db.one(id=key)
        finally:
            db.close()
        if record is None:
            raise ValueError(f"Unknown sidecar key: {key}")
        if record.get("version") != 1:
            raise ValueError("Unsupported sidecar record version")
        return record


def references(content):
    """Extract sidecar keys from a reserved history block.

    Parameters
    ----------
    content : str
        Tools block body.

    Returns
    -------
    list of str
        Keys in Markdown order.
    """
    keys = REFERENCE.findall(content)
    if "@sidecar" in REFERENCE.sub("", content).lower():
        raise ValueError("Malformed sidecar reference")
    return keys


def split_reasoning(native, backend):
    """Separate provider-native reasoning from an assistant response.

    Parameters
    ----------
    native : dict or list
        Native assistant continuation payload.
    backend : str
        Model API backend.

    Returns
    -------
    tuple
        Remaining payload, reasoning payload, and original list positions.
    """
    native = copy.deepcopy(native)
    if backend == "completion":
        reasoning = {
            key: native.pop(key)
            for key in ("reasoning", "reasoning_content", "reasoning_details")
            if key in native
        }
        return native, reasoning, []
    remaining, reasoning, positions = [], [], []
    for index, item in enumerate(native):
        if item.get("type") in ("reasoning", "thinking", "redacted_thinking"):
            reasoning.append(item)
            positions.append(index)
        else:
            remaining.append(item)
    return remaining, reasoning, positions


def replay_exchange(messages, record, backend, reasoning=None):
    """Insert native tool history using visible Markdown assistant text.

    Parameters
    ----------
    messages : list of dict
        Model messages constructed so far, modified in place.
    record : dict
        Completed tool exchange.
    backend : str
        Current backend; native exchanges cannot cross backend boundaries.
    reasoning : dict, optional
        Explicitly referenced reasoning record preceding this exchange.
    """
    if record.get("kind") != "tools" or record.get("status") != "complete":
        raise ValueError(
            "Incomplete or invalid tool exchange; inspect sidecar, do not retry automatically"
        )
    if record.get("backend") != backend:
        raise ValueError("Cannot replay tool history with a different backend")
    calls, results = record.get("calls", []), record.get("results", [])
    if not calls or [c["id"] for c in calls] != [r["call_id"] for r in results]:
        raise ValueError("Invalid sidecar call/result pairing")
    # Only an explicit reasoning reference restores native reasoning, including
    # when a first-pass record still has it embedded in the exchange payload.
    native, _, old_positions = split_reasoning(record["native"], backend)
    if reasoning is not None:
        if record.get("reasoning_key", reasoning["id"]) != reasoning["id"]:
            raise ValueError("Reasoning reference belongs to a different tool exchange")
        native = _restore_reasoning(
            native, reasoning, backend, record.get("reasoning_positions", old_positions)
        )
    _append_assistant(messages, native, backend)
    for result in results:
        messages.append(
            {
                "role": "tool",
                "tool_call_id": result["call_id"],
                "content": result["output"],
                "is_error": result["is_error"],
            }
        )


def replay_reasoning(messages, record, backend):
    """Replay native reasoning associated with a completed assistant answer.

    Parameters
    ----------
    messages : list of dict
        Parsed messages, modified in place.
    record : dict
        Reasoning record with the remaining native assistant payload.
    backend : str
        Selected model API backend.
    """
    native = copy.deepcopy(record["assistant_native"])
    native = _restore_reasoning(
        native, record, backend, record.get("reasoning_positions", [])
    )
    _append_assistant(messages, native, backend)


def _restore_reasoning(native, reasoning, backend, positions):
    if reasoning.get("backend") != backend or reasoning.get("status") != "complete":
        raise ValueError("Incomplete or incompatible native reasoning")
    _, payload, _ = split_reasoning(reasoning["native"], backend)
    if backend == "completion":
        native.update(payload)
    else:
        if len(positions) != len(payload) or positions != sorted(set(positions)):
            raise ValueError("Invalid native reasoning positions")
        for position, item in zip(positions, payload):
            if not 0 <= position <= len(native):
                raise ValueError("Invalid native reasoning position")
            native.insert(position, item)
    return native


def _append_assistant(messages, native, backend):
    visible = []
    while (
        messages
        and messages[-1]["role"] == "assistant"
        and "_native" not in messages[-1]
    ):
        visible.insert(0, messages.pop()["content"])
    if not messages or messages[-1]["role"] not in ("user", "tool"):
        raise ValueError("Tool reference is out of conversation order")
    content = visible[0] if visible else ""
    for part in visible[1:]:
        content = _merge_content(content, part)
    messages.append(
        {
            "role": "assistant",
            "content": content,
            "_native": native,
            "_backend": backend,
        }
    )
