#!/usr/bin/env python
# -*- coding: utf-8 -*-

import argparse
import itertools
import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import sys
import tomllib
from functools import cached_property
from pathlib import Path
from textwrap import dedent

from dotenv import load_dotenv  # pip install python-dotenv
from langchain.chat_models import init_chat_model
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    merge_message_runs,
)

__version__ = "0.0.4"

log = logging.getLogger("textllm")

# Environment variable configs for defaults
TEXTLLM_ENV_PATH = os.environ.get("TEXTLLM_ENV_PATH", None)
TEXTLLM_AUTO_RENAME = os.environ.get("TEXTLLM_AUTO_RENAME", "").lower() == "true"
TEXTLLM_STREAM = os.environ.get("TEXTLLM_STREAM", "").lower() == "true"
TEXTLLM_EDITOR = os.environ.get("TEXTLLM_EDITOR", os.environ.get("EDITOR", "vi"))

AUTO_TITLE = "!!AUTO TITLE!!"
TEMPLATE = f"""\
# {AUTO_TITLE}

```toml
# Optional Settings
# TOML Format
temperature = 0.5
model = "openai:gpt-4o"

# END Optional Settings
```

--- System ---

You are a helpful assistant. Provide clear and thorough answers but be concise unless instructed otherwise.

--- User ---

"""

TITLE_SYSTEM_PROMPT = """\
Provide an appropriate, consice, title for this conversation. The conversation is in JSON form with roles 'system' (or 'developer'), 'human', and 'ai'.

- Aim for fewer than 5 words but absolutely no more than 10.
- Give more influence to earlier messages than later.
- Be as concise as possible without losing the context of the conversation.
- Your goal is to extract the key point of the conversation
- Make sure the title is also appropriate for a filename. Spaces are acceptable.
- Reply with ONLY the title and nothing else!
"""

MAX_FILENAME_CHAR = 240

flag2role = {
    "--- system ---": SystemMessage,
    "--- user ---": HumanMessage,
    "--- assistant ---": AIMessage,
}

RETURN_AFTER_CLI_FOR_DEVEL = False


