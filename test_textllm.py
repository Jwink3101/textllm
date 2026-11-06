import base64
import importlib
import io
import json
import os
import shlex
import shutil
import subprocess
import sys
import types
from functools import cached_property
from pathlib import Path
from textwrap import dedent

import pytest

import textllm
from textllm import backends, config, conversation

cli_module = importlib.import_module("textllm.cli")
LOCAL_LAUNCHER = Path(__file__).resolve().with_name("textllm.py")

## Reset these
os.environ["TEXTLLM_TEST_MODE"] = "1"
os.environ["TEXTLLM_DEFAULT_TEMPERATURE"] = "0.01"
config.TEMPLATE = """\
# !!AUTO TITLE!!

```toml
temperature = {temperature}
model = "openai/gpt-4o-mini"
```

--- System ---

You are being used to test. You will respond to every call as follows:

"X user messages. Last message ended with:"

where X is just the user messages and the ending is the final word of the message. 
Do NOT include final punctuation.

--- User ---

"""

conversation.TITLE_SYSTEM_PROMPT = """\
Respond with "title set automatically" and nothing else regardless of all following messages of any kind!.
Do not deviate from this instruction under ANY circumstances!
"""
config.TEST_MODE = True


def test_template_literal_placeholders(monkeypatch):
    monkeypatch.setenv("TEXTLLM_DEFAULT_TEMPERATURE", "0.25")
    monkeypatch.setenv("TEXTLLM_DEFAULT_MODEL", "example/model")
    template = 'temperature = {temperature} # {{temperature}}\nmodel = {model!r}'
    assert config.CONFIG.render_template(template) == (
        'temperature = 0.25 # {temperature}\nmodel = \'example/model\''
    )
    assert config.CONFIG.render_template(
        "{{model!r}} {{header}} {unknown} {{unknown}}"
    ) == "{model!r} {header} {unknown} {{unknown}}"


def test_package_public_api():
    from textllm.files import file_edit
    from textllm.markdown import loads

    assert textllm.Conversation is conversation.Conversation
    assert textllm.ModelEvent is backends.ModelEvent
    assert textllm.iter_model_events is backends.iter_model_events
    assert textllm.file_edit is file_edit
    assert textllm.loads is loads
    assert textllm.NoHumanMessageError is textllm.NoUserMessageError
    assert callable(textllm.cli)


