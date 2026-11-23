"""Small, disposable continuation records shared by CLI invocations."""

import getpass
import hashlib
import os
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

from .config import log


def _state_root():
    if hasattr(os, "getuid"):
        user = str(os.getuid())
    else:
        user = hashlib.sha256(getpass.getuser().encode()).hexdigest()[:16]
    return Path(tempfile.gettempdir()) / f"textllm-{user}"


def _last_conversation(kind="regular"):
    database = _state_root() / "state.sqlite3"
    if not database.is_file():
        raise ValueError(f"No last {kind} conversation recorded; specify a file.")
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as conn:
        row = conn.execute(
            "SELECT value FROM state WHERE key = ?", (f"last_{kind}",)
        ).fetchone()
    if row is None:
        raise ValueError(f"No last {kind} conversation recorded; specify a file.")
    path = row[0]
    if not Path(path).is_file():
        raise ValueError(f"Last {kind} conversation is missing: {path!r}")
    return path


def _temp_directory():
    return _state_root() / "conversations"


def _conversation_kind(filepath):
    return (
        "temp"
        if Path(filepath).resolve().parent == _temp_directory().resolve()
        else "regular"
    )


def _remember_conversation(filepath, kind="regular"):
    # State is a convenience: failures must not discard a completed response.
    try:
        root = _state_root()
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        with closing(sqlite3.connect(root / "state.sqlite3", timeout=5)) as conn:
            with conn:
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS state "
                    "(key TEXT PRIMARY KEY, value TEXT NOT NULL)"
                )
                conn.execute(
                    "INSERT INTO state (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (f"last_{kind}", os.path.abspath(filepath)),
                )
    except (OSError, sqlite3.Error) as error:
        log.warning("Could not remember conversation: %s", error)
