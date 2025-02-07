import base64
import io
import os
import shlex
import shutil
import subprocess
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
temperature = 0.01
model = "openai:gpt-4o-mini"
```

--- System ---

You are being used to test. You will respond to every call as follows:

"X user messages. Last message ended with:"

where X is just the user messages and the ending is the final word of the message. 
Do NOT include final punctuation.

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
        logpath = "log"
    else:
        logpath = f"{argv[0]}.log" if argv else "log"  # assume it's first

    textllm.log.handlers.clear()
    with Capture() as cap:
        textllm.cli(argv)

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


def clean():
    for item in Path(".").glob("New Conversation*.md"):
        item.unlink()
    for item in Path(".").glob("title set automatically*.md"):
        item.unlink()
    try:
        shutil.rmtree("testdir/")
    except OSError:
        pass


def test_auto_names():

    clean()
    os.makedirs("testdir")

    try:
        textllm.cli([])
        assert os.path.exists("New Conversation.md")

        out, log = run_cli([])
        assert os.path.exists("New Conversation.md")
        assert os.path.exists("New Conversation (1).md")

        out, log = run_cli([])
        assert os.path.exists("New Conversation.md")
        assert os.path.exists("New Conversation (1).md")
        assert os.path.exists("New Conversation (2).md")

        out, log = run_cli(["testdir"])
        assert os.path.exists("testdir/New Conversation.md")

        out, log = run_cli(["testdir"])
        assert os.path.exists("testdir/New Conversation.md")
        assert os.path.exists("testdir/New Conversation (1).md")

        out, log = run_cli(["testdir/create/this/file.md"])
        assert os.path.exists("testdir/create/this/file.md")

        out, log = run_cli(["--prompt", "What time is it"])
        text = Path("title set automatically.md").read_text().strip()
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        assert (
            out.strip() == lines[-2] == "1 user messages. Last message ended with: it"
        )

        out, log = run_cli(["--prompt", "How are you"])
        text = Path("title set automatically (1).md").read_text().strip()
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        assert (
            out.strip() == lines[-2] == "1 user messages. Last message ended with: you"
        )

        out, log = run_cli(["--prompt", "Where do I go", "testdir"])
        text = Path("testdir/title set automatically.md").read_text().strip()
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        assert (
            out.strip() == lines[-2] == "1 user messages. Last message ended with: go"
        )

        out, log = run_cli(["--prompt", "look up", "testdir"])
        text = Path("testdir/title set automatically (1).md").read_text().strip()
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        assert (
            out.strip() == lines[-2] == "1 user messages. Last message ended with: up"
        )

        # Make sure it doesn't create new on a specified file
        out, log = run_cli(
            ["--prompt", "hello there", "testdir/title set automatically (1).md"]
        )
        text = Path("testdir/title set automatically (1).md").read_text().strip()
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        assert (
            out.strip()
            == lines[-2]
            == "2 user messages. Last message ended with: there"
        )

        # Other flags
        out, log = run_cli(["--prompt", "look down", "--no-rename"])
        text = Path("New Conversation (3).md").read_text().strip()
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        assert (
            out.strip() == lines[-2] == "1 user messages. Last message ended with: down"
        )

        out, log = run_cli(["--prompt", "look left", "--title", "off"])
        text = Path("New Conversation (4).md").read_text().strip()
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        assert (
            out.strip() == lines[-2] == "1 user messages. Last message ended with: left"
        )
        assert "WARNING: '!!AUTO TITLE!!' in title. Not renaming!" in log

    finally:
        clean()


def test_default_settings():
    """
    For these, we test with calling a different process. Ideally, we'd just call the
    function (to get line coverage credit) but (a) it won't matter for the defaults
    and (b) the global structure of them makes this more of a pain.
    """
    clean()
    try:
        os.makedirs("testdir")

        env = os.environ.copy()
        env["TEXTLLM_DEFAULT_MODEL"] = "this is made up"
        env["TEXTLLM_DEFAULT_TEMPERATURE"] = "0.001"

        subprocess.call([sys.executable, "textllm.py", "testdir"], env=env)
        with open("testdir/New Conversation.md") as fp:
            text = fp.read()
        assert "model = 'this is made up'" in text
        assert "temperature = 0.001" in text

        # Even though we set the template above to openai:gpt-4o-mini, it won't carry
        # in a subprocess-based test since it will reload textllm hardcoded defaults.
        # Therefore, we test it by setting it here and not in the template
        env["TEXTLLM_DEFAULT_MODEL"] = "openai:gpt-4o-mini"
        env["TEXTLLM_TEMPLATE_FILE"] = "testdir/template.md"
        with open("testdir/template.md", "wt") as fp:
            fp.write(
                dedent(
                    """
                # no title
                ```toml
                temperature = 0.123
                # asfdasdf3. 
                ```
                --- System ---
                Respond with nothing but "hello" (without quotes). DO NOT DEVIATE from this
                --- User ---
                
                """
                )
            )
        subprocess.call([sys.executable, "textllm.py", "testdir/new.md"], env=env)
        with open("testdir/new.md", "rt") as fp:
            template = fp.read()
        assert "temperature = 0.123" in template
        assert "asfdasdf3" in template
        proc = subprocess.run(
            [
                sys.executable,
                "textllm.py",
                "testdir/new.md",
                "--prompt",
                "say 'goodbye'",
                "-v",
            ],
            capture_output=True,
            env=env,
        )
        assert proc.stdout.decode().strip() == "hello", "did not get new system command"
        assert "openai:gpt-4o-mini" in proc.stderr.decode()
    finally:
        clean()


def test_images():
    """
    Test different image calling. filenames are flipped to make sure we don't get a false
    positive
    """
    clean()

    shutil.copytree("testresources/img", "testdir")

    shutil.copy2("testdir/template.md", "testdir/one.md")
    with open("testdir/one.md", "at") as fp:
        fp.write(
            dedent(
                """
            ![backup](traeh.png "My Title")
            Describe this image
            """
            )
        )

    out, log = run_cli(["testdir/one.md"])
    assert "heart" in out.lower()
    assert "Found image 'testdir/traeh.png', 3405 bytes" in log  # Path relative to exe

    shutil.copy2("testdir/template.md", "testdir/two.md")
    with open("testdir/two.md", "at") as fp:
        fp.write(
            dedent(
                """
            Describe this image in ONE WORD:
            ![](worra.jpg)
            
            Describe this image in two words:
            ![backup](traeh.png "My Title")
            
            How many images were there?
            """
            )
        )

    out, log = run_cli(["testdir/two.md"])
    #     assert "heart" in out.lower()
    #     assert "arrow" in out.lower()
    #     assert "two" in out.lower() or "2" in out.lower()
    print("#" * 40 + f"\nMANUAL: {out}\n")

    # Test context
    out, log = run_cli(["testdir/two.md", "--prompt", "what color are they?"])
    assert "blue" in out.lower()
    assert "black" in out.lower()

    # Test embedded images
    shutil.copy2("testdir/template.md", "testdir/three.md")
    with open("testdir/three.md", "at") as fp, open("testdir/worra.jpg", "rb") as img:
        data = img.read()
        bdata = base64.b64encode(data).decode()
        fp.write(
            dedent(
                f"""
                Describe this image. Make sure to include the color.
                
                ![Alt text](data:image/jpg;base64,{bdata})
                """
            )
        )
    out, log = run_cli(["testdir/three.md"])
    assert "blue" in out.lower()
    assert "Found 'data:<...>' URL" in log

    clean()


if __name__ == "__main__":
    # test_main()
    # test_auto_names()
    # test_default_settings()
    test_images()