@pytest.mark.parametrize("symlink", [False, True])
def test_local_launcher_uses_checkout(tmp_path, symlink):
    # A competing installation on the import path must never win over the
    # launcher's adjacent package, even from another working directory.
    shadow = tmp_path / "other installation"
    (shadow / "textllm").mkdir(parents=True)
    (shadow / "textllm" / "__init__.py").write_text(
        'raise RuntimeError("wrong textllm installation")\n'
    )
    launcher = LOCAL_LAUNCHER
    if symlink:
        launcher = tmp_path / "local launcher & link.py"
        launcher.symlink_to(LOCAL_LAUNCHER)
    target = tmp_path / "conversation & notes.md"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(shadow)
    env["PYTHON_DOTENV_DISABLED"] = "1"
    env["TEXTLLM_TEST_MODE"] = "1"
    env.pop("TEXTLLM_TEMPLATE_FILE", None)
    proc = subprocess.run(
        [
            sys.executable,
            str(launcher),
            str(target),
            "--title",
            "off",
            "--prompt",
            "Local checkout works",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert "Last message ended with: works" in proc.stdout
    assert "--- Assistant ---" in target.read_text()


def test_module_entry_point():
    env = os.environ.copy()
    env["TEXTLLM_TEST_MODE"] = "1"
    proc = subprocess.run(
        [sys.executable, "-m", "textllm", "--help"],
        cwd=LOCAL_LAUNCHER.parent,
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert "--list-templates" in proc.stdout


def test_package_wins_over_launcher():
    # Check fresh import resolution without relying on pytest's cached imports.
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "from pathlib import Path; import textllm; "
            "import importlib; "
            "assert Path(textllm.__file__).resolve() == "
            "Path('textllm/__init__.py').resolve(); "
            "assert textllm.__path__; "
            "assert importlib.import_module('textllm.cli').cli is textllm.cli",
        ],
        cwd=LOCAL_LAUNCHER.parent,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr


def test_importing_launcher_has_no_side_effects():
    original_path = sys.path.copy()
    spec = importlib.util.spec_from_file_location("local_launcher", LOCAL_LAUNCHER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert sys.path == original_path


def test_detached_launcher_does_not_use_installed_version(tmp_path):
    launcher = tmp_path / "textllm.py"
    shutil.copy2(LOCAL_LAUNCHER, launcher)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(LOCAL_LAUNCHER.parent)
    proc = subprocess.run(
        [sys.executable, str(launcher), "--help"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0
    assert "requires the adjacent textllm package" in proc.stderr
    assert "Traceback" not in proc.stderr


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

    # Make sure it renames properly. Streaming is now always on.
    out, log = run_cli(
        [
            "testdir/tmp.md",
            "--prompt",
            "This is also a tester",
            "--rename",
        ]
    )
    # breakpoint()
    assert not os.path.exists("testdir/tmp.md")
    assert os.path.exists("testdir/title set automatically (1).md")
    assert out.strip() == "1 user messages. Last message ended with: tester"

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
        v0 = os.environ["TEXTLLM_EDITOR"]
        os.environ["TEXTLLM_EDITOR"] = shlex.join(
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
            ["testdir/new.md", "--rename", "-v", "--title", "off", "--edit"]
        )

    finally:
        os.environ["TEXTLLM_EDITOR"] = v0

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

    with pytest.raises(textllm.NoUserMessageError):
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

    try:
        os.unlink(".env")
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

        # Even though we set the template above to openai/gpt-4o-mini, it won't carry
        # in a subprocess-based test since it will reload textllm hardcoded defaults.
        # Therefore, we test it by setting it here and not in the template
        env["TEXTLLM_DEFAULT_MODEL"] = "openai/gpt-4o-mini"
        env["TEXTLLM_TEMPLATE_FILE"] = "testdir/template.md"
        with open("testdir/template.md", "wt") as fp:
            fp.write(dedent("""
                # no title
                ```toml
                model = "{model}"
                temperature = {temperature}
                # asfdasdf3. 
                ```
                Created with {version} at {now}
                --- System ---
                Respond with nothing but "hello" (without quotes). DO NOT DEVIATE from this
                --- User ---
                
                """))
        subprocess.call([sys.executable, "textllm.py", "testdir/new.md"], env=env)
        with open("testdir/new.md", "rt") as fp:
            template = fp.read()
        assert 'model = "openai/gpt-4o-mini"' in template
        assert "temperature = 0.001" in template
        assert "Created with textllm-" in template
        assert " at 20" in template
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
        assert "openai/gpt-4o-mini" in proc.stderr.decode()
    finally:
        clean()


def test_images():
    """
    Test different image calling. filenames are flipped to make sure we don't get a false
    positive
    """
    clean()


def test_attachments():
    clean()
    try:
        os.makedirs("testdir/docs")
        Path("testdir/docs/q3-report.pdf").write_bytes(b"fake PDF")
        Path("testdir/docs/notes.txt").write_text("Included note.")
        Path("testdir/docs/notes with spaces.txt").write_text("Spaced note.")
        Path("testdir/docs/report (final).pdf").write_bytes(b"final PDF")
        Path("testdir/attachments.md").write_text(textllm.CONFIG.TEMPLATE + dedent("""
                Compare this with last quarter.

                [@attachment Q3 financial report][q3-report]

                [q3-report]: docs/q3-report.pdf

                [@include analyst notes](docs/notes.txt)
                """))

        convo = textllm.Conversation("testdir/attachments.md")
        messages = convo.process_conversation()
        content = messages[-1]["content"]
        assert content[0]["type"] == "text"
        assert "Q3 financial report" in content[0]["text"]
        assert "analyst notes" in content[0]["text"]
        assert (
            "--- Included file: notes.txt ---\n\nIncluded note." in content[0]["text"]
        )
        assert content[1]["type"] == "file"
        assert content[1]["file"]["filename"] == "q3-report.pdf"
        assert content[1]["file"]["file_data"].startswith(
            "data:application/pdf;base64,"
        )

        Path("testdir/inline.md").write_text(
            "--- User ---\n\nRead [@include notes](docs/notes.txt) carefully.\n"
        )
        inline_text = textllm.Conversation("testdir/inline.md").messages[0]["content"][
            0
        ]["text"]
        assert "Read notes carefully." in inline_text
        assert "--- Included file: notes.txt ---\n\nIncluded note." in inline_text

        Path("testdir/spaced-inline.md").write_text(
            "--- User ---\n\nRead [@include notes](docs/notes with spaces.txt) carefully.\n"
        )
        spaced_text = textllm.Conversation("testdir/spaced-inline.md").messages[0][
            "content"
        ][0]["text"]
        assert (
            "--- Included file: notes with spaces.txt ---\n\nSpaced note."
            in spaced_text
        )

        Path("testdir/parentheses.md").write_text(
            "--- User ---\n\nRead [@attach final report](docs/report (final).pdf).\n"
        )
        parentheses_content = textllm.Conversation("testdir/parentheses.md").messages[
            0
        ]["content"]
        assert parentheses_content[0]["text"] == "Read final report."
        assert parentheses_content[1]["file"]["filename"] == "report (final).pdf"

        Path("testdir/missing-inline.md").write_text(
            "--- User ---\n\nRead [@include missing](docs/missing.txt).\n"
        )
        with pytest.raises(FileNotFoundError):
            textllm.Conversation("testdir/missing-inline.md")

        out, log = run_cli(["testdir/attachments.md"])
        assert "saw 1 attachments" in out.lower()
        assert (
            "Found attachment reference definition 'q3-report' -> 'docs/q3-report.pdf'"
            in log
        )
        assert (
            "Resolved @attachment reference 'q3-report' to target 'docs/q3-report.pdf'"
            in log
        )
        assert (
            "Loading attachment 'testdir/docs/q3-report.pdf' with MIME type application/pdf"
            in log
        )
        assert "Found attachment 'testdir/docs/q3-report.pdf', 8 bytes" in log
        assert (
            "Including text attachment 'testdir/docs/notes.txt' with MIME type text/plain"
            in log
        )

        out, _ = run_cli(["testdir/attachments.md", "--prompt", "compare it again"])
        assert "saw 1 attachments" in out.lower()
        assert "2 user messages" in out.lower()
    finally:
        clean()

    shutil.copytree("testresources/img", "testdir")

    shutil.copy2("testdir/template.md", "testdir/one.md")
    with open("testdir/one.md", "at") as fp:
        fp.write(dedent("""
            ![backup](traeh.png "My Title")
            Describe this image
            """))

    out, log = run_cli(["testdir/one.md"])
    assert "saw 1 images" in out.lower()
    assert "Found image 'testdir/traeh.png', 3405 bytes" in log  # Path relative to exe

    shutil.copy2("testdir/template.md", "testdir/two.md")
    with open("testdir/two.md", "at") as fp:
        fp.write(dedent("""
            Describe this image in ONE WORD:
            ![](worra.jpg)
            
            Describe this image in two words:
            ![backup](traeh.png "My Title")
            
            How many images were there?
            """))

    out, log = run_cli(["testdir/two.md"])
    assert "saw 2 images" in out.lower()

    # Test context
    out, log = run_cli(["testdir/two.md", "--prompt", "what color are they?"])
    assert "saw 2 images" in out.lower()
    assert "2 user messages" in out.lower()

    # Test embedded images
    shutil.copy2("testdir/template.md", "testdir/three.md")
    with open("testdir/three.md", "at") as fp, open("testdir/worra.jpg", "rb") as img:
        data = img.read()
        bdata = base64.b64encode(data).decode()
        fp.write(dedent(f"""
                Describe this image. Make sure to include the color.
                
                ![Alt text](data:image/jpg;base64,{bdata})
                """))
    out, log = run_cli(["testdir/three.md"])
    assert "saw 1 images" in out.lower()
    assert "Found 'data:<...>' URL" in log

    clean()


def test_developer_role_and_adjacent_merge():
    clean()
    try:
        os.makedirs("testdir")
        Path("testdir/dev.md").write_text(dedent("""
                # dev role

                ```toml
                model = "openai/gpt-4o-mini"
                ```

                --- System ---

                System prompt

                --- Developer ---

                Developer prompt

                --- User ---

                First message

                --- User ---

                Second message
                """).strip())

        convo = textllm.Conversation("testdir/dev.md")
        assert [msg["role"] for msg in convo.messages] == [
            "system",
            "developer",
            "user",
        ]
        assert convo.messages[-1]["content"] == "First message\n\nSecond message"

        out, log = run_cli(["testdir/dev.md"])
        assert out.strip() == "1 user messages. Last message ended with: message"
    finally:
        clean()


def test_local_only_blocks_are_never_sent_to_model(monkeypatch):
    clean()
    try:
        os.makedirs("testdir")
        path = Path("testdir/note.md")
        path.write_text(dedent("""
                # !!AUTO TITLE!!

                ```toml
                model = "openai/gpt-4o-mini"
                ```

                --- System ---

                System prompt

                --- Note ---

                private title note

                --- User ---

                User prompt

                --- Note ---

                private chat note

                --- Reasoning ---

                private model reasoning
                """).strip())

        convo = textllm.Conversation(path)
        assert [item["role"] for item in convo.parsed["conversation"]] == [
            "system",
            "note",
            "user",
            "note",
            "reasoning",
        ]
        assert [message["role"] for message in convo.messages] == ["system", "user"]
        assert "private" not in repr(convo.messages)

        calls = []

        def fake_stream(*, model, messages, settings):
            calls.append(messages)
            yield "response", None

        monkeypatch.setattr(backends, "iter_completion_text", fake_stream)
        convo.call_llm(
            convo.messages
            + [
                {"role": "note", "content": "direct private note"},
                {"role": "reasoning", "content": "direct private reasoning"},
            ]
        )
        assert [message["role"] for message in calls[-1]] == ["system", "user"]
        assert "private" not in repr(calls[-1])

        monkeypatch.setattr(conversation, "_test_mode_enabled", lambda: False)
        convo.set_title()
        assert calls[-1] == [
            {"role": "system", "content": conversation.TITLE_SYSTEM_PROMPT},
            {"role": "user", "content": "User prompt"},
            {"role": "user", "content": "Generate a title for the conversation above."},
        ]
        assert "private" not in repr(calls[-1])
    finally:
        clean()


def test_title_uses_native_history_and_replaces_original_instructions(tmp_path, monkeypatch):
    path = tmp_path / "conversation.md"
    path.write_text("# !!AUTO TITLE!!\n\n--- User ---\n\nQuestion")
    convo = textllm.Conversation(path)
    history = [
        {"role": "system", "content": "Original system instructions"},
        {"role": "developer", "content": "Original developer instructions"},
        {"role": "user", "content": "First question"},
        {"role": "assistant", "content": "First answer"},
        {"role": "user", "content": "Follow-up question"},
        {"role": "assistant", "content": "Follow-up answer"},
    ]
    convo.parsed["conversation"] = history
    calls = []

    def fake_call(*, messages, title_call):
        calls.append((messages, title_call))
        return backends.LLMResponse("Conversation title")

    monkeypatch.setattr(conversation, "_test_mode_enabled", lambda: False)
    monkeypatch.setattr(convo, "call_llm", fake_call)
    convo.set_title()

    assert calls == [([
        {"role": "system", "content": conversation.TITLE_SYSTEM_PROMPT},
        *history[2:],
        {"role": "user", "content": "Generate a title for the conversation above."},
    ], True)]
    assert path.read_text() == "# Conversation title\n\n--- User ---\n\nQuestion"


def test_chunk_helpers_and_multimodal_merge():
    class Delta:
        content = "object text"

    class Choice:
        delta = Delta()

    class Chunk:
        choices = [Choice()]
        usage = {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}

    assert (
        backends._chunk_text({"choices": [{"delta": {"content": "dict text"}}]})
        == "dict text"
    )
    assert backends._chunk_text({"choices": [{"delta": {}}]}) == ""
    assert backends._chunk_text(Chunk()) == "object text"
    assert backends._chunk_text(object()) == ""
    assert backends._chunk_usage({"usage": {"total_tokens": 3}}) == {"total_tokens": 3}
    assert backends._chunk_usage(Chunk()) == {
        "prompt_tokens": 1,
        "completion_tokens": 2,
        "total_tokens": 3,
    }

    merged = textllm.merge_message_runs(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Describe"},
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/png;base64,"},
                    },
                ],
            },
            {"role": "user", "content": "Then summarize"},
        ]
    )

    assert len(merged) == 1
    assert merged[0]["content"][0]["text"] == "Describe"
    assert merged[0]["content"][2] == {"type": "text", "text": "\n\n"}
    assert merged[0]["content"][-1] == {"type": "text", "text": "Then summarize"}

    merged = textllm.merge_message_runs(
        [
            {"role": "user", "content": [{"type": "text", "text": "One"}]},
            {"role": "user", "content": [{"type": "text", "text": "Two"}]},
        ]
    )
    assert merged[0]["content"] == [
        {"type": "text", "text": "One\n\n"},
        {"type": "text", "text": "Two"},
    ]

    assert backends._responses_messages(merged) == [
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": "One\n\n"},
                {"type": "input_text", "text": "Two"},
            ],
        }
    ]
    assert backends._responses_messages(
        [
            {
                "role": "user",
                "content": [
                    {
                        "type": "file",
                        "file": {
                            "file_data": "data:application/pdf;base64,cGRm",
                            "filename": "report.pdf",
                        },
                    }
                ],
            }
        ]
    ) == [
        {
            "role": "user",
            "content": [
                {
                    "type": "input_file",
                    "file_data": "data:application/pdf;base64,cGRm",
                    "filename": "report.pdf",
                }
            ],
        }
    ]


