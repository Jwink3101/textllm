import base64
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

## Reset these
os.environ["TEXTLLM_TEST_MODE"] = "1"
os.environ["TEXTLLM_DEFAULT_TEMPERATURE"] = "0.01"
textllm.TEMPLATE = """\
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
        assert "--- Included file: notes with spaces.txt ---\n\nSpaced note." in spaced_text

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


def test_note_blocks_are_never_sent_to_model(monkeypatch):
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
                """).strip())

        convo = textllm.Conversation(path)
        assert [item["role"] for item in convo.parsed["conversation"]] == [
            "system",
            "note",
            "user",
            "note",
        ]
        assert [message["role"] for message in convo.messages] == ["system", "user"]
        assert "private" not in repr(convo.messages)

        calls = []

        def fake_stream(*, model, messages, settings):
            calls.append(messages)
            yield "response", None

        monkeypatch.setattr(textllm, "iter_completion_text", fake_stream)
        convo.call_llm(
            convo.messages + [{"role": "note", "content": "direct private note"}]
        )
        assert [message["role"] for message in calls[-1]] == ["system", "user"]
        assert "private" not in repr(calls[-1])

        monkeypatch.setattr(textllm, "_test_mode_enabled", lambda: False)
        convo.set_title()
        assert [message["role"] for message in calls[-1]] == ["system", "user"]
        assert "private" not in repr(calls[-1])
        title_conversation = json.loads(calls[-1][1]["content"])
        assert [item["role"] for item in title_conversation] == ["system", "user"]
    finally:
        clean()


def test_chunk_helpers_and_multimodal_merge():
    class Delta:
        content = "object text"

    class Choice:
        delta = Delta()

    class Chunk:
        choices = [Choice()]
        usage = {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}

    assert (
        textllm._chunk_text({"choices": [{"delta": {"content": "dict text"}}]})
        == "dict text"
    )
    assert textllm._chunk_text({"choices": [{"delta": {}}]}) == ""
    assert textllm._chunk_text(Chunk()) == "object text"
    assert textllm._chunk_text(object()) == ""
    assert textllm._chunk_usage({"usage": {"total_tokens": 3}}) == {"total_tokens": 3}
    assert textllm._chunk_usage(Chunk()) == {
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

    assert textllm._responses_messages(merged) == [
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": "One\n\n"},
                {"type": "input_text", "text": "Two"},
            ],
        }
    ]
    assert textllm._responses_messages(
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
        textllm._web_search_output_item_status(
            "response.output_item.added",
            {"item": {"type": "server_tool_call", "name": "openrouter:web_search"}},
        )
        == "in_progress"
    )
    assert (
        textllm._web_search_output_item_status(
            "response.output_item.done",
            {"item": {"type": "openrouter:web_search"}},
        )
        == "completed"
    )
    assert (
        textllm._web_search_output_item_status(
            "response.output_item.done",
            {"item": {"type": "message"}},
        )
        is None
    )

    assert textllm._sources_markdown(
        [
            {"title": "One [source]", "url": "https://example.com/a path"},
            {"title": "Duplicate", "url": "https://example.com/a path"},
        ]
    ) == ("\n\n### Sources\n\n" "- [One \\[source\\]](<https://example.com/a%20path>)")


def test_messages_request_conversion():
    system, messages = textllm._messages_request(
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
        textllm._messages_image_block(
            {"type": "image_url", "image_url": {"url": "data:image/png,raw"}}
        )
    assert textllm._messages_attachment_block(
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
        textllm._messages_attachment_block({"type": "file", "file": {}})


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

        monkeypatch.setattr(textllm, "iter_model_events", fake_events)
        with pytest.raises(RuntimeError, match="provider unavailable"):
            convo.chat()
        assert "--- Assistant ---" not in path.read_text()
    finally:
        clean()


def test_responses_backend_writes_sources_and_reasoning_note():
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
                reasoning_summary = "note"
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
        assert "--- Note ---" in text
        assert "Deterministic reasoning summary." in text
        assert text.endswith("--- User ---  \n\n")
        reparsed = textllm.Conversation(path)
        assert "Deterministic reasoning summary" not in repr(reparsed.messages)
        assert (
            textllm.Conversation._escape_role_markers(
                "Summary\n--- User ---\nstill local"
            )
            == "Summary\n\\--- User ---\nstill local"
        )
    finally:
        clean()


def test_messages_backend_writes_sources_and_reasoning_note():
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
                reasoning_summary = "note"
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
        assert "--- Note ---" in text
        assert "Deterministic reasoning summary." in text
        assert text.endswith("--- User ---  \n\n")
        assert "Deterministic reasoning summary" not in repr(
            textllm.Conversation(path).messages
        )
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

        monkeypatch.setattr(textllm, "iter_model_events", fake_events)
        with Capture() as cap:
            convo.set_title()

        assert calls == [
            ("responses", {"temperature": 0.01, "reasoning": {"effort": "low"}})
        ]
        assert "Token usage:" not in cap.err
        assert path.read_text().startswith("# Safe title")
    finally:
        clean()


def test_token_usage_format_and_nested_merge():
    usage = textllm._merge_usage(
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
    assert textllm._format_token_usage(usage) == dedent("""
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

        monkeypatch.setattr(textllm, "iter_model_events", fake_events)
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

        monkeypatch.setattr(textllm, "iter_completion_text", fake_stream)
        response = convo.call_llm(convo.messages)
        assert response.content == "hi"
        assert response.usage_metadata["total_tokens"] == 3

        def empty_stream(*, model, messages, settings):
            if False:
                yield None

        monkeypatch.setattr(textllm, "iter_completion_text", empty_stream)
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

        monkeypatch.setattr(textllm, "iter_completion_text", fake_stream)

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

        monkeypatch.setattr(textllm, "iter_completion_text", failing_stream)

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

        monkeypatch.setattr(textllm, "iter_completion_text", empty_stream)

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
    monkeypatch.setattr(textllm, "TEST_MODE", False)

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
