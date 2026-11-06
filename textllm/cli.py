"""Command-line argument handling and local conversation orchestration."""

import argparse
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

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
            Specify a template file or @name for new chats. Overrides $TEXTLLM_TEMPLATE_FILE,
            including values loaded via --env.
            """,
    )

    parser.add_argument(
        "--list-templates",
        action="store_true",
        help="List named templates, their paths and sources, then exit.",
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
        status = _list_templates()
        if status:
            sys.exit(status)
        return

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
            template_file = args.template or CONFIG.TEXTLLM_TEMPLATE_FILE
            template_text = (
                _read_template(template_file) if template_file else config.TEMPLATE
            )
            rendered_template = CONFIG.render_template(
                template_text, header_level=args.header_level
            )
            if args.header_level is None and "{header}" in template_text:
                template_settings = Conversation.read_settings(rendered_template).get(
                    "textllm", {}
                )
                if "header_level" in template_settings:
                    rendered_template = CONFIG.render_template(
                        template_text, header_level=template_settings["header_level"]
                    )
            if not template_file:
                header_level = (
                    args.header_level
                    if args.header_level is not None
                    else CONFIG.TEXTLLM_DEFAULT_HEADER_LEVEL
                )
                rendered_template = _set_role_marker_header_level(
                    rendered_template, header_level
                )
            Path(filepath).parent.mkdir(parents=True, exist_ok=True)
            with open(filepath, "xt") as fp:
                fp.write(rendered_template)
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

        convo.chat(
            require_user_prompt=args.require_user_prompt,
            tokens=args.tokens,
            header_level=args.header_level,
        )

        if args.rename:
            convo.rename_by_title()

        if _test_mode_enabled():
            return convo

    except Exception as E:
        log.error(E)
        if levels[level_index] == logging.DEBUG or _test_mode_enabled():
            raise
        sys.exit(1)