def test_responses_events(monkeypatch):
    import any_llm

    calls = []

    def fake_responses(**kwargs):
        calls.append(kwargs)
        yield {"type": "response.web_search_call.in_progress"}
        yield {"type": "response.refusal.delta", "delta": "declined "}
        yield {
            "type": "response.reasoning_summary_text.delta",
            "delta": "Checked sources.",
        }
        yield {"type": "response.output_text.delta", "delta": "answer"}
        yield {
            "type": "response.output_text.annotation.added",
            "annotation": {
                "type": "url_citation",
                "title": "Example",
                "url": "https://example.com/",
            },
        }
        yield {
            "type": "response.completed",
            "response": {
                "output": [],
                "usage": {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5},
            },
        }

    monkeypatch.setattr(any_llm, "responses", fake_responses)
    events = list(
        textllm.iter_responses_events(
            model="openai/gpt-5.5",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "describe"},
                        {
                            "type": "image_url",
                            "image_url": {"url": "https://example.com/image.png"},
                        },
                    ],
                }
            ],
            settings={"tools": [{"type": "web_search"}]},
        )
    )

    assert calls[0]["provider"] == "openai"
    assert calls[0]["model"] == "gpt-5.5"
    assert calls[0]["stream"] is True
    assert calls[0]["input_data"][0]["content"] == [
        {"type": "input_text", "text": "describe"},
        {"type": "input_image", "image_url": "https://example.com/image.png"},
    ]
    assert [event.kind for event in events] == [
        "tool_status",
        "output_text_delta",
        "reasoning_summary_delta",
        "output_text_delta",
        "citation",
        "usage",
        "completed",
    ]

    calls.clear()
    list(
        textllm.iter_responses_events(
            model="openrouter/anthropic/claude-sonnet-4.6",
            messages=[{"role": "user", "content": "hello"}],
            settings={"reasoning": {"effort": "medium"}},
        )
    )
    assert calls[0]["provider"] == "openrouter"
    assert calls[0]["model"] == "anthropic/claude-sonnet-4.6"

    assert (
        backends._web_search_output_item_status(
            "response.output_item.added",
            {"item": {"type": "server_tool_call", "name": "openrouter:web_search"}},
        )
        == "in_progress"
    )
    assert (
        backends._web_search_output_item_status(
            "response.output_item.done",
            {"item": {"type": "openrouter:web_search"}},
        )
        == "completed"
    )
    assert (
        backends._web_search_output_item_status(
            "response.output_item.done",
            {"item": {"type": "message"}},
        )
        is None
    )

    assert backends._sources_markdown(
        [
            {"title": "One [source]", "url": "https://example.com/a path"},
            {"title": "Duplicate", "url": "https://example.com/a path"},
        ]
    ) == ("\n\n### Sources\n\n" "- [One \\[source\\]](<https://example.com/a%20path>)")


def test_messages_request_conversion():
    system, messages = backends._messages_request(
        [
            {"role": "system", "content": "System instruction"},
            {"role": "developer", "content": "Developer instruction"},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Describe these"},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "data:image/png;base64,cGl4ZWxz",
                        },
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": "https://example.com/image.jpg"},
                    },
                ],
            },
        ]
    )

    assert system == "System instruction\n\nDeveloper instruction"
    assert messages == [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Describe these"},
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": "cGl4ZWxz",
                    },
                },
                {
                    "type": "image",
                    "source": {
                        "type": "url",
                        "url": "https://example.com/image.jpg",
                    },
                },
            ],
        }
    ]
    with pytest.raises(ValueError, match="base64 image data"):
        backends._messages_image_block(
            {"type": "image_url", "image_url": {"url": "data:image/png,raw"}}
        )
    assert backends._messages_attachment_block(
        {
            "type": "file",
            "file": {
                "file_data": "data:application/pdf;base64,cGRm",
                "filename": "report.pdf",
            },
        }
    ) == {
        "type": "document",
        "source": {
            "type": "base64",
            "media_type": "application/pdf",
            "data": "cGRm",
        },
    }
    with pytest.raises(ValueError, match="non-empty file data"):
        backends._messages_attachment_block({"type": "file", "file": {}})


def test_messages_events(monkeypatch):
    import any_llm

    calls = []

    def fake_messages(**kwargs):
        calls.append(kwargs)
        yield {
            "type": "message_start",
            "message": {"usage": {"input_tokens": 2, "output_tokens": 0}},
        }
        yield {
            "type": "content_block_delta",
            "delta": {"type": "thinking_delta", "thinking": "Checked sources."},
        }
        yield {
            "type": "content_block_start",
            "content_block": {"type": "server_tool_use", "name": "web_search"},
        }
        yield {
            "type": "content_block_start",
            "content_block": {"type": "web_search_tool_result"},
        }
        yield {
            "type": "content_block_delta",
            "delta": {"type": "text_delta", "text": "answer"},
        }
        yield {
            "type": "content_block_delta",
            "delta": {
                "type": "citations_delta",
                "citation": {
                    "type": "web_search_result_location",
                    "title": "Example",
                    "url": "https://example.com/",
                },
            },
        }
        yield {
            "type": "message_delta",
            "usage": {
                "output_tokens": 3,
                "server_tool_use": {"web_search_requests": 1},
            },
        }
        yield {"type": "message_stop"}

    monkeypatch.setattr(any_llm, "messages", fake_messages)
    events = list(
        textllm.iter_messages_events(
            model="anthropic/claude-sonnet-4-6",
            messages=[
                {"role": "system", "content": "Conversation system"},
                {"role": "user", "content": "Research this"},
            ],
            settings={
                "max_tokens": 4096,
                "system": "Configured system",
                "thinking": {"type": "adaptive"},
                "output_config": {"effort": "medium"},
            },
        )
    )

    assert calls == [
        {
            "provider": "anthropic",
            "model": "claude-sonnet-4-6",
            "messages": [{"role": "user", "content": "Research this"}],
            "max_tokens": 4096,
            "stream": True,
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": "medium"},
            "system": "Configured system\n\nConversation system",
        }
    ]
    assert [event.kind for event in events] == [
        "reasoning_summary_delta",
        "tool_status",
        "tool_status",
        "output_text_delta",
        "citation",
        "usage",
        "completed",
    ]
    assert events[-2].data == {
        "input_tokens": 2,
        "output_tokens": 3,
        "server_tool_use": {"web_search_requests": 1},
        "total_tokens": 5,
    }

    with pytest.raises(ValueError, match="requires max_tokens"):
        list(
            textllm.iter_messages_events(
                model="anthropic/claude-sonnet-4-6",
                messages=[{"role": "user", "content": "hello"}],
                settings={},
            )
        )

    calls.clear()
    monkeypatch.setenv("OPENROUTER_API_KEY", "router-key")
    list(
        textllm.iter_messages_events(
            model="openrouter/anthropic/claude-sonnet-4.6",
            messages=[{"role": "user", "content": "hello"}],
            settings={"max_tokens": 1024},
        )
    )
    assert calls[0]["provider"] == "anthropic"
    assert calls[0]["model"] == "anthropic/claude-sonnet-4.6"
    assert calls[0]["api_base"] == "https://openrouter.ai/api"
    assert calls[0]["api_key"] == "router-key"

    calls.clear()
    monkeypatch.delenv("OPENROUTER_API_KEY")
    with pytest.raises(ValueError, match="OPENROUTER_API_KEY"):
        list(
            textllm.iter_messages_events(
                model="openrouter/anthropic/claude-sonnet-4.6",
                messages=[{"role": "user", "content": "hello"}],
                settings={"max_tokens": 1024},
            )
        )


