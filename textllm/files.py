"""Conversation filenames and prompt/editor file updates."""

import os
import re
import shlex
import shutil
import subprocess

from .config import CONFIG, _test_mode_enabled, log

MAX_FILENAME_CHAR = 240


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
    while os.path.lexists(filepath) or os.path.lexists(str(filepath) + ".db"):
        c += 1
        if c >= 100:
            raise ValueError(f"Too many for {filepath0!r}")

        new = f"{base} ({c}){ext}"
        filepath = os.path.join(dirname, new)
    log.debug(f"{filepath0!r} required {c} iterations for unique name: {filepath!r}")
    return filepath


def copy_conversation(source, destination):
    """Copy Markdown and its sidecar as an independent conversation.

    Parameters
    ----------
    source, destination : str or path-like
        Conversation paths. Destination files must not exist.

    Returns
    -------
    str
        Destination conversation path.
    """
    return _transfer_conversation(source, destination, move=False)


def move_conversation(source, destination):
    """Move Markdown and its sidecar, rolling back ordinary rename failures.

    Parameters
    ----------
    source, destination : str or path-like
        Conversation paths. Destination files must not exist.

    Returns
    -------
    str
        Destination conversation path.
    """
    return _transfer_conversation(source, destination, move=True)


def _transfer_conversation(source, destination, *, move):
    source, destination = os.fspath(source), os.fspath(destination)
    if os.path.abspath(source) == os.path.abspath(destination):
        raise ValueError("Conversation source and destination must differ")
    if os.path.lexists(destination) or os.path.lexists(destination + ".db"):
        raise FileExistsError("Conversation destination or sidecar already exists")
    sidecar_exists = os.path.exists(source + ".db")
    if sidecar_exists:
        # Opening through Sidecar disables WAL, checkpoints an existing WAL
        # database, and closes the connection before filesystem operations.
        from .history import Sidecar

        db = Sidecar(source)._open()
        db.close()
    created = []
    try:
        # Exclusive creation avoids overwriting even if a destination appears
        # after the preflight check. Keep the source intact until both copies
        # are durable. File operations require an idle conversation.
        for src, dst in [(source, destination)] + (
            [(source + ".db", destination + ".db")] if sidecar_exists else []
        ):
            with open(src, "rb") as reader, open(dst, "xb") as writer:
                created.append(dst)
                shutil.copyfileobj(reader, writer)
                writer.flush()
                os.fsync(writer.fileno())
        if move:
            # Retain a complete destination if deleting either source fails.
            for src in ([source + ".db"] if sidecar_exists else []) + [source]:
                os.unlink(src)
    except BaseException:
        if (
            not move
            or os.path.exists(source)
            and (not sidecar_exists or os.path.exists(source + ".db"))
        ):
            for dst in created:
                os.unlink(dst)
        raise
    return destination