class Conversation:
    def __init__(self, filepath):

        if load_dotenv(TEXTLLM_ENV_PATH):
            # $TEXTLLM_ENV_PATH defaults to None to look in the parent dir.
            log.debug(f"Loaded env. ${TEXTLLM_ENV_PATH = }")
        else:
            log.debug(f"Could not load env. ${TEXTLLM_ENV_PATH = }")

        self.filepath = filepath

        # Read and truncate file. Do it now in case the title is updated
        with open(self.filepath, "rb+") as fp:
            content = fp.read().rstrip()
            self.text = content.decode("UTF-8")

            fp.seek(len(content), 0)
            fp.truncate()

        self.messages = self.read_conversation()

    def call_llm(self, messages, stream_model=False, **new_settings):
        settings = self.settings.copy() | new_settings
        log.debug(f"Settings {settings}")

        model = settings.pop("model")  # Will KeyError if not set as expected
        try:
            model_provider, model_name = model.split(":", 1)
        except ValueError:
            model_provider = None
            model_name = model
            log.debug(f"{model!r} does not contain a provider. Will try to infer")

        log.debug(f"{model_provider = } {model_name = }")

        chat_model = init_chat_model(
            model=model_name,
            model_provider=model_provider,
            **settings,
        )

        if stream_model:
            try:
                stream = chat_model.stream(messages, stream_usage=True)
                chunk = response = next(stream)
            except:
                stream = chat_model.stream(messages)
                chunk = response = next(stream)

            print("\n" + chunk.content, end="", flush=True)
            for chunk in stream:
                response += chunk
                print(chunk.content, end="", flush=True)
            print("\n\n", end="", flush=True)
        else:
            response = chat_model.invoke(messages)

        try:
            logtxt = (
                f"tokens: "
                f"prompt {response.usage_metadata['input_tokens']}, "
                f"completion {response.usage_metadata['output_tokens']}, "
                f"total {response.usage_metadata['total_tokens']}"
            )
            log.debug(logtxt)
        except:
            # The above seems to only work well with OpenAI.
            # ToDO: Fix this
            pass

        return response

    def chat(self, require_user_prompt=True, stream_model=False):
        if require_user_prompt and (
            not self.messages or not isinstance(self.messages[-1], HumanMessage)
        ):
            raise NoHumanMessageError("Must have a new user message")

        response = self.call_llm(messages=self.messages, stream_model=stream_model)

        # Not really needed but in case I do more with it later
        self.messages.append(response)

        # Add escapes to the content
        content = response.content
        pattern = re.compile(
            "(" + "|".join("^" + re.escape(flag) for flag in flag2role) + ")",
            flags=re.DOTALL | re.MULTILINE | re.IGNORECASE,
        )
        content = pattern.sub(r"\\\1", content)

        with open(self.filepath, "at") as fp:
            fp.write("\n\n--- Assistant ---\n\n")
            fp.write(content)
            fp.write("\n\n--- User ---\n\n")

        log.info(f"Updated {self.filepath!r}")

    def set_title(self):
        top, rest = self.text.split("\n", 1)
        if AUTO_TITLE not in top:
            log.debug(f"{AUTO_TITLE!r} not found in first line.")
            return  # This will happen nearly every time but the first

        messages = [(m.type, m.content) for m in self.messages]
        new = [
            SystemMessage(content=TITLE_SYSTEM_PROMPT),
            HumanMessage(content=json.dumps(messages)),
        ]

        response = self.call_llm(messages=new, temperature=0.1)
        title = response.content

        top = top.replace(AUTO_TITLE, title)
        self.text = f"{top}\n{rest}"
        with open(self.filepath, "wt") as fp:
            fp.write(self.text)
        log.info(f"Set title to {title!r}")

    @cached_property
    def settings(self):
        defaults = Conversation.read_settings(TEMPLATE)
        new = Conversation.read_settings(self.text)
        final = defaults | new
        return final

    @staticmethod
    def read_settings(text):

        pattern = re.compile(
            r"```toml\s*"
            r"# Optional Settings\s*"
            r"(.*?)"
            r"^# END Optional Settings\s*"
            r"```",
            flags=re.DOTALL | re.MULTILINE,
        )
        match = pattern.search(text)
        if match:
            toml_content = match.group(1).strip()
            return tomllib.loads(toml_content)  # Parse as TOML

        return {}

    def read_conversation(self):
        conversation = []

        pattern = re.compile(
            "(" + "|".join("^" + re.escape(flag) for flag in flag2role) + ")",
            flags=re.DOTALL | re.MULTILINE | re.IGNORECASE,
        )

        split_text = pattern.split(self.text)

        # Decide if the first item is a flag. It likely isn't but could be!
        if split_text[0].lower() not in flag2role:
            del split_text[0]

        for flag, msg in grouper(split_text, 2):
            msg = msg.strip()
            if not msg:
                continue  # Empty or blank

            # Clean up and unescape
            msg_lines = []
            for line in msg.strip().split("\n"):
                if any(line.lower().startswith(rf"\{flag}") for flag in flag2role):
                    line = line[1:]
                msg_lines.append(line)

            conversation.append(flag2role[flag.lower()](content="\n".join(msg_lines)))

        return merge_message_runs(conversation)

    def rename_by_title(self):
        dirname = os.path.dirname(self.filepath)

        # Clean the current for possible "<name> (n).<ext>"
        base, ext = os.path.splitext(self.filepath)
        cleaned_filepath = re.sub(r" \(\d+\)$", "", base) + ext
        cleaned_filename = os.path.basename(cleaned_filepath)
        log.debug(f"{cleaned_filename = }")

        # Compute the new name without worrying about duplicates
        title, *_ = self.text.split("\n", 1)

        if AUTO_TITLE in title:  # BEFORE cleaning it
            log.warning(f"{AUTO_TITLE!r} in title. Not renaming!")
            return

        # Sub unsafe or invalid characters
        invalid_chars = set(
            "\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b\x0c\r\x0e\x0f\x10\x11\x12\x13"
            '\x14\x15\x16\x17\x18\x19\x1a\x1b\x1c\x1d\x1e\x1f"*/:<>?\\|'
        )
        title = title.strip().strip("#").strip()
        title_based_filebase = "".join(c for c in title if c not in invalid_chars)
        title_based_filebase = title_based_filebase[: (MAX_FILENAME_CHAR - len(ext))]
        title_based_filename = title_based_filebase + ext
        title_based_filepath = os.path.join(dirname, title_based_filename)
        log.debug(f"{title_based_filename = }")
        if cleaned_filename == title_based_filename:
            log.debug("Already named by title. No action needed")
            return

        # Ensure it is unique by added " (n)" up to 99
        c = 0
        while os.path.exists(title_based_filepath):
            c += 1
            if c >= 100:
                raise ValueError(f"Too many for {title_based_filebase + ext!r}")

            new = f"{title_based_filebase} ({c}){ext}"
            title_based_filepath = os.path.join(dirname, new)
        log.debug(f"Required {c} iterations for unique name")

        shutil.move(self.filepath, title_based_filepath)
        log.info(f"Rename by title {self.filepath!r} --> {title_based_filepath!r}")
        self.filepath = title_based_filepath


def file_edit(filepath, *, prompt, editor):
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
        subprocess.check_call(shlex.split(TEXTLLM_EDITOR) + [filepath])

    size1 = os.path.getsize(filepath)
    mtime1 = os.path.getmtime(filepath)
    if size1 == size0 and abs(mtime1 - mtime0) <= 0.5:
        return False
    return True