def test_responses_error_event_raises(monkeypatch):
    clean()
    try:
        os.makedirs("testdir")
        path = Path("testdir/responses-error.md")
        path.write_text(dedent("""
                # responses error

                ```toml
                model = "openai/gpt-5.5"

                [textllm]
                backend = "responses"
                ```

                --- User ---

                hello
                """).strip())
        convo = textllm.Conversation(path)

        def fake_events(*, backend, model, messages, settings):
            yield textllm.ModelEvent(
                "error",
                data={"error": {"message": "provider unavailable"}},
            )

        monkeypatch.setattr(conversation, "iter_model_events", fake_events)
        with pytest.raises(RuntimeError, match="provider unavailable"):
            convo.chat()
        assert "--- Assistant ---" not in path.read_text()
    finally:
        clean()


def test_responses_backend_writes_sources_and_reasoning_block():
    clean()
    try:
        os.makedirs("testdir")
        path = Path("testdir/responses.md")
        path.write_text(dedent("""
                # responses

                ```toml
                model = "openrouter/anthropic/claude-sonnet-4.6"
                reasoning = { effort = "high", summary = "auto" }
                tools = [{ type = "openrouter:web_search" }]

                [textllm]
                backend = "responses"
                reasoning_summary = "block"
                ```

                --- User ---

                current news
                """).strip())

        convo = textllm.Conversation(path)
        with Capture() as cap:
            convo.chat()

        text = path.read_text()
        assert "### Sources" in cap.out
        assert "[Deterministic source](<https://example.com/>)" in text
        assert "--- Reasoning ---" in text
        assert "--- Note ---" not in text
        assert "Deterministic reasoning summary." in text
        assert text.index("--- Reasoning ---") < text.index("--- Assistant ---")
        assert text.endswith("--- User ---  \n\n")
        reparsed = textllm.Conversation(path)
        assert "Deterministic reasoning summary" not in repr(reparsed.messages)
        assert (
            textllm.Conversation._escape_role_markers(
                "Summary\n--- Reasoning ---\nstill local"
            )
            == "Summary\n\\--- Reasoning ---\nstill local"
        )
    finally:
        clean()


def test_messages_backend_writes_sources_and_reasoning_block():
    clean()
    try:
        os.makedirs("testdir")
        path = Path("testdir/messages.md")
        path.write_text(dedent("""
                # messages

                ```toml
                model = "anthropic/claude-sonnet-4-6"
                max_tokens = 4096
                thinking = { type = "adaptive" }
                output_config = { effort = "medium" }
                tools = [{ type = "web_search_20260318", name = "web_search" }]

                [textllm]
                backend = "messages"
                reasoning_summary = "block"
                ```

                --- User ---

                current news
                """).strip())

        convo = textllm.Conversation(path)
        with Capture() as cap:
            convo.chat()

        text = path.read_text()
        assert "### Sources" in cap.out
        assert "[Deterministic source](<https://example.com/>)" in text
        assert "--- Reasoning ---" in text
        assert "--- Note ---" not in text
        assert "Deterministic reasoning summary." in text
        assert text.index("--- Reasoning ---") < text.index("--- Assistant ---")
        assert text.endswith("--- User ---  \n\n")
        assert "Deterministic reasoning summary" not in repr(
            textllm.Conversation(path).messages
        )
    finally:
        clean()


def test_reasoning_and_assistant_blocks_follow_event_order(monkeypatch):
    clean()
    try:
        os.makedirs("testdir")
        path = Path("testdir/interleaved-reasoning.md")
        path.write_text(dedent("""
                # interleaved reasoning

                ```toml
                model = "openai/gpt-5.5"

                [textllm]
                backend = "responses"
                reasoning_summary = "block"
                ```

                --- User ---

                hello
                """).strip())

        def fake_events(*, backend, model, messages, settings):
            yield textllm.ModelEvent("reasoning_summary_delta", text="Think one.")
            yield textllm.ModelEvent("output_text_delta", text="Answer one.")
            yield textllm.ModelEvent("reasoning_summary_delta", text="Think two.")
            yield textllm.ModelEvent("output_text_delta", text="Answer two.")
            yield textllm.ModelEvent("completed")

        monkeypatch.setattr(conversation, "iter_model_events", fake_events)
        with Capture() as cap:
            convo = textllm.Conversation(path)
            convo.chat()

        text = path.read_text()
        markers = [
            "--- Reasoning ---",
            "--- Assistant ---",
            "--- Reasoning ---",
            "--- Assistant ---",
        ]
        positions = []
        start = 0
        for marker in markers:
            position = text.index(marker, start)
            positions.append(position)
            start = position + len(marker)
        assert positions == sorted(positions)
        assert "Think one." not in cap.out
        assert "Think two." not in cap.out
        assert "Answer one.Answer two." in cap.out

        reparsed = textllm.Conversation(path)
        assert reparsed.messages[-1] == {
            "role": "assistant",
            "content": "Answer one.\n\nAnswer two.",
        }
        assert "Think one." not in repr(reparsed.messages)
        assert "Think two." not in repr(reparsed.messages)
    finally:
        clean()


def test_title_call_disables_tools_and_uses_title_settings(monkeypatch):
    clean()
    try:
        os.makedirs("testdir")
        path = Path("testdir/title-settings.md")
        path.write_text(dedent("""
                # !!AUTO TITLE!!

                ```toml
                model = "openai/gpt-5.5"
                tools = [{ type = "web_search" }]
                tool_choice = "auto"
                max_tool_calls = 4

                [textllm]
                backend = "responses"
                tokens = "stderr"

                [textllm.title]
                reasoning = { effort = "low" }
                tool_choice = "required"
                ```

                --- User ---

                title me
                """).strip())
        convo = textllm.Conversation(path)
        calls = []

        def fake_events(*, backend, model, messages, settings):
            calls.append((backend, settings))
            yield textllm.ModelEvent("output_text_delta", text="Safe title")
            yield textllm.ModelEvent("usage", data={"total_tokens": 99})
            yield textllm.ModelEvent("completed")

        monkeypatch.setattr(conversation, "iter_model_events", fake_events)
        with Capture() as cap:
            convo.set_title()

        assert calls == [
            ("responses", {"reasoning": {"effort": "low"}})
        ]
        assert "Token usage:" not in cap.err
        assert path.read_text().startswith("# Safe title")
    finally:
        clean()


def test_token_usage_format_and_nested_merge():
    usage = backends._merge_usage(
        {
            "input_tokens": 8,
            "input_tokens_details": {"cached_tokens": 3},
        },
        {
            "output_tokens": 5,
            "input_tokens_details": {"audio_tokens": 2},
            "output_tokens_details": {"reasoning_tokens": 4},
            "total_tokens": 13,
        },
    )

    assert usage == {
        "input_tokens": 8,
        "input_tokens_details": {"cached_tokens": 3, "audio_tokens": 2},
        "output_tokens": 5,
        "output_tokens_details": {"reasoning_tokens": 4},
        "total_tokens": 13,
    }
    assert backends._format_token_usage(usage) == dedent("""
        Token usage:

        - input_tokens: 8
        - input_tokens_details.cached_tokens: 3
        - input_tokens_details.audio_tokens: 2
        - output_tokens: 5
        - output_tokens_details.reasoning_tokens: 4
        - total_tokens: 13
        """).strip()


