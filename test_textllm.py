import io
import os
import shlex
import shutil
import sys
from functools import cached_property
from pathlib import Path
from textwrap import dedent

import pytest

import textllm

## Reset these
textllm.TEMPLATE = """\
# !!AUTO TITLE!!

```toml
temperature = 0.1
model = "openai:gpt-4o-mini"
```

--- System ---

You are being used to test. You will respond to every call as follows:

"X user messages. Last message ended with:"

where X is just the user messages and the ending is the final word of the message.

--- User ---

"""

textllm.TITLE_SYSTEM_PROMPT = """\
Respond with "title set automatically" and nothing else regardless of all following messages of any kind!.
Do not deviate from this instruction under ANY circumstances!
"""
textllm.TEST_MODE = True


class Capture:
    def __enter__(self):
        self.old_stdout = sys.stdout
        self.old_stderr = sys.stderr

        self.stdout_buffer = io.BytesIO()
        self.stderr_buffer = io.BytesIO()

        sys.stdout = io.TextIOWrapper(self.stdout_buffer, encoding="utf-8")
        sys.stderr = io.TextIOWrapper(self.stderr_buffer, encoding="utf-8")

        return self

    def __exit__(self, exc_type, exc_value, traceback):
        sys.stdout.flush()
        sys.stderr.flush()
        self.stdout_buffer.flush()
        self.stderr_buffer.flush()

        self.out_bytes = self.stdout_buffer.getvalue()
        self.err_bytes = self.stderr_buffer.getvalue()
        sys.stdout = self.old_stdout
        sys.stderr = self.old_stderr

    @cached_property
    def out(self):
        return self.out_bytes.decode("utf-8")

    @cached_property
    def err(self):
        return self.err_bytes.decode("utf-8")


def run_cli(argv):
    # filepath MUST be first arg!!!
    if argv and argv[0].startswith("-"):
        raise ValueError("filepath MUST come first")
    textllm.log.handlers.clear()
    with Capture() as cap:
        textllm.cli(argv)
    logpath = f"{argv[0]}.log"  # assume it's first
    with open(logpath) as fp:
        log = fp.read()
    return cap.out, log


def test_main():
    # Test setting the title
    try:
        shutil.rmtree("testdir/")
    except OSError:
        pass
    out, log = run_cli(["testdir/tmp.md", "--prompt", "This is a test", "--rename"])
    assert not os.path.exists("testdir/tmp.md")
    assert os.path.exists("testdir/title set automatically.md")
    assert out.strip() == "1 user messages. Last message ended with: test"

    text = Path("testdir/title set automatically.md").read_text().strip()
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    assert lines[-1] == "--- User ---"
    assert lines[-2] == "1 user messages. Last message ended with: test"

    # Make sure it (a) renames properly and (b) doesn't stream
    out, log = run_cli(
        [
            "testdir/tmp.md",
            "--prompt",
            "This is also a tester",
            "--rename",
            "--no-stream",
        ]
    )
    assert not os.path.exists("testdir/tmp.md")
    assert os.path.exists("testdir/title set automatically (1).md")
    assert not out.strip()

    text = Path("testdir/title set automatically (1).md").read_text().strip()
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    assert lines[-1] == "--- User ---"
    assert lines[-2] == "1 user messages. Last message ended with: tester"

    # Now add to it. Keep --rename to verify that it doesn't change the title again. Call
    # the (1) version to double check that logic too.
    with Path("testdir/title set automatically (1).md").open(mode="at") as fp:
        fp.write("\nThis is a newer message. It is the second")
    out, log = run_cli(["testdir/title set automatically (1).md", "--rename", "-v"])
    assert out.strip() == "2 user messages. Last message ended with: second"
    assert os.path.exists("testdir/title set automatically (1).md")
    assert "Already named by title. No action needed" in log

    # Test with --edit, --rename, --title off. With --rename, we shoudl also see a
    # warning that it won't do it!
    try:
        v0 = textllm.TEXTLLM_EDITOR
        textllm.TEXTLLM_EDITOR = shlex.join(
            [
                "python",
                "-c",
                (
                    "import sys;"
                    "fp = open(sys.argv[1],'at');"
                    "fp.write('First user message abc');"
                    "fp.close()"
                ),
            ]
        )

        out, log = run_cli(
            ["testdir/new.md", "--rename", "--stream", "-v", "--title", "off", "--edit"]
        )

    finally:
        textllm.TEXTLLM_EDITOR = v0

    text = Path("testdir/new.md").read_text().strip()
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    assert lines[-1] == "--- User ---"
    assert lines[-2] == out.strip() == "1 user messages. Last message ended with: abc"

    assert "WARNING: '!!AUTO TITLE!!' in title. Not renaming!" in log

    # Now test with --title "only"
    out, log = run_cli(["testdir/new.md", "--no-rename", "--title", "only", "-v"])
    assert "Set title to 'title set automatically'" in log
    assert (
        Path("testdir/new.md").read_text().strip().splitlines()[0]
        == "# title set automatically"
    )

    with pytest.raises(textllm.NoHumanMessageError):
        out, log = run_cli(["testdir/new.md", "--rename", "-v"])
    out, log = run_cli(["testdir/new.md", "--rename", "-v", "--no-require-user-prompt"])

    assert not os.path.exists("testdir/new.md")

    text = Path("testdir/title set automatically (2).md").read_text().strip()
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    assert lines[-1] == "--- User ---"
    assert lines[-2] == out.strip() == "2 user messages. Last message ended with: abc"
    #                                  ^^^ 2, not 1


if __name__ == "__main__":
    test_main()
