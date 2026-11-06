"""Markdown conversation parsing, role markers, and file directives."""

import itertools
import re
import tomllib

from .config import _header_prefix, log

FLAG2ROLE = {
    "--- system ---": "system",
    "--- developer ---": "developer",
    "--- user ---": "user",
    "--- assistant ---": "assistant",
    "--- note ---": "note",
    "--- reasoning ---": "reasoning",
}

LOCAL_ONLY_ROLES = {"note", "reasoning"}

ROLE_NAMES_PATTERN = "|".join(re.escape(role) for role in FLAG2ROLE.values())

ROLE_MARKER_PATTERN = rf"(?:#{{1,6}}[ \t]+)?--- (?:{ROLE_NAMES_PATTERN}) ---"

CONVO_PATTERN = re.compile(
    rf"^({ROLE_MARKER_PATTERN})",
    flags=re.MULTILINE | re.IGNORECASE,
)

ROLE_MARKER_RE = re.compile(
    rf"^(?:#{{1,6}}[ \t]+)?--- ({ROLE_NAMES_PATTERN}) ---",
    flags=re.IGNORECASE,
)


def _role_from_marker(marker):
    """Return the normalized role named by a role-marker line."""

    match = ROLE_MARKER_RE.match(marker)
    if not match:
        raise ValueError(f"Invalid role marker: {marker!r}")
    return match.group(1).lower()


def _role_marker(role, header_level=0):
    """Format a canonical role marker at the requested Markdown heading level."""

    return f"{_header_prefix(header_level)}--- {role} ---"


def _set_role_marker_header_level(text, header_level):
    """Rewrite recognized role markers to one Markdown heading level."""

    def replace(match):
        role = _role_from_marker(match.group(0)).title()
        return _role_marker(role, header_level)

    return CONVO_PATTERN.sub(replace, text)


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
    # Regex to capture standard Markdown image lines. A fallback below handles
    # valid local filenames containing parentheses.
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
            target = _markdown_image_target(line)
            if target is not None:
                image_urls.append(target)
            else:
                final_lines.append(line)

    # Return the processed text and list of image URLs
    return "\n".join(final_lines), image_urls


def _parenthesized_target(text, start):
    """Return `(target, end)` for a Markdown destination beginning at *start*.

    The scanner accepts spaces and balanced parentheses in local filenames. It
    intentionally does not interpret backslash escapes: paths are used exactly
    as written in the conversation file.
    """

    depth = 0
    angle = False
    for index in range(start, len(text)):
        char = text[index]
        if index == start and char == "<":
            angle = True
        elif angle:
            if char == ">":
                angle = False
        elif char == "(":
            depth += 1
        elif char == ")":
            if depth:
                depth -= 1
            else:
                return text[start:index].strip(), index + 1
    return None, None


def _markdown_target_value(target):
    """Normalize an angle-bracket Markdown destination without rewriting paths."""

    if target.startswith("<") and target.endswith(">"):
        return target[1:-1]
    return target


def _markdown_image_target(line):
    """Parse a standalone image line with a destination that may contain spaces."""

    match = re.match(r"^\s*!\[[^\]]*\]\(\s*", line)
    if not match:
        return None
    target, end = _parenthesized_target(line, match.end())
    if end is None or line[end:].strip():
        return None
    return _markdown_target_value(target)


def process_msg_for_attachments(lines, *, attachments=True):
    """Separate Markdown images, ``@attach``, and ``@include`` links.

    ``[@attach optional description](target)`` sends the file as a provider
    attachment. ``[@include optional description](target)`` reads a local
    UTF-8 text file into model text. The normal Markdown reference-link
    spelling is supported for both. ``@attachment`` remains an alias for
    ``@attach``. Directives can appear in prose outside fenced code blocks.
    Optional descriptions remain as text while directive syntax and matching
    reference definitions do not.

    Parameters
    ----------
    lines : list of str
        Message lines to inspect.
    attachments : bool, optional
        Whether attachment links should be processed. Attachments are user
        inputs, so non-user role content leaves them as ordinary Markdown.

    Returns
    -------
    tuple
        `(text, image_urls, attachment_targets, include_targets)`.
    """

    inline_start = re.compile(
        r"\[@(attach(?:ment)?|include)(?:\s+([^\]]*?))?\]\(\s*", re.IGNORECASE
    )
    reference = re.compile(
        r"\[@(attach(?:ment)?|include)(?:\s+(.*?))?\]\[([^\]]+)\]",
        re.IGNORECASE,
    )
    definition = re.compile(r"^\s*\[([^\]]+)\]:\s*(.+?)\s*$")

    references = {}
    in_code_block = False
    for index, line in enumerate(lines):
        if line.strip().startswith("```"):
            in_code_block = not in_code_block
            continue
        if not in_code_block and (match := definition.match(line)):
            reference_id = match.group(1).strip().casefold()
            target = _markdown_target_value(match.group(2))
            references[reference_id] = (target, index)
            log.debug(
                "Found attachment reference definition %r -> %r", reference_id, target
            )

    attachment_targets = []
    include_targets = []
    definition_indexes = set()
    final_lines = []
    in_code_block = False
    for index, line in enumerate(lines):
        if line.strip().startswith("```"):
            in_code_block = not in_code_block
            final_lines.append((index, line))
            continue
        if in_code_block:
            final_lines.append((index, line))
            continue

        if attachments:

            def replace_inline_directives(value):
                result = []
                position = 0
                while match := inline_start.search(value, position):
                    target, end = _parenthesized_target(value, match.end())
                    if end is None:
                        break
                    kind, description = match.groups()
                    target = _markdown_target_value(target)
                    result.append(value[position : match.start()])
                    result.append(description or "")
                    position = end
                    targets = (
                        include_targets
                        if kind.casefold() == "include"
                        else attachment_targets
                    )
                    targets.append(target)
                    log.debug("Detected inline @%s target %r", kind, target)
                result.append(value[position:])
                return "".join(result)

            def replace_reference(match):
                kind, description, reference_id = match.groups()
                target = references.get(reference_id.strip().casefold())
                if target is None:
                    raise ValueError(
                        f"Attachment reference {reference_id!r} is not defined"
                    )
                targets = (
                    include_targets
                    if kind.casefold() == "include"
                    else attachment_targets
                )
                targets.append(target[0])
                definition_indexes.add(target[1])
                log.debug(
                    "Resolved @%s reference %r to target %r",
                    kind,
                    reference_id,
                    target[0],
                )
                return description or ""

            line = replace_inline_directives(line)
            line = reference.sub(replace_reference, line)
        final_lines.append((index, line))

    if definition_indexes:
        final_lines = [
            (index, line)
            for index, line in final_lines
            if index not in definition_indexes
        ]

    text, image_urls = process_msg_for_images([line for _, line in final_lines])
    return text, image_urls, attachment_targets, include_targets


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
    if not ROLE_MARKER_RE.match(split_text[0]):
        top = split_text.pop(0)
    else:
        top = ""

    res["title"] = top.split("\n")[0].strip().strip("#").strip()
    res["settings"] = read_settings(top)
    res["top"] = top

    res["conversation"] = conversation = []
    for flag, msg in grouper(split_text, 2):
        msg = msg.strip()
        if not msg:
            continue  # Empty or blank

        # Clean up and unescape
        msg_lines = []
        for line in msg.strip().split("\n"):
            if line.startswith("\\") and ROLE_MARKER_RE.match(line[1:]):
                line = line[1:]
            msg_lines.append(line)

        role = _role_from_marker(flag)
        content = "\n".join(msg_lines)

        conversation.append({"role": role, "content": content})

    return res