def test_token_usage_stderr_note_and_completion_request(monkeypatch):
    clean()
    try:
        os.makedirs("testdir")
        path = Path("testdir/tokens.md")
        path.write_text(dedent("""
                # tokens

                ```toml
                model = "openai/gpt-4o-mini"
                stream_options = { custom = true }

                [textllm]
                tokens = "stderr"
                ```

                --- User ---

                hello
                """).strip())
        convo = textllm.Conversation(path)
        calls = []

        def fake_events(*, backend, model, messages, settings):
            calls.append(settings)
            yield textllm.ModelEvent("output_text_delta", text="answer")
            yield textllm.ModelEvent(
                "usage",
                data={
                    "prompt_tokens": 2,
                    "completion_tokens": 3,
                    "completion_tokens_details": {"reasoning_tokens": 1},
                    "total_tokens": 5,
                },
            )
            yield textllm.ModelEvent("completed")

        monkeypatch.setattr(conversation, "iter_model_events", fake_events)
        with Capture() as cap:
            convo.chat()

        assert "Token usage:" in cap.err
        assert "completion_tokens_details.reasoning_tokens: 1" in cap.err
        assert "Token usage:" not in path.read_text()
        assert calls[-1]["stream_options"] == {
            "custom": True,
            "include_usage": True,
        }

        path.write_text(
            path.read_text().replace('tokens = "stderr"', 'tokens = "none"')
        )
        convo = textllm.Conversation(path)
        with Capture() as cap:
            convo.chat(require_user_prompt=False, tokens="note")

        text = path.read_text()
        assert "Token usage:" in text
        assert "- prompt_tokens: 2" in text
        assert "Token usage:" not in cap.err
        assert text.endswith("--- User ---  \n\n")
    finally:
        clean()


def test_invalid_token_mode():
    clean()
    try:
        os.makedirs("testdir")
        path = Path("testdir/tokens-invalid.md")
        path.write_text(dedent("""
                # tokens

                ```toml
                model = "openai/gpt-4o-mini"

                [textllm]
                tokens = "somewhere"
                ```

                --- User ---

                hello
                """).strip())

        with pytest.raises(ValueError, match="textllm.tokens"):
            textllm.Conversation(path).call_llm([{"role": "user", "content": "hi"}])
    finally:
        clean()


def test_call_llm_usage_and_empty_stream(monkeypatch):
    clean()
    try:
        os.makedirs("testdir")
        Path("testdir/call.md").write_text(dedent("""
                # call

                ```toml
                model = "openai/gpt-4o-mini"
                ```

                --- User ---

                hello
                """).strip())
        convo = textllm.Conversation("testdir/call.md")

        def fake_stream(*, model, messages, settings):
            yield "hi", {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}

        monkeypatch.setattr(backends, "iter_completion_text", fake_stream)
        response = convo.call_llm(convo.messages)
        assert response.content == "hi"
        assert response.usage_metadata["total_tokens"] == 3

        def empty_stream(*, model, messages, settings):
            if False:
                yield None

        monkeypatch.setattr(backends, "iter_completion_text", empty_stream)
        with pytest.raises(ValueError, match="did not include text"):
            convo.call_llm(convo.messages)
    finally:
        clean()


def test_chat_streams_chunks_to_file(monkeypatch):
    clean()
    try:
        os.makedirs("testdir")
        path = Path("testdir/stream.md")
        path.write_text(dedent("""
                # stream

                ```toml
                model = "openai/gpt-4o-mini"
                ```

                --- User ---

                hello
                """).strip())
        convo = textllm.Conversation(path)

        def fake_stream(*, model, messages, settings):
            yield "first ", None
            text = path.read_text()
            assert "--- Assistant ---" in text
            assert "first " in text
            assert "second" not in text
            yield "second", None
            yield "\n--- User ---", None

        monkeypatch.setattr(backends, "iter_completion_text", fake_stream)

        with Capture() as cap:
            convo.chat()

        text = path.read_text()
        assert cap.out.strip() == "first second\n--- User ---"
        assert "first second\n--- User ---" in text
        assert "\\--- User ---" not in text
        assert text.endswith("--- User ---  \n\n")
    finally:
        clean()


def test_chat_does_not_write_assistant_block_before_first_chunk(monkeypatch):
    clean()
    try:
        os.makedirs("testdir")
        path = Path("testdir/error-before-stream.md")
        path.write_text(dedent("""
                # stream error

                ```toml
                model = "openai/gpt-4o-mini"
                ```

                --- User ---

                hello
                """).strip())
        convo = textllm.Conversation(path)

        def failing_stream(*, model, messages, settings):
            raise RuntimeError("provider failed")
            yield

        monkeypatch.setattr(backends, "iter_completion_text", failing_stream)

        with pytest.raises(RuntimeError, match="provider failed"):
            convo.chat()

        text = path.read_text()
        assert "--- Assistant ---" not in text
        assert not text.endswith("--- User ---  \n\n")
    finally:
        clean()


def test_chat_does_not_write_assistant_block_for_empty_stream(monkeypatch):
    clean()
    try:
        os.makedirs("testdir")
        path = Path("testdir/empty-stream.md")
        path.write_text(dedent("""
                # empty stream

                ```toml
                model = "openai/gpt-4o-mini"
                ```

                --- User ---

                hello
                """).strip())
        convo = textllm.Conversation(path)

        def empty_stream(*, model, messages, settings):
            if False:
                yield None

        monkeypatch.setattr(backends, "iter_completion_text", empty_stream)

        with pytest.raises(ValueError, match="did not include text"):
            convo.chat()

        text = path.read_text()
        assert "--- Assistant ---" not in text
        assert not text.endswith("--- User ---  \n\n")
    finally:
        clean()


def test_seek_append_point_truncates_trailing_whitespace():
    clean()
    try:
        os.makedirs("testdir")
        path = Path("testdir/trim.md")
        path.write_text("message\n\n   \n")

        with path.open("r+") as fp:
            textllm.Conversation._seek_append_point(fp)
            fp.write(" appended")

        assert path.read_text() == "message appended"
    finally:
        clean()


def test_file_edit_adds_prompt_after_user_marker_without_trailing_newline():
    clean()
    try:
        os.makedirs("testdir")
        path = Path("testdir/prompt.md")
        path.write_text("--- User ---")

        assert textllm.file_edit(path, prompt="hello", editor=False)
        assert path.read_text() == "--- User ---\nhello"
    finally:
        clean()


def test_file_edit_uses_existing_blank_user_block():
    clean()
    try:
        os.makedirs("testdir")
        path = Path("testdir/prompt.md")
        path.write_text("--- User ---  \n\n")

        assert textllm.file_edit(path, prompt="hello", editor=False)
        assert path.read_text() == "--- User ---  \n\nhello"
    finally:
        clean()


def test_split_model_provider():
    assert textllm.split_model_provider("openai/gpt-4o-mini") == (
        "openai",
        "gpt-4o-mini",
    )
    assert textllm.split_model_provider("openai:gpt-4o-mini") == (
        "openai",
        "gpt-4o-mini",
    )
    assert textllm.split_model_provider("openrouter/openai/gpt-4o-mini") == (
        "openrouter",
        "openai/gpt-4o-mini",
    )
    assert textllm.split_model_provider("openrouter:openai/gpt-4o-mini") == (
        "openrouter",
        "openai/gpt-4o-mini",
    )
    assert textllm.split_model_provider("gateway/model:variant") == (
        "gateway",
        "model:variant",
    )
    assert textllm.split_model_provider("gpt-4o-mini") == (None, "gpt-4o-mini")


