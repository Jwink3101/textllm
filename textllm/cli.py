"""Command-line argument handling and local conversation orchestration."""

import argparse
import logging
import os
import sys
import tomllib
from datetime import UTC, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

from .output import ConsoleHandler
from . import config
from ._version import __version__
from .config import (
    AUTO_TITLE,
    CONFIG,
    DEFAULT_FILEPATH,
    _list_templates,
    _read_template,
    _test_mode_enabled,
    log,
)
from .conversation import Conversation, NoUserMessageError
from .files import file_edit, uniqueify_filepath
from .markdown import _set_role_marker_header_level
from .state import (
    _conversation_kind,
    _last_conversation,
    _remember_conversation,
    _state_root,
    _temp_directory,
)


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

    conversation_files = parser.add_argument_group("Conversation Files")
    settings = parser.add_argument_group("Settings and Templates")
    response = parser.add_argument_group("Response Settings")

    conversation_files.add_argument(
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

    conversation_files.add_argument(
        "-c",
        "--continue",
        dest="continue_conversation",
        action="store_true",
        help="Resume the last conversation, or require an explicit file to exist.",
    )

    conversation_files.add_argument(
        "-t",
        "--temp",
        action="store_true",
        help="Store a timestamp-named conversation in temporary storage; ignore --rename.",
    )

    settings.add_argument(
        "-o",
        "--override",
        action="append",
        default=[],
        metavar="TOML",
        help="Override settings for this invocation; repeat to recursively merge TOML fragments.",
    )

    settings.add_argument(
        "--env",
        help="""
            Specify an additional environment file to load. Note, %(prog)s will
            also look for a .env file and from $TEXTLLM_ENV_PATH.

            Useful for storing API keys""",
    )
    settings.add_argument(
        "--template",
        help="""
            Specify a template file or @name for new chats. Overrides $TEXTLLM_TEMPLATE_FILE,
            including values loaded via --env.
            """,
    )

    settings.add_argument(
        "--list-templates",
        action="count",
        default=0,
        help="List template names and exit. Repeat to include paths, sources, and shadowed entries.",
    )

    response.add_argument(
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

    response.add_argument(
        "--tokens",
        choices=["none", "stderr", "note"],
        default=None,
        help="""
            Where to report all provider-returned token usage fields. Defaults to
            [textllm].tokens, or 'none' when that setting is absent.
            """,
    )

    response.add_argument(
        "--header-level",
        type=int,
        choices=range(7),
        default=None,
        metavar="{0..6}",
        help="""
            Markdown heading level for newly written role markers. 0 writes plain
            separators. Defaults to [textllm].header_level, then
            $TEXTLLM_DEFAULT_HEADER_LEVEL, then 0.
            """,
    )

    response.add_argument(
        "--require-user-prompt",
        dest="require_user_prompt",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="""
            [%(default)s] Whether or not to require there be a user prompt at the end of
            the messages.
        """,
    )

    conversation_files.add_argument(
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

    edit = parser.add_argument_group(
        title="Prompt and Editor",
        description="""
            These options let you add the prompt and/or edit the file directly
            before calling the LLM. Note it is assumed that a User role marker
            is present (as it should be by default).
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

    verb = parser.add_argument_group("Verbosity")
    verb.add_argument(
        "-q", "--quiet", action="count", default=0, help="Decrease Verbosity"
    )
    verb.add_argument(
        "-v", "--verbose", action="count", default=0, help="Increase Verbosity"
    )

    args = parser.parse_args(argv)
    overrides = {}
    legacy_overrides = False
    for fragment in args.override:
        try:
            parsed_override = tomllib.loads(fragment)
            legacy_overrides |= config._has_legacy_settings(parsed_override)
            overrides = config._merge_settings(
                overrides, config._normalize_settings(parsed_override)
            )
        except ValueError as error:
            parser.error(f"Invalid --override TOML: {error}")
    local_overrides = overrides.get("textllm", {})
    if not isinstance(local_overrides, dict):
        parser.error("--override textllm must be a TOML table")
    template_header_level = args.header_level
    if template_header_level is None:
        template_header_level = local_overrides.get("header_level")
    if template_header_level is not None:
        try:
            config._header_prefix(template_header_level)
        except ValueError as error:
            parser.error(str(error))
    if args.temp and args.filepath is not None:
        parser.error("--temp cannot be combined with an explicit path")
    if args.continue_conversation and args.filepath and os.path.isdir(args.filepath):
        parser.error("--continue requires a file, not a directory")

    # Define logging levels
    levels = [logging.ERROR, logging.WARNING, logging.INFO, logging.DEBUG]
    level_index = args.verbose - args.quiet + 2  # +1: WARNING, +2: INFO
    level_index = max(0, min(level_index, len(levels) - 1))  # Always keep ERROR

    log.setLevel(logging.DEBUG)  # Highest. Handler will set lower
    fmt = logging.Formatter(
        "%(asctime)s:%(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    console_handler = ConsoleHandler()
    console_handler.setFormatter(fmt)
    console_handler.setLevel(levels[level_index])

    log.handlers.clear()
    log.addHandler(console_handler)
    if _test_mode_enabled() and not args.list_templates:
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

    if args.list_templates:
        status = _list_templates(details=args.list_templates >= 2)
        if status:
            sys.exit(status)
        return

    # Handle default --rename
    if args.rename is None:
        args.rename = not args.continue_conversation and (
            args.filepath is None or os.path.isdir(args.filepath)
        )
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
        new_temp = args.temp and not args.continue_conversation
        if args.continue_conversation:
            if args.filepath is None:
                args.filepath = _last_conversation("temp" if args.temp else "regular")
            elif not os.path.isfile(args.filepath):
                raise ValueError(f"Cannot continue missing file: {args.filepath!r}")
            log.info("Continuing %r", args.filepath)
        if new_temp:
            temp_timestamp = datetime.now(UTC)
            args.filepath = str(
                _temp_directory() / temp_timestamp.strftime("%Y%m%dT%H%M%S.%fZ.md")
            )
        elif args.filepath is None:
            args.filepath = uniqueify_filepath(DEFAULT_FILEPATH)
            log.debug(f"No filepath specified. Set {args.filepath!r}")
        elif os.path.isdir(args.filepath):
            fp = os.path.join(args.filepath, DEFAULT_FILEPATH)
            args.filepath = uniqueify_filepath(fp)
            log.debug(f"Directory specified. Set {args.filepath!r}")

        filepath = args.filepath
        kind = _conversation_kind(filepath)
        if kind == "temp":
            args.rename = False
        if new_temp or not os.path.exists(filepath):
            template_file = args.template or CONFIG.TEXTLLM_TEMPLATE_FILE
            template_text = (
                _read_template(template_file) if template_file else config.TEMPLATE
            )
            rendered_template = CONFIG.render_template(
                template_text, header_level=template_header_level
            )
            template_effective = config._merge_settings(
                config._normalize_settings(
                    Conversation.read_settings(rendered_template)
                ),
                overrides,
            )
            from .tools import mcp_config, tool_limits

            if edit_mode:
                # Creating a template alone must not require runtime variables.
                resolved = config._resolve_settings(template_effective)
                local = resolved.get("textllm", {})
                mcp_config(local.get("mcp", []), expand=False)
                tool_limits(local.get("tools", {}))
            if os.path.lexists(str(filepath) + ".db"):
                if not new_temp:
                    raise FileExistsError("New conversation has an existing sidecar")
            if template_header_level is None and "{header}" in template_text:
                template_settings = Conversation.read_settings(rendered_template).get(
                    "textllm", {}
                )
                if "header_level" in template_settings:
                    rendered_template = CONFIG.render_template(
                        template_text, header_level=template_settings["header_level"]
                    )
            if not template_file:
                header_level = (
                    template_header_level
                    if template_header_level is not None
                    else CONFIG.TEXTLLM_DEFAULT_HEADER_LEVEL
                )
                rendered_template = _set_role_marker_header_level(
                    rendered_template, header_level
                )
            if new_temp:
                _state_root().mkdir(mode=0o700, parents=True, exist_ok=True)
            Path(filepath).parent.mkdir(parents=True, exist_ok=True)
            while True:
                try:
                    if os.path.lexists(str(filepath) + ".db"):
                        raise FileExistsError(
                            "Temporary conversation sidecar collision"
                        )
                    with open(filepath, "xt") as fp:
                        fp.write(rendered_template)
                    break
                except FileExistsError:
                    if not new_temp:
                        raise
                    # A fixed or coarse clock must not prevent progress.
                    temp_timestamp = max(
                        datetime.now(UTC), temp_timestamp + timedelta(microseconds=1)
                    )
                    filepath = str(
                        _temp_directory()
                        / temp_timestamp.strftime("%Y%m%dT%H%M%S.%fZ.md")
                    )
            args.filepath = filepath
            log.info(f"{filepath!r} does not exist. Created template.")
            _remember_conversation(filepath, kind)

            if not edit_mode:
                return
        else:
            log.debug(f"{filepath!r} exists")

        if kind == "temp":
            log.info("Temporary conversation: %r", filepath)

        # Validate effective MCP settings and references before appending a CLI
        # prompt or invoking an editor on an existing conversation.
        validation_overrides = {
            "llm": {
                k: v
                for k, v in overrides.items()
                if k not in config._LOCAL_SETTINGS_TABLES
            },
            **{
                k: v for k, v in overrides.items() if k in config._LOCAL_SETTINGS_TABLES
            },
        }
        Conversation(filepath, overrides=validation_overrides)
        if edit_mode and not file_edit(filepath, prompt=args.prompt, editor=args.edit):
            # edit returns True iff it was modified.
            raise ValueError("File not modified")

        canonical_overrides = {
            "llm": {
                k: v
                for k, v in overrides.items()
                if k not in config._LOCAL_SETTINGS_TABLES
            },
            **{
                k: v for k, v in overrides.items() if k in config._LOCAL_SETTINGS_TABLES
            },
        }
        convo = Conversation(filepath, overrides=canonical_overrides)
        convo._legacy_overrides = legacy_overrides

        if args.title != "off":
            convo.set_title()  # Will do nothing if AUTO_TITLE not in the top line
        if args.title == "only":
            _remember_conversation(convo.filepath, kind)
            if _test_mode_enabled():
                return convo
            return

        convo.chat(
            require_user_prompt=args.require_user_prompt,
            tokens=args.tokens,
            header_level=args.header_level,
        )

        if args.rename:
            convo.rename_by_title()

        _remember_conversation(convo.filepath, kind)

        if _test_mode_enabled():
            return convo

    except Exception as E:
        log.error(E)
        if levels[level_index] == logging.DEBUG or _test_mode_enabled():
            raise
        sys.exit(1)