def grouper(iterable, n, *, fillvalue=None):
    iterators = [iter(iterable)] * n
    return itertools.zip_longest(*iterators, fillvalue="")


class NoHumanMessageError(ValueError):
    """Error when a conversation doesn't end with a HumanMessage"""


def cli(argv=None):

    parser = argparse.ArgumentParser(
        description="Simple LLM interface that reads and writes to a text file",
        epilog="See readme.md for details on format description",
        # formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "conversation",
        help="""
            Input file in the noted format. If it does not exists, the template will
            instead be written there (unless --no-create)
            """,
    )

    parser.add_argument(
        "--create",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Whether or not to create a file with a template if no file exists",
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
        "--u",  # To make --no-u an easy option
        "--require-user-prompt",
        dest="require_user_prompt",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="""
            Whether or not to require there be a user prompt at the end of 
            the messages. Default %(default)s
        """,
    )

    parser.add_argument(
        "--rename",
        "--move",
        action=argparse.BooleanOptionalAction,
        default=TEXTLLM_AUTO_RENAME,
        help=f"""
            Rename the file based on the title. The title must NOT have {AUTO_TITLE!r}
            in the title. Note that the automatic title generation will happen first if 
            set. Will increment the file if one already exists. Default is based
            on environment variable whether $TEXTLLM_AUTO_RENAME == "true". Currently 
            %(default)s
        """,
    )

    parser.add_argument(
        "--stream",
        action=argparse.BooleanOptionalAction,
        default=TEXTLLM_STREAM,
        help=f"""
            Whether or not to stream the model response to stdout in addition to
            writing it to file. Default is based on environment variable whether $TEXTLLM_STREAM == "true". Currently %(default)s
        """,
    )

    parser.add_argument(
        "--version",
        action="version",
        version="%(prog)s-" + __version__,
    )

    verb = parser.add_argument_group("Verbosity Settings:")
    verb.add_argument(
        "-s", "--silent", action="count", default=0, help="Decrease Verbosity"
    )
    verb.add_argument(
        "-v", "--verbose", action="count", default=0, help="Increase Verbosity"
    )

    edit = parser.add_argument_group(
        title="Edit Settings",
        description="""
            These options let you add the prompt and/or edit the file directly
            before calling the LLM. Note it is assumed that a '--- User ---'
            heading is present (as it should be by default). Either of these settings will
            force --create.""",
    )
    edit.add_argument(
        "--prompt",
        metavar="text",
        default="",
        help="Prompt text to add. If combined with --edit, this will be added first. Specify as '-' to read stdin.",
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
    level_index = args.verbose - args.silent + 2  # +1: WARNING, +2: INFO
    level_index = max(0, min(level_index, len(levels) - 1))  # Always keep ERROR

    log.setLevel(levels[level_index])

    console_handler = logging.StreamHandler()
    fmt = logging.Formatter(
        "%(asctime)s:%(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    console_handler.setFormatter(fmt)
    log.addHandler(console_handler)

    log.debug(f"argv: {sys.argv[1:]}")
    log.debug(f"{args = }")

    filepath = args.conversation

    # Hanle edit modes.
    args.prompt = args.prompt.strip()
    if args.prompt == "-":
        log.debug("reading stdin")
        args.prompt = sys.stdin.read().strip()
    edit_mode = bool(args.edit or args.prompt)
    log.debug(f"{edit_mode = }")
    if edit_mode:
        log.debug("setting --create")
        args.create = True

    try:
        if not os.path.exists(filepath):
            if not args.create:
                raise ValueError(f"{filepath!r} does not exist. Exit")

            Path(filepath).parent.mkdir(parents=True, exist_ok=True)
            with open(filepath, "xt") as fp:
                fp.write(TEMPLATE)
            log.info(f"{filepath!r} does not exist. Created template.")

            if not edit_mode:
                sys.exit()
        else:
            log.debug(f"{filepath!r} exists")

        if edit_mode and not file_edit(filepath, prompt=args.prompt, editor=args.edit):
            # edit returns True iff it was modified.
            log.info("File not modified. Exit")
            sys.exit(1)

        convo = Conversation(filepath)

        if args.title != "off":
            convo.set_title()  # Will do nothing if AUTO_TITLE not in the top line
        if args.title == "only":
            return convo

        convo.chat(
            require_user_prompt=args.require_user_prompt,
            stream_model=args.stream,
        )

        if args.rename:
            convo.rename_by_title()

        if RETURN_AFTER_CLI_FOR_DEVEL:
            return convo

    except Exception as E:
        log.error(E)
        if levels[level_index] == logging.DEBUG:
            raise


if __name__ == "__main__":
    cli()