def test_iter_completion_text_uses_any_llm_provider_model(monkeypatch):
    fake_any_llm = types.SimpleNamespace()

    def fake_completion(*, model, provider, messages, stream, **settings):
        assert provider == "openai"
        assert model == "gpt-4o-mini"
        assert stream is True
        assert settings == {"temperature": 0.5}
        yield {"choices": [{"delta": {"content": "ok"}}]}

    fake_any_llm.completion = fake_completion
    monkeypatch.setitem(sys.modules, "any_llm", fake_any_llm)
    monkeypatch.setenv("TEXTLLM_TEST_MODE", "0")
    monkeypatch.setattr(config, "TEST_MODE", False)

    chunks = list(
        textllm.iter_completion_text(
            model="openai/gpt-4o-mini",
            messages=[{"role": "user", "content": "hello"}],
            settings={"temperature": 0.5},
        )
    )

    assert chunks == [("ok", None)]


def test_parser_edges_and_image_code_blocks():
    assert textllm.Conversation.read_settings("No settings here") == {}

    parsed = textllm.loads(dedent(r"""
            --- User ---

            \--- User ---
            This is literal marker text.
            """).strip())
    assert parsed["title"] == ""
    assert parsed["conversation"] == [
        {
            "role": "user",
            "content": "--- User ---\nThis is literal marker text.",
        }
    ]

    msg, images = textllm.process_msg_for_images(
        [
            "Before",
            "```md",
            "![not image](inside-code.png)",
            "```",
            "![real](outside.png)",
        ]
    )
    assert images == ["outside.png"]
    assert "![not image](inside-code.png)" in msg
    assert "![real](outside.png)" not in msg

    msg, images = textllm.process_msg_for_images(["![photo](image (1).png)"])
    assert msg == ""
    assert images == ["image (1).png"]

    msg, images, attachments, includes = textllm.process_msg_for_attachments(
        [
            "Compare these.",
            "[@attachment report][quarterly]",
            "[quarterly]: docs/report.pdf",
            "```md",
            "[@attachment literal](not-an-attachment.pdf)",
            "```",
        ]
    )
    assert msg == (
        "Compare these.\nreport\n```md\n"
        "[@attachment literal](not-an-attachment.pdf)\n```"
    )
    assert images == []
    assert attachments == ["docs/report.pdf"]
    assert includes == []
    msg, images, attachments, includes = textllm.process_msg_for_attachments(
        ["Read [@include](meeting notes (final).md) now."]
    )
    assert msg == "Read  now."
    assert images == []
    assert attachments == []
    assert includes == ["meeting notes (final).md"]
    msg, images, attachments, includes = textllm.process_msg_for_attachments(
        [
            "Read [@include][meeting-notes].",
            "[meeting-notes]: meeting notes (final).md",
        ]
    )
    assert msg == "Read ."
    assert images == []
    assert attachments == []
    assert includes == ["meeting notes (final).md"]
    with pytest.raises(ValueError, match="not defined"):
        textllm.process_msg_for_attachments(["[@attachment][missing]"])


def test_role_markers_can_be_markdown_headings():
    without_preamble = textllm.loads("### --- User ---\n\nHello")
    assert without_preamble["title"] == ""
    assert without_preamble["conversation"] == [{"role": "user", "content": "Hello"}]

    parsed = textllm.loads(dedent(r"""
            # Conversation title

            # --- System ---

            System message.

            ## --- User ---

            User message.

            ###### --- Assistant ---

            Assistant message.
            \# --- User ---
            This is literal marker text.
            """).strip())

    assert parsed["title"] == "Conversation title"
    assert parsed["conversation"] == [
        {"role": "system", "content": "System message."},
        {"role": "user", "content": "User message."},
        {
            "role": "assistant",
            "content": (
                "Assistant message.\n# --- User ---\nThis is literal marker text."
            ),
        },
    ]


@pytest.mark.parametrize(
    ("cli_level", "expected_marker"),
    [
        (None, "## --- Assistant ---"),
        (4, "#### --- Assistant ---"),
        (0, "--- Assistant ---"),
    ],
)
def test_header_level_config_and_cli_override(tmp_path, cli_level, expected_marker):
    path = tmp_path / f"header-{cli_level}.md"
    path.write_text(dedent("""
            # Header test

            ```toml
            [textllm]
            header_level = 2
            ```

            # --- User ---

            hello
            """).strip())
    argv = [str(path), "--title", "off"]
    if cli_level is not None:
        argv.extend(["--header-level", str(cli_level)])

    run_cli(argv)

    text = path.read_text()
    assert expected_marker in text
    expected_user = expected_marker.replace("Assistant", "User")
    assert text.endswith(f"{expected_user}  \n\n")


def test_invalid_header_level_config_does_not_modify_file(tmp_path):
    path = tmp_path / "invalid-header.md"
    original = dedent("""
        # Header test

        ```toml
        [textllm]
        header_level = 7
        ```

        --- User ---

        hello
        """).strip()
    path.write_text(original)

    with pytest.raises(ValueError, match="header_level"):
        textllm.Conversation(path).chat()

    assert path.read_text() == original


def test_header_level_environment_and_env_file(tmp_path, monkeypatch):
    def write_conversation(name, header_level=None):
        setting = (
            f"\n[textllm]\nheader_level = {header_level}\n"
            if header_level is not None
            else ""
        )
        path = tmp_path / name
        path.write_text(
            f"# Header test\n\n```toml\n{setting}```\n\n--- User ---\n\nhello"
        )
        return path

    monkeypatch.setenv("TEXTLLM_DEFAULT_HEADER_LEVEL", "3")
    environment_path = write_conversation("environment.md")
    run_cli([str(environment_path), "--title", "off"])
    assert "### --- Assistant ---" in environment_path.read_text()

    config_path = write_conversation("config.md", header_level=2)
    run_cli([str(config_path), "--title", "off"])
    assert "## --- Assistant ---" in config_path.read_text()

    env_path = tmp_path / "header.env"
    env_path.write_text("TEXTLLM_DEFAULT_HEADER_LEVEL=4")
    env_file_path = write_conversation("env-file.md")
    run_cli(
        [
            str(env_file_path),
            "--title",
            "off",
            "--env",
            str(env_path),
        ]
    )
    assert "#### --- Assistant ---" in env_file_path.read_text()


def test_header_level_applies_to_new_conversation_templates(tmp_path, monkeypatch):
    monkeypatch.setenv("TEXTLLM_DEFAULT_HEADER_LEVEL", "0")

    builtin_path = tmp_path / "builtin.md"
    run_cli([str(builtin_path), "--header-level", "2"])
    builtin_text = builtin_path.read_text()
    assert "## --- System ---" in builtin_text
    assert "## --- User ---" in builtin_text

    literal_template = tmp_path / "literal-template.md"
    literal_template.write_text("# Literal\n\n--- User ---\n")
    literal_path = tmp_path / "literal.md"
    run_cli(
        [
            str(literal_path),
            "--template",
            str(literal_template),
            "--header-level",
            "3",
        ]
    )
    assert "--- User ---" in literal_path.read_text()
    assert "### --- User ---" not in literal_path.read_text()

    placeholder_template = tmp_path / "placeholder-template.md"
    placeholder_template.write_text("# Placeholder\n\n{header}--- User ---\n")
    placeholder_path = tmp_path / "placeholder.md"
    run_cli(
        [
            str(placeholder_path),
            "--template",
            str(placeholder_template),
            "--header-level",
            "3",
        ]
    )
    assert "### --- User ---" in placeholder_path.read_text()

    configured_template = tmp_path / "configured-template.md"
    configured_template.write_text(
        "# Configured\n\n```toml\n[textllm]\nheader_level = 4\n```\n\n"
        "{header}--- User ---\n"
    )
    configured_path = tmp_path / "configured.md"
    run_cli([str(configured_path), "--template", str(configured_template)])
    assert "#### --- User ---" in configured_path.read_text()

    env_path = tmp_path / "header.env"
    env_path.write_text("TEXTLLM_DEFAULT_HEADER_LEVEL=5")
    env_builtin_path = tmp_path / "env-builtin.md"
    run_cli([str(env_builtin_path), "--env", str(env_path)])
    assert "##### --- System ---" in env_builtin_path.read_text()
    assert "##### --- User ---" in env_builtin_path.read_text()


