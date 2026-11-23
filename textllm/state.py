"""Small, disposable continuation records shared by CLI invocations."""

import getpass
import hashlib
import os
import json
import tempfile
from pathlib import Path

from .config import log


def _state_root():
    if hasattr(os, "getuid"):
        user = str(os.getuid())
    else:
        user = hashlib.sha256(getpass.getuser().encode()).hexdigest()[:16]
    base = os.environ.get("TEXTLLM_TEMP_DIR") or tempfile.gettempdir()
    return Path(base).expanduser().absolute() / f"textllm-{user}"


def _read_state():
    try:
        with (_state_root() / "state.json").open(encoding="utf-8") as stream:
            state = json.load(stream)
    except FileNotFoundError:
        return {}
    except (ValueError, UnicodeError) as error:
        raise ValueError("Invalid continuation state JSON") from error
    if not isinstance(state, dict) or any(
        not isinstance(value, str) or not value for value in state.values()
    ):
        raise ValueError("Invalid continuation state JSON")
    return state


def _last_conversation(kind="regular"):
    path = _read_state().get(f"last_{kind}")
    if path is None:
        raise ValueError(f"No last {kind} conversation recorded; specify a file.")
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
    temporary = None
    try:
        root = _state_root()
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        state = _read_state()
        state[f"last_{kind}"] = os.path.abspath(filepath)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=root, prefix=".state-", suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump(state, stream, indent=1)
            stream.write("\n")
        os.replace(temporary, root / "state.json")
    except (OSError, ValueError) as error:
        log.warning("Could not remember conversation: %s", error)
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