def test_stdin_prompt_joining_and_empty_prompt_file_edit():
    clean()
    try:
        os.makedirs("testdir")
        Path("testdir/stdin.md").write_text(textllm.CONFIG.TEMPLATE)

        proc = subprocess.run(
            [
                sys.executable,
                "textllm.py",
                "testdir/stdin.md",
                "--prompt",
                "first prompt",
                "--stdin",
            ],
            input=b"second prompt",
            capture_output=True,
            env=os.environ.copy(),
        )
        assert proc.returncode == 0
        text = Path("testdir/stdin.md").read_text()
        assert "first prompt\n\nsecond prompt" in text
        assert "Last message ended with: prompt" in proc.stdout.decode()

        Path("testdir/empty.md").write_text("")
        assert textllm.file_edit("testdir/empty.md", prompt="abc", editor=False)
        assert Path("testdir/empty.md").read_text() == "abc"

        assert not textllm.file_edit("testdir/empty.md", prompt="", editor=False)
    finally:
        clean()


@pytest.fixture
def template_env(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "environ", os.environ.copy())
    for key in list(os.environ):
        if key.startswith("TEXTLLM_NAMED_TEMPLATE_") or key in {
            "TEXTLLM_TEMPLATE_FILE",
            "TEXTLLM_TEMPLATE_DIR",
            "TEXTLLM_ENV_PATH",
        }:
            monkeypatch.delenv(key)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_module, "load_dotenv", lambda *args, **kwargs: False)
    return tmp_path


def test_named_template_precedence_and_rendering(template_env, monkeypatch):
    directory = template_env / "templates"
    directory.mkdir()
    (directory / "openai.md").write_text("# Directory\n\n--- User ---\n")
    registered = template_env / "custom $ template.md"
    registered.write_text(
        "# Registered\n\n```toml\n[textllm]\nheader_level = 2\n```\n"
        "\n{header}--- User ---\n"
    )
    monkeypatch.setenv("TEXTLLM_TEMPLATE_DIR", str(directory))
    monkeypatch.setenv("TEXTLLM_NAMED_TEMPLATE_openai", str(registered))
    monkeypatch.setenv("TEXTLLM_TEMPLATE_FILE", "@openai")
    textllm.cli(["default.md"])
    assert "# Registered" in Path("default.md").read_text()
    assert "## --- User ---" in Path("default.md").read_text()

    textllm.cli(["cli.md", "--template", "@openai", "--header-level", "3"])
    assert "### --- User ---" in Path("cli.md").read_text()
    monkeypatch.delenv("TEXTLLM_NAMED_TEMPLATE_openai")
    textllm.cli(["directory.md", "--template", "@openai"])
    assert Path("directory.md").read_text().startswith("# Directory")
    Path("@openai").write_text("# Literal\n\n--- User ---\n")
    textllm.cli(["literal.md", "--template", "./@openai"])
    assert Path("literal.md").read_text().startswith("# Literal")


@pytest.mark.parametrize(
    "name", ["", ".", "..", "../openai", "sub/openai", "sub\\openai", "OPENAI"]
)
def test_named_template_invalid_or_unknown(template_env, monkeypatch, name):
    monkeypatch.setenv("TEXTLLM_NAMED_TEMPLATE_openai", "unused.md")
    with pytest.raises(ValueError, match="template"):
        textllm.cli(["new.md", "--template", "@" + name])
    assert not Path("new.md").exists()


@pytest.mark.parametrize("value", ["", "missing.md", "templates", "binary.md"])
def test_named_template_broken_override(template_env, monkeypatch, value):
    Path("templates").mkdir()
    Path("templates/openai.md").write_text("# Fallback\n")
    Path("binary.md").write_bytes(b"\xff")
    monkeypatch.setenv("TEXTLLM_TEMPLATE_DIR", "templates")
    monkeypatch.setenv("TEXTLLM_NAMED_TEMPLATE_openai", value)
    with pytest.raises(ValueError, match="TEXTLLM_NAMED_TEMPLATE_openai"):
        textllm.cli(["nested/new.md", "--template", "@openai"])
    assert not Path("nested/new.md").exists()


def test_named_template_listing(template_env, monkeypatch, capsys):
    Path("templates").mkdir()
    for name in [
        "openai.md",
        "writing help.md",
        "cash $ (notes).md",
        ".hidden.md",
        "upper.MD",
    ]:
        Path("templates", name).write_text("# Template\n")
    Path("templates/sub").mkdir()
    Path("templates/sub/nested.md").write_text("# Nested\n")
    Path("templates/folder.md").mkdir()
    Path("templates/link.md").symlink_to("openai.md")
    Path("override.md").write_text("# Override\n")
    monkeypatch.setenv("TEXTLLM_TEMPLATE_DIR", "templates")
    monkeypatch.setenv("TEXTLLM_NAMED_TEMPLATE_openai", "override.md")
    textllm.cli(["--list-templates", "--stdin", "--edit"])
    out = capsys.readouterr().out
    assert '"@openai" -> "override.md" [TEXTLLM_NAMED_TEMPLATE_openai]' in out
    assert 'overrides "templates/openai.md"' in out
    assert '"@link"' in out
    assert '"@writing help"' in out
    assert '"@cash $ (notes)"' in out
    assert not any(name in out for name in ["hidden", "nested", "upper", "folder"])
    assert not Path(textllm.DEFAULT_FILEPATH).exists()
    assert not Path("log").exists()
    textllm.cli(["spaces.md", "--template", "@writing help"])
    assert Path("spaces.md").read_text() == "# Template\n"


def test_named_template_listing_errors_and_empty(template_env, monkeypatch, capsys):
    textllm.cli(["--list-templates"])
    assert "No named templates configured" in capsys.readouterr().out
    Path("valid.md").write_text("# Valid\n")
    monkeypatch.setenv("TEXTLLM_NAMED_TEMPLATE_valid", "valid.md")
    monkeypatch.setenv("TEXTLLM_NAMED_TEMPLATE_broken", "missing.md")
    monkeypatch.setenv("TEXTLLM_NAMED_TEMPLATE_empty", "")
    monkeypatch.setenv("TEXTLLM_TEMPLATE_DIR", "missing-directory")
    with pytest.raises(SystemExit) as exc:
        textllm.cli(["--list-templates"])
    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert '"@valid"' in captured.out
    assert "TEXTLLM_TEMPLATE_DIR" in captured.err
    assert "TEXTLLM_NAMED_TEMPLATE_broken" in captured.err
    assert "TEXTLLM_NAMED_TEMPLATE_empty" in captured.err
    # An invalid discovery directory does not prevent explicit selection.
    textllm.cli(["new.md", "--template", "@valid"])
    textllm.cli(["direct.md", "--template", "valid.md"])
    textllm.cli(["builtin.md"])


def test_named_template_existing_conversation_ignores_lookup(template_env, monkeypatch):
    monkeypatch.setenv("TEXTLLM_TEMPLATE_DIR", "missing-directory")
    Path("existing.md").write_text("# Existing\n\n--- User ---\n\nhello\n")
    textllm.cli(["existing.md", "--template", "@missing", "--title", "off"])
    assert "--- Assistant ---" in Path("existing.md").read_text()


def test_named_template_dotenv_loading(template_env, monkeypatch):
    from dotenv import load_dotenv
    import dotenv.main

    monkeypatch.setattr(cli_module, "load_dotenv", load_dotenv)
    monkeypatch.setattr(dotenv.main, "find_dotenv", lambda: str(template_env / ".env"))
    Path("templates").mkdir()
    Path("templates/expanded.md").write_text("# Expanded\n\n--- User ---\n")
    Path("first.env").write_text("TEXTLLM_NAMED_TEMPLATE_special=missing-first.md\n")
    Path(".env").write_text("TEXTLLM_NAMED_TEMPLATE_special=missing-second.md\n")
    Path("last.env").write_text(
        "TEXTLLM_TEMPLATE_DIR=templates\n"
        "TEXTLLM_NAMED_TEMPLATE_special=${TEXTLLM_TEMPLATE_DIR}/expanded.md\n"
        "TEXTLLM_TEMPLATE_FILE=@special\n"
    )
    monkeypatch.setenv("TEXTLLM_ENV_PATH", "first.env")
    monkeypatch.setenv("TEXTLLM_NAMED_TEMPLATE_special", "missing-shell.md")
    Path("direct.md").write_text("# Direct\n")
    textllm.cli(["precedence.md", "--template", "direct.md"])
    assert os.environ["TEXTLLM_NAMED_TEMPLATE_special"] == "missing-second.md"
    textllm.cli(["new.md", "--env", "last.env"])
    assert Path("new.md").read_text().startswith("# Expanded")
    # Later changes do not retroactively interpolate earlier registrations.
    Path("first.env").write_text("")
    Path(".env").write_text("")
    Path("later.env").write_text("TEXTLLM_TEMPLATE_DIR=missing-directory\n")
    textllm.cli(["later.md", "--env", "later.env"])
    assert Path("later.md").read_text().startswith("# Expanded")
    assert os.environ["TEXTLLM_NAMED_TEMPLATE_special"] == "templates/expanded.md"


def test_named_template_subprocess(template_env):
    script = str(LOCAL_LAUNCHER)
    Path("template.md").write_text("# Subprocess\n\n--- User ---\n")
    Path("templates.env").write_text(
        "TEXTLLM_NAMED_TEMPLATE_special=template.md\n"
        "TEXTLLM_TEMPLATE_FILE=@special\n"
    )
    env = os.environ.copy()
    env["TEXTLLM_TEST_MODE"] = "1"
    env.pop("PYTHON_DOTENV_DISABLED", None)
    env.pop("TEXTLLM_TEST_OUTPUT_FILE", None)
    commands = [
        (["--list-templates"], 0),
        (["new.md"], 0),
        (["failed.md", "--template", "@missing"], 1),
    ]
    for args, expected_status in commands:
        proc = subprocess.run(
            [sys.executable, script, "--env", "templates.env", *args],
            env=env,
            capture_output=True,
            text=True,
        )
        assert proc.returncode == expected_status, proc.stderr
        if "--list-templates" in args:
            assert '"@special"' in proc.stdout
            assert not Path(textllm.DEFAULT_FILEPATH).exists()
            assert not Path("log").exists()
    assert Path("new.md").read_text().startswith("# Subprocess")
    assert not Path("failed.md").exists()
    Path("templates.env").write_text("TEXTLLM_NAMED_TEMPLATE_broken=missing.md\n")
    proc = subprocess.run(
        [sys.executable, script, "--env", "templates.env", "--list-templates"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1
    assert "TEXTLLM_NAMED_TEMPLATE_broken" in proc.stderr


def test_dynamic_env():
    """
    Test that it will change based on os.environ and/or loaded environment.

    Note that the first test isn't really needed because it is already used in the
    main tests but still good to explicitly confirm
    """
    clean()
    env0 = os.environ.copy()
    try:
        # Even the fact that it is 0.01 as set above as an env after import will test
        # it in many ways
        run_cli(["testdir/os.environ.md"])
        convo = textllm.loads(Path("testdir/os.environ.md").read_text())
        assert convo["settings"]["temperature"] == 0.01

        # Env file specified. Also tests overwriting environment
        Path("testdir/envfile.env").write_text("TEXTLLM_DEFAULT_TEMPERATURE = 0.1")
        run_cli(["testdir/env_file.md", "--env", "testdir/envfile.env"])
        convo = textllm.loads(Path("testdir/env_file.md").read_text())
        assert convo["settings"]["temperature"] == 0.1

        Path("testdir/env_template.md").write_text(
            "# env template\n```toml\ntemperature = 0.4\n```\n\n--- User ---\n"
        )
        Path("testdir/arg_template.md").write_text(
            "# arg template\n```toml\ntemperature = 0.5\n```\n\n--- User ---\n"
        )
        Path("testdir/template.env").write_text(
            "TEXTLLM_TEMPLATE_FILE = testdir/env_template.md"
        )
        run_cli(
            [
                "testdir/template_precedence.md",
                "--env",
                "testdir/template.env",
                "--template",
                "testdir/arg_template.md",
            ]
        )
        text = Path("testdir/template_precedence.md").read_text()
        assert "# arg template" in text
        assert "# env template" not in text
        os.environ.pop("TEXTLLM_TEMPLATE_FILE", None)

        env2 = Path("testdir/envfile2.env")
        env2.write_text("TEXTLLM_DEFAULT_TEMPERATURE = 0.2")
        os.environ["TEXTLLM_ENV_PATH"] = str(env2.resolve())
        run_cli(["testdir/env_file2.md"])
        convo = textllm.loads(Path("testdir/env_file2.md").read_text())
        assert convo["settings"]["temperature"] == 0.2
    finally:
        os.environ = env0

    try:
        os.environ.pop("TEXTLLM_ENV_PATH", None)
        Path(".env").write_text("TEXTLLM_DEFAULT_TEMPERATURE = 0.3")
        run_cli(["testdir/env_file_dot.md"])
        convo = textllm.loads(Path("testdir/env_file_dot.md").read_text())
        assert convo["settings"]["temperature"] == 0.3
    finally:
        os.unlink(".env")
        os.environ = env0


if __name__ == "__main__":
    # test_main()
    # test_auto_names()
    # test_default_settings()
    # test_images()
    test_dynamic_env()


@pytest.mark.parametrize("backend", ["completion", "responses", "messages"])
@pytest.mark.parametrize("temperature", [None, 0.4])
def test_optional_temperature_reaches_chat_and_title(tmp_path, monkeypatch, backend, temperature):
    monkeypatch.setenv("TEXTLLM_DEFAULT_TEMPERATURE", "0.75")
    monkeypatch.setenv("TEXTLLM_DEFAULT_MODEL", "openai/test-model")
    path = tmp_path / "conversation with spaces.md"
    setting = "# temperature = 0.75" if temperature is None else f"temperature = {temperature}"
    path.write_text(
        f'# !!AUTO TITLE!!\n\n```toml\n{setting}\n'
        f'[textllm]\nbackend = "{backend}"\n```\n\n--- User ---\n\nHello'
    )
    convo = textllm.Conversation(path)
    calls = []

    def fake_events(**kwargs):
        calls.append(kwargs)
        yield textllm.ModelEvent("output_text_delta", text="Answer")
        yield textllm.ModelEvent("completed")

    monkeypatch.setattr(conversation, "iter_model_events", fake_events)
    convo.call_llm(convo.messages)
    convo.set_title()
    assert len(calls) == 2
    for call in calls:
        assert call["backend"] == backend
        assert call["model"] == "openai/test-model"
        assert call["settings"] == ({} if temperature is None else {"temperature": temperature})


@pytest.mark.parametrize("chunk", ["partial", "complete\n", ""])
@pytest.mark.parametrize("visible", [True, False])
def test_console_logging_during_stream(monkeypatch, chunk, visible):
    import logging
    from textllm.output import ConsoleHandler

    terminal = io.StringIO()
    monkeypatch.setattr(sys, "stdout", terminal)
    handler = ConsoleHandler(terminal)
    handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    handler.setLevel(logging.INFO if visible else logging.WARNING)
    monkeypatch.setattr(config.log, "handlers", [handler])
    monkeypatch.setattr(config.log, "level", logging.DEBUG)
    monkeypatch.setattr(config.log, "propagate", False)

    convo = object.__new__(textllm.Conversation)
    convo.settings = {"model": "fake"}

    def events(**kwargs):
        if chunk:
            yield textllm.ModelEvent("output_text_delta", text=chunk)
        yield textllm.ModelEvent("tool_status", data="searching")
        yield textllm.ModelEvent("tool_status", data="completed")
        yield textllm.ModelEvent("output_text_delta", text="answer")

    monkeypatch.setattr(conversation, "iter_model_events", events)
    response = convo.call_llm([], print_stream=True)
    separator = "\n" if chunk and not chunk.endswith("\n") else ""
    logs = "INFO: Web search searching\nINFO: Web search completed\n"
    assert terminal.getvalue() == (
        "\n" + chunk + (separator + logs if visible else "") + "answer\n\n"
    )
    assert response.content == chunk + "answer"
