import base64
import importlib
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import types
from datetime import UTC, datetime
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


@pytest.fixture(autouse=True)
def isolated_continuation_state(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "environ", os.environ.copy())
    monkeypatch.delenv("TEXTLLM_ENV_PATH", raising=False)
    monkeypatch.delenv("TEXTLLM_TEMP_DIR", raising=False)
    monkeypatch.delenv("TEXTLLM_DEFAULT_HEADER_LEVEL", raising=False)
    monkeypatch.setenv("TEXTLLM_EDITOR", os.environ.get("TEXTLLM_EDITOR", "vi"))
    # Cover both cached tempfile lookups and fresh subprocess interpreters.
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.setenv("TEMP", str(tmp_path))
    monkeypatch.setenv("TMP", str(tmp_path))


def test_continue_tracks_creation_rename_and_working_directory(tmp_path, monkeypatch):
    from textllm.state import _last_conversation

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    textllm.cli([])
    created = Path(_last_conversation())
    assert created.name == "New Conversation.md"
    convo = textllm.cli(["-c", "--rename", "--prompt", "First question"])
    assert Path(_last_conversation()) == Path(convo.filepath).absolute()
    assert Path(convo.filepath).name == "title set automatically.md"
    assert not created.exists()
    elsewhere = tmp_path / "other directory"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    convo = textllm.cli(["--continue", "--prompt", "Second question"])
    assert Path(convo.filepath).parent == tmp_path
    assert "2 user messages" in Path(convo.filepath).read_text()


def test_continue_missing_and_explicit_paths(tmp_path, monkeypatch):
    from textllm.state import _last_conversation

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    with pytest.raises(ValueError, match="No last regular"):
        textllm.cli(["-c"])
    target = tmp_path / "notes & questions.md"
    with pytest.raises(ValueError, match="missing file"):
        textllm.cli(["-c", str(target)])
    assert not target.exists()
    textllm.cli([str(target)])
    textllm.cli(["-c", str(target), "--prompt", "Hello"])
    assert _last_conversation() == str(target)
    target.unlink()
    with pytest.raises(ValueError, match="is missing"):
        textllm.cli(["-c"])
    with pytest.raises(SystemExit):
        textllm.cli(["-c", str(tmp_path)])


def test_continue_subprocess(tmp_path):
    target = tmp_path / "saved & chat.md"
    env = os.environ.copy()
    env["PYTHON_DOTENV_DISABLED"] = "1"
    env.pop("TEXTLLM_TEMPLATE_FILE", None)
    for args in (
        [str(target), "--title", "off", "--prompt", "First"],
        ["-c", "--title", "off", "--prompt", "Second"],
    ):
        proc = subprocess.run(
            [sys.executable, str(LOCAL_LAUNCHER), *args],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, proc.stderr
    assert "2 user messages" in proc.stdout


def test_state_updates_and_failed_operation(tmp_path, monkeypatch):
    from textllm.state import _last_conversation, _remember_conversation

    paths = [tmp_path / "regular.md", tmp_path / "temporary.md"]
    for path in paths:
        path.write_text("saved")
    for path, kind in zip(paths, ("regular", "temp")):
        _remember_conversation(path, kind)
    from textllm.state import _state_root
    saved = (_state_root() / "state.json").read_text()
    assert saved == json.dumps(
        {"last_regular": str(paths[0]), "last_temp": str(paths[1])}, indent=1
    ) + "\n"
    assert not list(_state_root().glob(".state-*.tmp"))
    assert _last_conversation() == str(paths[0])
    assert _last_conversation("temp") == str(paths[1])
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    broken = tmp_path / "broken.md"
    broken.write_text("# Broken\n\n--- Assistant ---\n\nNo prompt\n")
    with pytest.raises(textllm.NoUserMessageError):
        textllm.cli([str(broken), "--title", "off"])
    assert _last_conversation() == str(paths[0])


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("failure_stage", ["write", "replace"])
def test_state_write_failure_preserves_response(
    tmp_path, monkeypatch, capsys, existing, failure_stage
):
    from textllm import state

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    previous = tmp_path / "previous conversation.md"
    textllm.cli([str(previous)])
    target = tmp_path / "response & notes.md"
    original = "# Saved notes\n\n--- User ---\n\n"
    if existing:
        target.write_text(original)
    capsys.readouterr()
    def fail_writes(*args, **kwargs):
        raise PermissionError("state write unavailable")

    if failure_stage == "write":
        monkeypatch.setattr(state.json, "dump", fail_writes)
    else:
        monkeypatch.setattr(state.os, "replace", fail_writes)
    result = textllm.cli(
        [str(target), "--title", "off", "--prompt", "Keep this persisted"]
    )
    captured = capsys.readouterr()
    assert result.filepath == str(target)
    assert "Last message ended with: persisted" in captured.out
    assert (
        "WARNING: Could not remember conversation: state write unavailable"
        in captured.err
    )
    saved = target.read_text()
    assert "Keep this persisted" in saved
    assert "--- Assistant ---" in saved
    assert "Last message ended with: persisted" in saved
    assert saved.endswith("--- User ---\n\n")
    if existing:
        assert saved.startswith(original)
    assert state._last_conversation() == str(previous)
    assert not list(state._state_root().glob(".state-*.tmp"))


@pytest.mark.parametrize("temporary", [False, True])
def test_corrupt_state_fails_without_selecting_or_creating_files(tmp_path, temporary):
    from textllm.state import _state_root, _temp_directory

    _temp_directory().mkdir(parents=True)
    database = _state_root() / "state.json"
    corrupt = b"This is not JSON.\n"
    database.write_bytes(corrupt)
    for target in (
        tmp_path / "latest conversation.md",
        _temp_directory() / "candidate.md",
    ):
        target.write_text("# Saved\n\n--- User ---\n\nOriginal prompt\n")
    before = {path: path.read_bytes() for path in tmp_path.rglob("*.md")}
    env = os.environ.copy()
    env["PYTHON_DOTENV_DISABLED"] = "1"
    proc = subprocess.run(
        [
            sys.executable,
            str(LOCAL_LAUNCHER),
            "-c",
            *(["-t"] if temporary else []),
            "--prompt",
            "Must not append",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0
    assert "ERROR: Invalid continuation state JSON" in proc.stderr
    assert "deterministic test" not in proc.stderr
    assert proc.stdout == ""
    assert database.read_bytes() == corrupt
    assert {path: path.read_bytes() for path in tmp_path.rglob("*.md")} == before


@pytest.mark.parametrize("continuing", [False, True])
def test_temp_stream_failure_keeps_logged_path_and_partial_response(
    tmp_path, monkeypatch, capsys, continuing
):
    from textllm.state import _last_conversation, _temp_directory

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    regular = tmp_path / "regular.md"
    textllm.cli([str(regular)])
    if continuing:
        textllm.cli(["-t"])
        previous_temp = _last_conversation("temp")
    capsys.readouterr()
    streamed_paths = []

    def failing_events(**kwargs):
        target = Path(_last_conversation("temp"))
        streamed_paths.append(target)
        assert str(target) in capsys.readouterr().err
        assert "--- Assistant ---" not in target.read_text()
        yield textllm.ModelEvent("output_text_delta", text="Partial")
        assert target.read_text().endswith("Partial")
        yield textllm.ModelEvent("output_text_delta", text=" answer")
        assert target.read_text().endswith("Partial answer")
        raise RuntimeError("stream interrupted")

    monkeypatch.setattr(conversation, "iter_model_events", failing_events)
    with pytest.raises(RuntimeError, match="stream interrupted"):
        textllm.cli(
            [
                "-t",
                *(["-c"] if continuing else []),
                "--title",
                "off",
                "--prompt",
                "A recoverable prompt",
            ]
        )
    captured = capsys.readouterr()
    assert "Partial answer" in captured.out
    assert "ERROR: stream interrupted" in captured.err
    (target,) = streamed_paths
    assert target.parent == _temp_directory()
    assert _last_conversation("temp") == str(target)
    assert _last_conversation() == str(regular)
    if continuing:
        assert str(target) == previous_temp
    saved = target.read_text()
    assert "A recoverable prompt" in saved
    assert saved.count("--- Assistant ---") == 1
    assert saved.count("--- User ---") == 2
    assert "Partial answer" in saved
    assert saved.endswith("--- User ---\n\n")


def test_temp_creation_continuation_and_copy(tmp_path, monkeypatch, capsys):
    from textllm.state import _last_conversation, _temp_directory

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    regular = tmp_path / "regular notes.md"
    textllm.cli([str(regular)])
    convo = textllm.cli(["--temp", "--rename", "--prompt", "First question"])
    target = Path(convo.filepath)
    assert target.parent == _temp_directory()
    assert datetime.strptime(target.stem, "%Y%m%dT%H%M%S.%fZ").strftime(
        "%Y%m%dT%H%M%S.%fZ"
    ) == target.stem
    assert target.read_text().startswith("# title set automatically")
    assert str(target) in capsys.readouterr().err
    assert _last_conversation() == str(regular)
    assert _last_conversation("temp") == str(target)
    textllm.cli(["-t", "-c", "--move", "--prompt", "Second question"])
    assert "2 user messages" in target.read_text()
    textllm.cli(["-c", str(target), "--rename", "--prompt", "Third question"])
    assert target.exists()
    assert _last_conversation() == str(regular)
    saved = tmp_path / "Kept conversation.md"
    shutil.copy2(target, saved)
    textllm.cli([str(saved), "--prompt", "Fourth question"])
    assert _last_conversation() == str(saved)
    assert _last_conversation("temp") == str(target)


def test_temp_directory_environment(tmp_path, monkeypatch):
    from textllm.state import _state_root, _temp_directory

    default_root = _state_root()
    monkeypatch.setenv("TEXTLLM_TEMP_DIR", "")
    assert _state_root() == default_root
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("TEXTLLM_TEMP_DIR", "~/custom temp & storage")
    assert _state_root() == tmp_path / "custom temp & storage" / default_root.name
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TEXTLLM_TEMP_DIR", "relative temp")
    assert _state_root() == tmp_path / "relative temp" / default_root.name
    assert _temp_directory() == _state_root() / "conversations"
    assert not _state_root().exists()


def test_temp_directory_from_env_file_subprocess(tmp_path):
    custom = tmp_path / "custom temp & storage"
    env_file = tmp_path / "settings.env"
    env_file.write_text(f'TEXTLLM_TEMP_DIR="{custom}"\n')
    env = os.environ.copy()
    env.pop("TEXTLLM_TEMPLATE_FILE", None)
    env.pop("PYTHON_DOTENV_DISABLED", None)
    env["TEXTLLM_TEST_MODE"] = "1"
    for extra in ([], ["-c"]):
        proc = subprocess.run(
            [sys.executable, str(LOCAL_LAUNCHER), "--env", str(env_file),
             "--temp", *extra, "--prompt", "Hello"],
            cwd=tmp_path, env=env, capture_output=True, text=True,
        )
        assert proc.returncode == 0, proc.stderr
        assert str(custom) in proc.stderr
    assert "2 user messages" in proc.stdout
    assert len(list(custom.glob("textllm-*/conversations/*.md"))) == 1
    assert len(list(custom.glob("textllm-*/state.json"))) == 1
    assert not list(tmp_path.glob("textllm-*/state.json"))


def test_temp_collision_missing_state_and_failures(tmp_path, monkeypatch, capsys):
    from textllm.state import _last_conversation, _temp_directory

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    with pytest.raises(ValueError, match="No last temp"):
        textllm.cli(["--temp", "-c"])
    with pytest.raises(SystemExit):
        textllm.cli(["--temp", "explicit.md"])
    with pytest.raises(ValueError):
        textllm.cli(["--temp", "--template", "@unregistered"])
    assert not _temp_directory().exists()
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz):
            assert tz is UTC
            return cls(2026, 11, 19, 19, 1, 32, 123456, tzinfo=tz)

    monkeypatch.setattr(cli_module, "datetime", FixedDatetime)
    textllm.cli(["--temp"])
    original = Path(_last_conversation("temp"))
    assert original.name == "20261119T190132.123456Z.md"
    original_text = original.read_text()

    def fail_title(self):
        # The path must already be visible and remembered before a model call.
        assert _last_conversation("temp").endswith("20261119T190132.123457Z.md")
        assert "20261119T190132.123457Z.md" in capsys.readouterr().err
        raise RuntimeError("model failed")

    monkeypatch.setattr(textllm.Conversation, "set_title", fail_title)
    with pytest.raises(RuntimeError, match="model failed"):
        textllm.cli(["--temp", "--prompt", "Question"])
    assert original.read_text() == original_text
    failed = Path(_last_conversation("temp"))
    assert failed.exists()
    failed.unlink()
    with pytest.raises(ValueError, match="is missing"):
        textllm.cli(["--temp", "-c"])


def test_temp_subprocess(tmp_path):
    env = os.environ.copy()
    env["PYTHON_DOTENV_DISABLED"] = "1"
    env.pop("TEXTLLM_TEMPLATE_FILE", None)
    for extra in ([], ["-c", "--rename"]):
        proc = subprocess.run(
            [sys.executable, str(LOCAL_LAUNCHER), "-t", *extra, "--prompt", "Hello"],
            cwd=tmp_path, env=env, capture_output=True, text=True,
        )
        assert proc.returncode == 0, proc.stderr
        assert "Temporary conversation:" in proc.stderr
    assert "2 user messages" in proc.stdout
    assert not list(tmp_path.glob("*.md"))


def test_settings_merge_replaces_arrays_and_preserves_inputs():
    base = {"nested": {"keep": 1, "replace": [1]}, "scalar": 1, "table": {"a": 2}}
    override = {"nested": {"replace": [2]}, "scalar": {"b": 3}, "table": False}
    merged = config._merge_settings(base, override)
    assert merged == {
        "nested": {"keep": 1, "replace": [2]},
        "scalar": {"b": 3},
        "table": False,
    }
    merged["nested"]["replace"].append(3)
    assert base["nested"]["replace"] == [1]
    assert override["nested"]["replace"] == [2]


@pytest.mark.parametrize("backend", ["completion", "responses", "messages"])
def test_overrides_share_effective_settings_without_persisting(
    tmp_path, monkeypatch, backend
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    target = tmp_path / "Override & notes.md"
    target.write_text(
        '# !!AUTO TITLE!!\n\n```toml\nmodel = "original/model"\ntemperature = 0.8\n'
        'reasoning = { effort = "low", summary = "auto" }\n'
        'tools = [{type = "original"}]\ntool_choice = "auto"\n'
        '[textllm]\nbackend = "completion"\nheader_level = 1\ntokens = "none"\n'
        '[textllm.title]\nmodel = "original/title"\n'
        'reasoning = {effort = "minimal"}\n```\n\n--- User ---\n\nQuestion\n'
    )
    saved_settings = textllm.Conversation.read_settings(target.read_text())
    calls = []

    def events(**kwargs):
        calls.append(kwargs)
        yield textllm.ModelEvent("reasoning_summary_delta", text="Thinking")
        yield textllm.ModelEvent("output_text_delta", text="Answer")
        yield textllm.ModelEvent("usage", data={"input_tokens": 2, "output_tokens": 3})
        yield textllm.ModelEvent("completed")

    monkeypatch.setattr(conversation, "iter_model_events", events)
    convo = textllm.cli(
        [
            str(target),
            "-o",
            'model = "override/chat"',
            "-o",
            "temperature = 0.2",
            "--override",
            "temperature = 0.4",
            "-o",
            'reasoning.effort = "high"',
            "-o",
            'tools = [{type = "replacement"}]',
            "-o",
            f'textllm.backend = "{backend}"',
            "-o",
            "textllm.header_level = 2",
            "-o",
            'textllm.tokens = "note"',
            "-o",
            'textllm.reasoning_summary = "block"',
            "-o",
            'textllm.title.model = "override/title"',
            "-o",
            'textllm.title.reasoning.effort = "medium"',
            "-o",
            'textllm.title.tools = [{type = "forbidden"}]',
            "-o",
            'textllm.title.tool_choice = "required"',
        ]
    )
    title, chat = calls
    assert title["model"] == "override/title"
    assert chat["model"] == "override/chat"
    assert title["settings"]["reasoning"] == {"effort": "medium", "summary": "auto"}
    assert chat["settings"]["reasoning"] == {"effort": "high", "summary": "auto"}
    assert chat["settings"]["tools"] == [{"type": "replacement"}]
    for call in calls:
        assert call["backend"] == backend
        assert call["settings"]["temperature"] == 0.4
        assert "textllm" not in call["settings"]
    assert "tools" not in title["settings"]
    assert "tool_choice" not in title["settings"]
    assert "stream_options" not in title["settings"]
    text = target.read_text()
    assert "## --- Reasoning ---" in text
    assert "## --- Note ---" in text
    assert "input_tokens: 2" in text
    assert text.endswith("## --- User ---\n\n")
    assert convo.parsed["settings"] == saved_settings
    assert textllm.Conversation.read_settings(text) == saved_settings
    textllm.cli([str(target), "--prompt", "Another question"])
    assert calls[-1]["model"] == "original/model"
    assert calls[-1]["backend"] == "completion"
    assert calls[-1]["settings"]["temperature"] == 0.8
    assert calls[-1]["settings"]["tools"] == [{"type": "original"}]
    assert target.read_text().endswith("# --- User ---\n\n")


@pytest.mark.parametrize("fragment", ["temperature =", "a = 1\na = 2", "textllm = 3"])
def test_override_errors_before_mutation(tmp_path, monkeypatch, fragment):
    from textllm.state import _state_root

    monkeypatch.chdir(tmp_path)
    target = tmp_path / "existing.md"
    target.write_text("# Saved\n\n--- User ---\n\nOriginal\n")
    for args in ([str(target)], ["new.md"], ["--temp"]):
        with pytest.raises(SystemExit):
            textllm.cli([*args, "--prompt", "Must not append", "-o", fragment])
    assert target.read_text().endswith("Original\n")
    assert not Path("new.md").exists()
    assert not _state_root().exists()


def test_override_header_and_dedicated_flags(template_env, monkeypatch):
    def events(**kwargs):
        yield textllm.ModelEvent("output_text_delta", text="Answer")
        yield textllm.ModelEvent("usage", data={"input_tokens": 2})
        yield textllm.ModelEvent("completed")

    monkeypatch.setattr(conversation, "iter_model_events", events)
    monkeypatch.setenv("TEXTLLM_DEFAULT_HEADER_LEVEL", "5")
    for name, template in (
        ("builtin", None),
        (
            "placeholder",
            "# Saved\n\n```toml\n[textllm]\nheader_level = 1\n```\n\n{header}--- User ---\n",
        ),
        ("literal", "# Saved\n\n--- User ---\n"),
    ):
        extra = []
        if template is not None:
            Path("template.md").write_text(template)
            extra = ["--template", "template.md"]
        textllm.cli([f"{name}.md", *extra, "-o", "textllm.header_level = 2"])
        expected = "--- User ---" if name == "literal" else "## --- User ---"
        assert Path(f"{name}.md").read_text().rstrip().endswith(expected)
        textllm.cli(
            [
                f"{name}.md",
                "--prompt",
                "Hello",
                "--tokens",
                "none",
                "--header-level",
                "3",
                "-o",
                "textllm.header_level = 2",
                "-o",
                'textllm.tokens = "note"',
            ]
        )
        assert Path(f"{name}.md").read_text().endswith("### --- User ---\n\n")
        assert "--- Note ---" not in Path(f"{name}.md").read_text()
    textllm.cli(["initial.md", "-o", "textllm.header_level = 2", "--header-level", "0"])
    assert Path("initial.md").read_text().rstrip().endswith("\n--- User ---")


def test_override_temp_subprocess(tmp_path):
    env = os.environ.copy()
    env["PYTHON_DOTENV_DISABLED"] = "1"
    env.pop("TEXTLLM_TEMPLATE_FILE", None)
    for extra in ([], ["-c"]):
        proc = subprocess.run(
            [
                sys.executable,
                str(LOCAL_LAUNCHER),
                "--temp",
                *extra,
                "--prompt",
                "Hello",
                "-o",
                'textllm.backend = "responses"',
                "-o",
                "textllm.header_level = 2",
            ],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, proc.stderr
    assert "2 user messages" in proc.stdout
    from textllm.state import _last_conversation

    assert (
        Path(_last_conversation("temp")).read_text().endswith("## --- User ---\n\n")
    )


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
    assert "List template names and exit. Repeat to include paths," in proc.stdout
    assert "sources, and shadowed entries." in proc.stdout


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
        assert text.endswith("--- User ---\n\n")
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
        assert text.endswith("--- User ---\n\n")
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
        assert text.endswith("--- User ---\n\n")
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
        assert text.endswith("--- User ---\n\n")
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
        assert text.endswith("--- User ---\n\n")
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
        assert text.endswith("--- User ---\n\n")
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
    assert text.endswith(f"{expected_user}\n\n")


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


@pytest.mark.parametrize("count", [1, 2, 3])
def test_named_template_listing(template_env, monkeypatch, capsys, count):
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
    textllm.cli(["--list-templates"] * count + ["--stdin", "--edit"])
    out = capsys.readouterr().out
    if count >= 2:
        assert '"@openai" -> "override.md" [TEXTLLM_NAMED_TEMPLATE_openai]' in out
        assert 'overrides "templates/openai.md"' in out
    else:
        assert out.splitlines() == [
            '"@cash $ (notes)"',
            '"@link"',
            '"@openai"',
            '"@writing help"',
        ]
    assert '"@link"' in out
    assert '"@writing help"' in out
    assert '"@cash $ (notes)"' in out
    assert not any(name in out for name in ["hidden", "nested", "upper", "folder"])
    assert not Path(textllm.DEFAULT_FILEPATH).exists()
    assert not Path("log").exists()
    textllm.cli(["spaces.md", "--template", "@writing help"])
    assert Path("spaces.md").read_text() == "# Template\n"


@pytest.mark.parametrize("count", [1, 2])
def test_named_template_listing_errors_and_empty(template_env, monkeypatch, capsys, count):
    textllm.cli(["--list-templates"] * count)
    assert "No named templates configured" in capsys.readouterr().out
    Path("valid.md").write_text("# Valid\n")
    monkeypatch.setenv("TEXTLLM_NAMED_TEMPLATE_valid", "valid.md")
    monkeypatch.setenv("TEXTLLM_NAMED_TEMPLATE_broken", "missing.md")
    monkeypatch.setenv("TEXTLLM_NAMED_TEMPLATE_empty", "")
    monkeypatch.setenv("TEXTLLM_TEMPLATE_DIR", "missing-directory")
    with pytest.raises(SystemExit) as exc:
        textllm.cli(["--list-templates"] * count)
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
        (["--list-templates", "--list-templates"], 0),
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
            if args.count("--list-templates") == 1:
                assert proc.stdout == '"@special"\n'
            else:
                assert (
                    '"@special" -> "template.md" [TEXTLLM_NAMED_TEMPLATE_special]'
                    in proc.stdout
                )
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
    monkeypatch.setattr(config.log, "_cache", {})
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


def test_llm_namespace_normalization():
    original = {
        "model": "old/model", "reasoning": {"effort": "low", "summary": "auto"},
        "tools": [{"type": "old"}],
        "llm": {"model": "new/model", "reasoning": {"effort": "high"},
                "tools": [{"type": "new"}]},
        "textllm": {"backend": "responses"},
    }
    normalized = config._normalize_settings(original)
    assert normalized == {
        "model": "new/model", "reasoning": {"effort": "high", "summary": "auto"},
        "tools": [{"type": "new"}], "textllm": {"backend": "responses"},
    }
    normalized["tools"][0]["type"] = "changed"
    assert original["llm"]["tools"][0]["type"] == "new"


@pytest.mark.parametrize("settings", [{"llm": 1}, {"textllm": False},
    {"llm": {"textllm": {}}}, {"llm": {"llm": {}}}])
def test_invalid_llm_namespace(settings):
    with pytest.raises(ValueError):
        config._normalize_settings(settings)


@pytest.mark.parametrize("backend", ["completion", "responses", "messages"])
def test_llm_namespace_requests(tmp_path, monkeypatch, caplog, backend):
    path = tmp_path / "namespace.md"
    source = f'''# !!AUTO TITLE!!

```toml
model = "old/model"
[llm]
model = "openai/test"
max_tokens = 100
reasoning = {{ effort = "high" }}
tools = [{{ type = "function" }}]
tool_choice = "auto"
[textllm]
backend = "{backend}"
[textllm.title]
model = "openai/title"
```

--- User ---

Hello
'''
    path.write_text(source)
    requests = []
    def capture(**kwargs):
        requests.append(kwargs)
        yield backends.ModelEvent("output_text_delta", "Hello")
    # No transport is invoked: inspect the normalized request boundary.
    monkeypatch.setattr(conversation, "iter_model_events", capture)
    convo = textllm.Conversation(path)
    convo.call_llm(convo.messages)
    convo.call_llm(convo.messages, title_call=True)
    chat, title = requests
    assert chat["model"] == "openai/test"
    assert title["model"] == "openai/title"
    assert chat["settings"]["reasoning"] == {"effort": "high"}
    assert "tools" in chat["settings"]
    assert "tools" not in title["settings"] and "tool_choice" not in title["settings"]
    assert all("llm" not in r["settings"] and "textllm" not in r["settings"] for r in requests)
    assert sum("Root-level model settings are deprecated" in r.message for r in caplog.records) == 1
    assert path.read_text() == source


@pytest.mark.parametrize("fragments, expected, warned", [
    (["llm.temperature=0.1", "temperature=0.7"], 0.7, True),
    (["temperature=0.7", "llm.temperature=0.1"], 0.1, True),
    (["llm.temperature=0.1", "llm.temperature=0.2"], 0.2, False),
])
def test_llm_override_order_subprocess(tmp_path, fragments, expected, warned):
    path = tmp_path / "chat with spaces & symbols.md"
    source = '# Fixed title\n\n```toml\n[llm]\nmodel="openai/test"\ntemperature=0.9\n```\n\n--- User ---\n\nHello\n'
    path.write_text(source)
    env = os.environ.copy()
    env["PYTHON_DOTENV_DISABLED"] = "1"
    args = [sys.executable, str(LOCAL_LAUNCHER), str(path), "--title", "off", "-v"]
    for fragment in fragments:
        args.extend(["-o", fragment])
    result = subprocess.run(args, env=env, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert f"'temperature': {expected}" in result.stderr
    assert result.stderr.count("Root-level model settings are deprecated") == int(warned)
    assert source in path.read_text()


def test_invalid_llm_override_before_mutation(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit):
        textllm.cli(["new.md", "-o", "llm=5"])
    assert not (tmp_path / "new.md").exists()


def test_repository_templates_use_llm_namespace():
    root = Path(__file__).resolve().parent
    for path in (root / "examples" / "templates").glob("*.md"):
        settings = textllm.Conversation.read_settings(config.CONFIG.render_template(path.read_text()))
        assert "llm" in settings, path
        assert not config._has_legacy_settings(settings), path
        assert config._normalize_settings(settings)["model"]


def test_invalid_llm_template_before_creation(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    template = tmp_path / "invalid template.md"
    template.write_text('# Title\n\n```toml\nllm=5\n```\n\n--- User ---\n')
    with pytest.raises(ValueError, match="llm must be a TOML table"):
        textllm.cli(["new.md", "--template", str(template)])
    assert not (tmp_path / "new.md").exists()


def test_llm_nested_toml_tables_and_arrays():
    settings = textllm.Conversation.read_settings('''```toml
[llm]
model = "openai/test"
[llm.reasoning]
effort = "high"
[[llm.tools]]
type = "web_search"
[textllm]
backend = "responses"
```''')
    normalized = config._normalize_settings(settings)
    assert normalized["reasoning"] == {"effort": "high"}
    assert normalized["tools"] == [{"type": "web_search"}]
    assert not config._has_legacy_settings(settings)


# MCP and sidecar tests use only deterministic local fixtures.
MCP_FIXTURE = Path(__file__).resolve().parent / "testresources" / "mcp" / "server.py"


def _sidecar_records(path):
    from textllm.history import Sidecar

    db = Sidecar(path)._open()
    try:
        return list(db)
    finally:
        db.close()


def test_mcp_configuration_expands_once_and_preserves_arguments(monkeypatch):
    from textllm.tools import mcp_config

    monkeypatch.setenv("MCP_INDEX", "/path with spaces/${UNCHANGED}")
    monkeypatch.setenv("MCP_TOKEN", "secret-${UNCHANGED}")
    entries = [
        {
            "command": 'search --index "${MCP_INDEX}"',
            "env": {"LITERAL": "$${MCP_INDEX}"},
        },
        {"url": "https://example.com/mcp", "bearer_token": "${MCP_TOKEN}"},
    ]
    servers = mcp_config(entries)
    assert servers[0]["command"] == [
        "search",
        "--index",
        "/path with spaces/${UNCHANGED}",
    ]
    assert servers[0]["env"] == {"LITERAL": "${MCP_INDEX}"}
    assert servers[1]["token"] == "secret-${UNCHANGED}"
    assert entries[0]["command"].endswith('"${MCP_INDEX}"')


@pytest.mark.parametrize(
    "entries",
    [
        {},
        [{}],
        [{"command": []}],
        [{"command": [12]}],
        [{"command": "run", "url": "https://example.com"}],
        [{"url": "file:///tmp/server"}],
        [{"url": "https://user:pass@example.com"}],
        [{"command": "run", "bearer_token": "token"}],
        [{"url": "https://example.com", "bearer_token": ""}],
        [{"url": "https://example.com", "bearer_token": 123}],
        [{"url": "https://example.com", "bearer_token_env": "KEY"}],
        [
            {
                "url": "https://example.com",
                "bearer_token": "${TEXTLLM_MISSING_MCP_TEST_VARIABLE}",
            }
        ],
        [{"url": "https://example.com", "env": {}}],
        [{"command": "run", "name": "a"}, {"command": "run", "name": "a"}],
        [{"command": ["${TEXTLLM_MISSING_MCP_TEST_VARIABLE}"]}],
    ],
)
def test_mcp_invalid_configuration(entries, monkeypatch):
    from textllm.tools import mcp_config

    monkeypatch.delenv("TEXTLLM_MISSING_MCP_TEST_VARIABLE", raising=False)
    with pytest.raises(ValueError):
        mcp_config(entries)


def test_mcp_stdio_discovery_execution_validation_and_cleanup():
    from textllm.tools import MCPExecutor, mcp_config, model_result
    from jsonschema import ValidationError

    config_entries = mcp_config(
        [
            {
                "command": [sys.executable, str(MCP_FIXTURE)],
                "env": {"TEXTLLM_MCP_FIXTURE": "fixture & value"},
            }
        ]
    )
    with MCPExecutor(config_entries, timeout=10) as executor:
        assert len(executor.tools) == 3
        name = next(t["name"] for t in executor.tools if "__echo_" in t["name"])
        call = {"id": "a", "name": name, "arguments": '{"value":"hello"}'}
        result = executor.execute(call, executor.validate(call))
        output, error = model_result(result, 1000)
        assert json.loads(output) == {"value": "hello"}
        assert not error
        with pytest.raises(ValidationError):
            executor.validate(call | {"arguments": '{"value":42}'})
        env_name = next(
            t["name"] for t in executor.tools if "__environment_" in t["name"]
        )
        result = executor.execute({"name": env_name}, {})
        output, error = model_result(result, 1000)
        assert json.loads(output) == {"result": "fixture & value"}
        assert not error
        failure = next(t["name"] for t in executor.tools if "__failure_" in t["name"])
        assert executor.execute({"name": failure}, {})["isError"]


@pytest.mark.parametrize("random_bits", [0, 1, (1 << 74) - 1])
def test_sidecar_uuid7_layout_and_timestamp_order(monkeypatch, random_bits):
    from uuid import RFC_4122
    from textllm import history

    timestamp_ms = 0x017F22E279B0
    monkeypatch.setattr(history.time, "time_ns", lambda: timestamp_ms * 1_000_000)

    def randbits(count):
        assert count == 74
        return random_bits

    monkeypatch.setattr(history.secrets, "randbits", randbits)
    first = history._uuid7()
    assert first.version == 7
    assert first.variant == RFC_4122
    assert first.int >> 80 == timestamp_ms
    extracted_random = (((first.int >> 64) & 0xFFF) << 62) | (
        first.int & ((1 << 62) - 1)
    )
    assert extracted_random == random_bits
    timestamp_ms += 1
    assert str(first) < str(history._uuid7())


def test_sidecar_uuid7_randomness_within_millisecond(monkeypatch):
    from textllm import history

    monkeypatch.setattr(history.time, "time_ns", lambda: 1_700_000_000_000_000_000)
    values = iter([1, 2, 1 << 62])
    monkeypatch.setattr(history.secrets, "randbits", lambda count: next(values))
    keys = [history._uuid7() for _ in range(3)]
    assert len(set(keys)) == 3
    assert len({key.int >> 80 for key in keys}) == 1


def test_sidecar_uuid_persistence_and_wal_override(tmp_path, monkeypatch):
    import sqlite3
    from uuid import UUID
    from jsonlitedb import JSONLiteDB
    from textllm.history import Sidecar

    path = tmp_path / "chat & symbols.md"
    db = JSONLiteDB(str(path) + ".db")
    assert db.db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    db.close()
    sidecar = Sidecar(path)
    record = {"kind": "reasoning", "text": "saved"}
    key = sidecar.save(record)
    assert UUID(key).version == 7
    # Updating existing records must preserve IDs without generating new ones.
    monkeypatch.setattr("textllm.history._uuid7", lambda: pytest.fail("New ID"))
    record["text"] = "updated"
    sidecar.save(record)
    assert sidecar.read(key)["text"] == "updated"
    existing_key = "827bc021-6da3-45fe-95c1-ec37a876ca9c"
    sidecar.save({"id": existing_key, "kind": "reasoning", "text": "existing"})
    assert sidecar.read(existing_key)["text"] == "existing"
    with sqlite3.connect(str(path) + ".db") as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    connection.close()
    assert not Path(str(path) + ".db-wal").exists()
    with pytest.raises(ValueError, match="Unknown"):
        sidecar.read("827bc021-6da3-45fe-95c1-ec37a876ca9b")
    with pytest.raises(ValueError, match="Missing"):
        Sidecar(tmp_path / "missing.md").read(key)
    assert not (tmp_path / "missing.md.db").exists()


@pytest.mark.parametrize("backend", ["completion", "responses", "messages"])
@pytest.mark.parametrize("reasoning_mode", ["omit", "stderr", "block"])
def test_mcp_tool_loop_replay_and_markdown_edits(
    tmp_path, monkeypatch, capsys, backend, reasoning_mode
):
    from textllm.history import Sidecar, references

    # Explicit stderr output must survive quiet/disabled informational logging.
    monkeypatch.setattr(conversation.log, "disabled", True)
    path = tmp_path / "MCP & conversation.md"
    path.write_text(
        '# MCP\n\n```toml\n[llm]\nmodel="test/model"\nmax_tokens=1000\n'
        f'[textllm]\nbackend="{backend}"\nreasoning_summary="{reasoning_mode}"\n'
        "[[textllm.mcp]]\ncommand="
        + json.dumps([sys.executable, str(MCP_FIXTURE)])
        + "\n```\n\n--- User ---\n\nFind hello\n"
    )
    requests = []

    def events(**kwargs):
        requests.append(kwargs)
        if len(requests) == 1:
            assert kwargs["settings"]["tools"]
            definition = next(
                t for t in kwargs["settings"]["tools"] if "__echo_" in str(t)
            )
            name = definition.get("function", definition)["name"]
            call = {"id": "call_a", "name": name, "arguments": '{"value":"hello"}'}
            yield backends.ModelEvent("output_text_delta", text="Checking.")
            yield backends.ModelEvent(
                "reasoning_summary_delta", text="Private reasoning."
            )
            if backend == "completion":
                native = {
                    "content": "Checking.",
                    "tool_calls": [
                        {
                            "id": call["id"],
                            "type": "function",
                            "function": {"name": name, "arguments": call["arguments"]},
                        }
                    ],
                    "reasoning_content": "Private reasoning.",
                }
            elif backend == "responses":
                native = {
                    "output": [
                        {"type": "reasoning", "id": "r1", "summary": []},
                        {
                            "type": "function_call",
                            "call_id": call["id"],
                            "name": name,
                            "arguments": call["arguments"],
                        },
                    ]
                }
            else:
                native = {
                    "content": [
                        {
                            "type": "thinking",
                            "thinking": "Private reasoning.",
                            "signature": "sig",
                        },
                        {
                            "type": "tool_use",
                            "id": call["id"],
                            "name": name,
                            "input": {"value": "hello"},
                        },
                    ],
                    "stop_reason": "tool_use",
                }
            yield backends.ModelEvent("completed", data=native)
        else:
            assert kwargs["messages"][-1]["role"] in ("tool", "user")
            yield backends.ModelEvent("output_text_delta", text="Found hello.")
            yield backends.ModelEvent("completed")

    monkeypatch.setattr(conversation, "iter_model_events", events)
    textllm.Conversation(path).chat()
    captured = capsys.readouterr()
    assert ("Private reasoning." in captured.err) == (reasoning_mode == "stderr")
    assert "Private reasoning." not in captured.out
    written = path.read_text()
    assert "--- Tools ---" in written
    assert ("Private reasoning." in written) == (reasoning_mode == "block")
    assert ("--- Reasoning ---" in written) == (reasoning_mode == "block")
    assert written.count("--- Tools ---") == 1
    assert "[@sidecar reasoning]" in written
    assert "[@sidecar tool exchange]" in written
    parsed = textllm.Conversation(path)
    assert [m["role"] for m in parsed.messages] == [
        "user",
        "assistant",
        "tool",
        "assistant",
    ]
    assert parsed.messages[1]["content"] == "Checking."
    assert json.loads(parsed.messages[2]["content"]) == {"value": "hello"}
    keys = references(written)
    assert len(keys) == 2
    assert Sidecar(path).read(keys[0])["text"] == "Private reasoning."
    exchange = Sidecar(path).read(keys[1])
    assert exchange["status"] == "complete"
    assert exchange["calls"][0]["executor"] == "mcp"
    if reasoning_mode == "omit":
        # Separate CLI processes must replay calls/results without replaying the
        # saved schemas, even when the current MCP configuration is removed.
        assert exchange["definitions"]
        original_definitions = json.loads(json.dumps(exchange["definitions"]))
        stored_marker = "HISTORICAL_DEFINITION_MUST_STAY_LOCAL"
        for definition in exchange["definitions"]:
            definition["description"] = stored_marker
        Sidecar(path).save(exchange)
        capture = tmp_path / "captured request.json"
        script = dedent("""\
            import json
            import sys
            from pathlib import Path
            import textllm
            from textllm import backends, conversation

            def events(**kwargs):
                Path(sys.argv[2]).write_text(json.dumps(kwargs))
                yield backends.ModelEvent("output_text_delta", text="Follow-up answer.")
                yield backends.ModelEvent("completed")

            conversation.iter_model_events = events
            textllm.cli([
                sys.argv[1], "--title", "off", "--prompt", "Follow up",
                *sys.argv[3:],
            ])
        """)
        env = os.environ.copy()
        env["PYTHON_DOTENV_DISABLED"] = "1"
        env["TEXTLLM_TEST_MODE"] = "1"
        for configured in (True, False):
            proc = subprocess.run(
                [
                    sys.executable, "-c", script, str(path), str(capture),
                    *([] if configured else ["-o", "[textllm]\nmcp=[]"]),
                ],
                cwd=LOCAL_LAUNCHER.parent,
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )
            assert proc.returncode == 0, proc.stderr
            request = json.loads(capture.read_text())
            assert stored_marker not in json.dumps(request)
            assert any(m["role"] == "tool" for m in request["messages"])
            assert any("_native" in m for m in request["messages"])
            if configured:
                from textllm.tools import tool_definitions

                # Current schemas are sent once, with no historical copies.
                assert request["settings"]["tools"] == tool_definitions(
                    original_definitions, backend
                )
            else:
                assert "tools" not in request["settings"]
            assert Sidecar(path).read(keys[1]) == exchange
    path.write_text(written.replace("Checking.", "Edited explanation."))
    assert textllm.Conversation(path).messages[1]["content"] == "Edited explanation."
    # Deleting both reference blocks excludes the original native exchange.
    path.write_text(
        written.split("<!--\n--- Tools ---")[0] + "\n\n--- User ---\n\nNew branch\n"
    )
    assert all("_native" not in m for m in textllm.Conversation(path).messages)


def test_mcp_native_backend_streams_and_replay(monkeypatch):
    import any_llm

    monkeypatch.setattr(config, "TEST_MODE", False)
    monkeypatch.delenv("TEXTLLM_TEST_MODE", raising=False)
    requests = []

    def completion(**kwargs):
        requests.append(kwargs)
        yield {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "c1",
                                "function": {"name": "echo", "arguments": '{"v":'},
                            }
                        ]
                    }
                }
            ]
        }
        yield {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [{"index": 0, "function": {"arguments": '"hi"}'}}]
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        }

    monkeypatch.setattr(any_llm, "completion", completion)
    events = list(
        backends.iter_model_events(
            backend="completion",
            model="openai/test",
            messages=[{"role": "user", "content": "hi"}],
            settings={},
        )
    )
    native, calls = backends.native_turn("completion", events[-1].data)
    assert calls == [{"id": "c1", "name": "echo", "arguments": '{"v":"hi"}'}]
    messages = [
        {
            "role": "assistant",
            "content": "Edited",
            "_native": native,
            "_backend": "completion",
        },
        {"role": "tool", "tool_call_id": "c1", "content": "result", "is_error": False},
    ]
    list(
        backends.iter_model_events(
            backend="completion", model="openai/test", messages=messages, settings={}
        )
    )
    assert requests[-1]["messages"][0]["content"] == "Edited"
    assert "_native" not in repr(requests[-1])

    def responses(**kwargs):
        requests.append(kwargs)
        yield {
            "type": "response.output_item.done",
            "item": {
                "type": "reasoning",
                "id": "r1",
                "encrypted_content": "opaque",
                "summary": [],
            },
        }
        yield {
            "type": "response.output_item.done",
            "item": {
                "type": "function_call",
                "call_id": "c1",
                "name": "echo",
                "arguments": "{}",
            },
        }
        yield {"type": "response.completed", "response": {}}

    monkeypatch.setattr(any_llm, "responses", responses)
    events = list(
        backends.iter_responses_events(model="openai/test", messages=[], settings={})
    )
    native, calls = backends.native_turn("responses", events[-1].data)
    assert calls[0]["id"] == "c1"
    replay = [
        {
            "role": "assistant",
            "content": "",
            "_native": native,
            "_backend": "responses",
        },
        {"role": "tool", "tool_call_id": "c1", "content": "result"},
    ]
    list(
        backends.iter_responses_events(
            model="openai/test", messages=replay, settings={}
        )
    )
    assert requests[-1]["input_data"] == native + [
        {"type": "function_call_output", "call_id": "c1", "output": "result"}
    ]

    def messages_stream(**kwargs):
        requests.append(kwargs)
        yield {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "thinking", "thinking": ""},
        }
        yield {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "thinking_delta", "thinking": "Think"},
        }
        yield {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "signature_delta", "signature": "signed"},
        }
        yield {
            "type": "content_block_start",
            "index": 1,
            "content_block": {
                "type": "tool_use",
                "id": "c1",
                "name": "echo",
                "input": {},
            },
        }
        yield {
            "type": "content_block_delta",
            "index": 1,
            "delta": {"type": "input_json_delta", "partial_json": '{"v":"hello"}'},
        }
        yield {"type": "message_delta", "delta": {"stop_reason": "tool_use"}}
        yield {"type": "message_stop"}

    monkeypatch.setattr(any_llm, "messages", messages_stream)
    events = list(
        backends.iter_messages_events(
            model="anthropic/test", messages=[], settings={"max_tokens": 1000}
        )
    )
    native, calls = backends.native_turn("messages", events[-1].data)
    assert native[0] == {"type": "thinking", "thinking": "Think", "signature": "signed"}
    assert calls[0]["arguments"] == '{"v": "hello"}'
    replay = [
        {"role": "assistant", "content": "", "_native": native, "_backend": "messages"},
        {"role": "tool", "tool_call_id": "c1", "content": "result", "is_error": True},
    ]
    list(
        backends.iter_messages_events(
            model="anthropic/test", messages=replay, settings={"max_tokens": 1000}
        )
    )
    assert requests[-1]["messages"][0]["content"] == native
    assert requests[-1]["messages"][1]["content"][0]["is_error"] is True


@pytest.mark.parametrize("authentication", ["none", "environment", "literal"])
@pytest.mark.parametrize("pages", [1, 2])
def test_mcp_streamable_http_and_bearer(monkeypatch, caplog, authentication, pages):
    import httpx2
    from textllm.tools import MCPExecutor, mcp_config

    caplog.set_level("DEBUG", logger="textllm")
    requests = []

    def handler(request):
        payload = json.loads(request.content)
        requests.append((request, payload))
        method = payload["method"]
        if "id" not in payload:
            return httpx2.Response(202)
        if method == "server/discover":
            return httpx2.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": payload["id"],
                    "error": {"code": -32601, "message": "Legacy fixture"},
                },
            )
        if method == "initialize":
            result = {
                "protocolVersion": payload["params"]["protocolVersion"],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "fixture", "version": "1"},
            }
        elif method == "tools/list":
            page = 2 if payload.get("params", {}).get("cursor") else 1
            result = {
                "tools": [
                    {
                        "name": f"echo{page}",
                        "description": "Echo",
                        "inputSchema": {"type": "object"},
                    }
                ]
            }
            if pages == 2 and page == 1:
                result["nextCursor"] = "private-pagination-cursor"
        else:
            assert method == "tools/call"
            result = {"content": [{"type": "text", "text": "HTTP hello"}]}
        return httpx2.Response(
            200, json={"jsonrpc": "2.0", "id": payload["id"], "result": result}
        )

    original = httpx2.AsyncClient
    monkeypatch.setattr(
        httpx2,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx2.MockTransport(handler), **kwargs),
    )
    entry = {"url": "https://fixture.invalid/mcp"}
    if authentication == "environment":
        monkeypatch.setenv("FIXTURE_BEARER", "secret-${literal}")
        entry["bearer_token"] = "${FIXTURE_BEARER}"
    elif authentication == "literal":
        entry["bearer_token"] = "literal-token"
    with MCPExecutor(mcp_config([entry]), timeout=5) as executor:
        assert len(executor.tools) == pages
        name = executor.tools[0]["name"]
        assert (
            executor.execute({"name": name}, {})["content"][0]["text"] == "HTTP hello"
        )
    logs = "\n".join(r.getMessage() for r in caplog.records if r.name == "textllm")
    assert "MCP connection/session initialization started: 'server_1'" in logs
    assert "MCP connection/session initialization completed: 'server_1'" in logs
    for page in range(1, pages + 1):
        assert f"MCP tools/list page {page} started: 'server_1'" in logs
        assert f"MCP tools/list page {page} completed: 'server_1'" in logs
        assert f"MCP tools/list page {page}: 'server_1' (1 tools," in logs
    assert f"MCP discovery completed: 'server_1' ({pages} tools, {pages} pages)" in logs
    for secret in (
        entry["url"],
        "secret-${literal}",
        "literal-token",
        "private-pagination-cursor",
    ):
        assert secret not in logs
    for request, _ in requests:
        assert request.headers.get("Authorization") == (
            {
                "none": None,
                "environment": "Bearer secret-${literal}",
                "literal": "Bearer literal-token",
            }[authentication]
        )


@pytest.mark.parametrize("failure", [None, "connection", "discovery", "interruption"])
@pytest.mark.parametrize("level", ["DEBUG", "INFO"])
def test_mcp_setup_logging(monkeypatch, caplog, failure, level):
    from contextlib import asynccontextmanager
    from itertools import count
    import mcp
    import mcp.client.stdio
    from textllm import tools

    caplog.set_level(level, logger="textllm")
    clock = count()
    monkeypatch.setattr(tools, "monotonic", lambda: next(clock))
    secret = "private-command-argument-and-exception"

    class Session:
        async def list_tools(self, **kwargs):
            if failure == "discovery":
                raise RuntimeError(secret)
            if failure == "interruption":
                raise KeyboardInterrupt(secret)
            return types.SimpleNamespace(tools=[], next_cursor=None)

    @asynccontextmanager
    async def client(*args, **kwargs):
        if failure == "connection":
            raise RuntimeError(secret)
        yield Session()

    monkeypatch.setattr(mcp, "Client", client)
    monkeypatch.setattr(mcp.client.stdio, "stdio_client", lambda *a, **kw: None)
    servers = tools.mcp_config([{"name": "fixture", "command": ["unused", secret]}])
    if failure:
        error = KeyboardInterrupt if failure == "interruption" else RuntimeError
        with pytest.raises(error):
            with tools.MCPExecutor(servers):
                pytest.fail("Setup should have failed")
    else:
        with tools.MCPExecutor(servers) as executor:
            assert executor.tools == []
    logs = "\n".join(r.getMessage() for r in caplog.records if r.name == "textllm")
    assert secret not in logs
    if level == "INFO":
        assert not logs
        return
    operation = "connection/session initialization"
    assert f"MCP {operation} started: 'fixture'" in logs
    outcome = "failed" if failure == "connection" else "completed"
    assert f"MCP {operation} {outcome}: 'fixture' (1.00s)" in logs
    if failure == "connection":
        assert "tools/list" not in logs
    else:
        assert "MCP tools/list page 1 started: 'fixture'" in logs
        outcome = "failed" if failure else "completed"
        assert f"MCP tools/list page 1 {outcome}: 'fixture' (1.00s)" in logs
        assert ("MCP discovery completed:" in logs) == (failure is None)


def test_mcp_sidecar_references_and_incomplete_recovery(tmp_path):
    from textllm.history import Sidecar

    path = tmp_path / "references.md"
    base = (
        "# Test\n\n--- User ---\n\nHello\n\n--- Tools ---\n\n[@sidecar tool exchange]("
    )
    key = "827bc021-6da3-45fe-95c1-ec37a876ca9b"
    path.write_text(base + key + ")")
    with pytest.raises(ValueError, match="Missing"):
        textllm.Conversation(path)
    sidecar = Sidecar(path)
    sidecar.save(
        {"id": key, "kind": "tools", "status": "unknown", "backend": "completion"}
    )
    with pytest.raises(ValueError, match="Incomplete"):
        textllm.Conversation(path)
    path.write_text(base + "bad-key)")
    with pytest.raises(ValueError, match="Malformed"):
        textllm.Conversation(path)
    path.write_text("# Test\n\n--- User ---\n\nHello")
    assert len(textllm.Conversation(path).messages) == 1  # orphan ignored


def test_mcp_failure_persists_unknown_outcome_and_partial_text(tmp_path, monkeypatch):
    from textllm.history import Sidecar, references
    from textllm.tools import MCPExecutor

    path = tmp_path / "interrupted.md"
    path.write_text(
        "# Test\n\n```toml\n[[textllm.mcp]]\ncommand="
        + json.dumps([sys.executable, str(MCP_FIXTURE)])
        + "\n```\n\n--- User ---\n\nHello"
    )
    executions = []

    def events(**kwargs):
        name = kwargs["settings"]["tools"][0]["function"]["name"]
        yield backends.ModelEvent("output_text_delta", text="Working now.")
        yield backends.ModelEvent(
            "completed",
            data={
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": name, "arguments": '{"value":"hello"}'},
                    }
                ]
            },
        )

    def execute(self, call, arguments):
        executions.append(call)
        raise KeyboardInterrupt

    monkeypatch.setattr(conversation, "iter_model_events", events)
    monkeypatch.setattr(MCPExecutor, "execute", execute)
    with pytest.raises(KeyboardInterrupt):
        textllm.Conversation(path).chat()
    text = path.read_text()
    assert "Working now." in text
    assert text.endswith("--- User ---\n\n")
    assert Sidecar(path).read(references(text)[0])["status"] == "unknown"
    with pytest.raises(ValueError, match="Incomplete"):
        textllm.Conversation(path)
    assert len(executions) == 1


@pytest.mark.parametrize("mode", ["omit", "stderr", "block"])
@pytest.mark.parametrize("configured", [False, True])
def test_reasoning_without_tool_calls_has_no_sidecar(
    tmp_path, monkeypatch, capsys, mode, configured
):
    from textllm.history import references

    path = tmp_path / "reasoning.md"
    path.write_text(
        f'# Reasoning\n\n```toml\n[textllm]\nreasoning_summary="{mode}"\n'
        + (
            "[[textllm.mcp]]\ncommand="
            + json.dumps([sys.executable, str(MCP_FIXTURE)])
            + "\n"
            if configured
            else ""
        )
        + "```\n\n--- User ---\n\nHello"
    )

    def events(**kwargs):
        yield backends.ModelEvent("reasoning_summary_delta", text="Private summary")
        yield backends.ModelEvent("output_text_delta", text="Answer")
        yield backends.ModelEvent("completed")

    monkeypatch.setattr(conversation, "iter_model_events", events)
    textllm.Conversation(path).chat()
    captured = capsys.readouterr()
    assert ("Private summary" in captured.err) == (mode == "stderr")
    assert "Private summary" not in captured.out
    text = path.read_text()
    assert references(text) == []
    assert not Path(str(path) + ".db").exists()
    assert ("Private summary" in text) == (mode == "block")
    assert "Private summary" not in repr(textllm.Conversation(path).messages)
    assert ("--- Reasoning ---" in text) == (mode == "block")
    # Displayed reasoning is always local, with no native history reference.
    path.write_text(text.replace("Private summary", "Edited local reasoning"))
    assert "Private summary" not in repr(textllm.Conversation(path).messages)


def test_mcp_paired_rename_copy_and_collision(tmp_path, monkeypatch):
    from textllm.files import copy_conversation
    from textllm.history import Sidecar

    path = tmp_path / "original & name.md"
    path.write_text("# Desired title\n\n--- User ---\n\nHello")
    key = Sidecar(path).save({"kind": "reasoning", "text": "Saved"})
    collision = tmp_path / "Desired title.md.db"
    collision.write_bytes(b"do not overwrite")
    convo = textllm.Conversation(path)
    convo.rename_by_title()
    renamed = tmp_path / "Desired title (1).md"
    assert Path(convo.filepath) == renamed
    assert not path.exists() and not Path(str(path) + ".db").exists()
    assert collision.read_bytes() == b"do not overwrite"
    assert Sidecar(renamed).read(key)["text"] == "Saved"
    copied = tmp_path / "kept temporary & copy.md"
    copy_conversation(renamed, copied)
    assert Sidecar(copied).read(key)["text"] == "Saved"
    Sidecar(copied).save({"id": key, "kind": "reasoning", "text": "Independent"})
    assert Sidecar(renamed).read(key)["text"] == "Saved"
    with pytest.raises(FileExistsError):
        copy_conversation(renamed, copied)


def test_mcp_cli_invalid_config_before_creation_or_prompt_append(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    source = (
        "# Bad MCP\n\n```toml\n[[textllm.mcp]]\ncommand=[]\n```\n\n--- User ---\n\n"
    )
    template = tmp_path / "template.md"
    template.write_text(source)
    path = tmp_path / "new.md"
    with pytest.raises(ValueError, match="command"):
        textllm.cli([str(path), "--template", str(template), "--prompt", "Hello"])
    assert not path.exists() and not Path(str(path) + ".db").exists()
    path.write_text(source)
    with pytest.raises(ValueError, match="command"):
        textllm.cli([str(path), "--prompt", "Hello"])
    assert path.read_text() == source


@pytest.mark.parametrize(
    "limit, expected",
    [
        ({"max_calls": 1}, "call limit"),
        ({"max_rounds": 1}, "round/call limit"),
        ({"max_context_chars": 1}, "max_context_chars"),
    ],
)
def test_mcp_limits_bound_execution(tmp_path, monkeypatch, limit, expected):
    from textllm.tool_loop import iter_tool_events
    from textllm.tools import tool_limits
    from textllm.history import Sidecar

    class Executor:
        tools = [
            {"name": "echo", "parameters": {"type": "object"}, "description": "Echo"}
        ]
        executions = 0

        def __init__(self, *args):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def validate(self, call):
            return {}

        def identity(self, call):
            return {"executor": "mcp", "server": "fixture", "tool": "echo"}

        def execute(self, *args):
            self.__class__.executions += 1
            return {"content": [{"type": "text", "text": "ok"}]}

    import textllm.tool_loop as loop

    monkeypatch.setattr(loop, "MCPExecutor", Executor)

    def events(**kwargs):
        yield backends.ModelEvent(
            "completed",
            data={
                "tool_calls": [
                    {
                        "id": str(i),
                        "type": "function",
                        "function": {"name": "echo", "arguments": "{}"},
                    }
                    for i in range(2)
                ]
            },
        )

    path = tmp_path / "bounded.md"
    with pytest.raises(ValueError, match=expected):
        list(
            iter_tool_events(
                events=events,
                backend="completion",
                model="fake",
                messages=[{"role": "user", "content": "hello"}],
                settings={},
                servers=[{}],
                limits=tool_limits(limit),
                filepath=path,
            )
        )
    assert Executor.executions == (2 if "max_rounds" in limit else 0)


def test_mcp_output_selection_and_size_limits():
    from textllm.tools import model_result

    result = {
        "content": [{"type": "text", "text": '{"value":1}'}],
        "structuredContent": {"value": 1},
    }
    assert model_result(result, 100) == ('{"value": 1}', False)
    assert model_result(result, 1)[1]
    assert model_result({"content": [{"type": "image", "data": "unused"}]}, 100) == (
        "Unsupported MCP output types: image",
        True,
    )
    assert model_result(
        {"content": [{"type": "text", "text": "failure"}], "isError": True}, 100
    ) == ("failure", True)


def test_mcp_title_never_connects_and_excludes_sidecar(tmp_path, monkeypatch):
    path = tmp_path / "title.md"
    path.write_text(
        '# !!AUTO TITLE!!\n\n```toml\n[llm]\nmodel="test/model"\ntools=[{type="function"}]\ntool_choice="auto"\n[[textllm.mcp]]\ncommand=["must-not-run"]\n```\n\n--- User ---\n\nHello'
    )
    calls = []

    def events(**kwargs):
        calls.append(kwargs)
        yield backends.ModelEvent("output_text_delta", text="Saved title")
        yield backends.ModelEvent("completed")

    import textllm.tool_loop as loop

    monkeypatch.setattr(
        loop, "MCPExecutor", lambda *args: pytest.fail("Title connected to MCP")
    )
    monkeypatch.setattr(conversation, "iter_model_events", events)
    textllm.Conversation(path).set_title()
    assert path.read_text().startswith("# Saved title")
    assert "tools" not in calls[0]["settings"]
    assert "tool_choice" not in calls[0]["settings"]
    assert not Path(str(path) + ".db").exists()


def test_mcp_sidecar_failure_preserves_streamed_text(tmp_path, monkeypatch):
    from textllm.history import Sidecar

    path = tmp_path / "disk-full.md"
    source = (
        "# Test\n\n```toml\n[[textllm.mcp]]\ncommand="
        + json.dumps([sys.executable, str(MCP_FIXTURE)])
        + "\n```\n\n--- User ---\n\nHello"
    )
    path.write_text(source)

    def events(**kwargs):
        yield backends.ModelEvent("output_text_delta", text="Flushed answer")
        yield backends.ModelEvent("reasoning_summary_delta", text="Reasoning")
        name = kwargs["settings"]["tools"][0]["function"]["name"]
        yield backends.ModelEvent(
            "completed",
            data={
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": name, "arguments": "{}"},
                    }
                ]
            },
        )

    def fail_save(*args):
        raise OSError("disk full")

    monkeypatch.setattr(conversation, "iter_model_events", events)
    monkeypatch.setattr(Sidecar, "save", fail_save)
    with pytest.raises(OSError, match="disk full"):
        textllm.Conversation(path).chat()
    assert "Flushed answer" in path.read_text()
    assert "[@sidecar" not in path.read_text()
    assert path.read_text().count("--- User ---") == 2


def test_mcp_replay_pairing_backend_duplicate_and_order(tmp_path):
    from textllm.history import Sidecar

    path = tmp_path / "ordering.md"
    record = {
        "kind": "tools",
        "backend": "completion",
        "status": "complete",
        "native": {"role": "assistant", "tool_calls": []},
        "calls": [{"id": "c1"}],
        "results": [{"call_id": "c1", "output": "ok", "is_error": False}],
    }
    key = Sidecar(path).save(record)
    block = "\n\n--- Tools ---\n\n[@sidecar tool exchange](" + key + ")"
    path.write_text("# Test" + block)
    with pytest.raises(ValueError, match="order"):
        textllm.Conversation(path)
    path.write_text("# Test\n\n--- User ---\n\nHi" + block + block)
    with pytest.raises(ValueError, match="Duplicate"):
        textllm.Conversation(path)
    path.write_text(
        '# Test\n\n```toml\n[textllm]\nbackend="responses"\n```\n\n--- User ---\n\nHi'
        + block
    )
    with pytest.raises(ValueError, match="different backend"):
        textllm.Conversation(path)
    record["results"][0]["call_id"] = "other"
    Sidecar(path).save(record)
    path.write_text("# Test\n\n--- User ---\n\nHi" + block)
    with pytest.raises(ValueError, match="pairing"):
        textllm.Conversation(path)


def test_mcp_subprocess_temporary_reasoning_and_keep(tmp_path):
    from textllm.files import copy_conversation
    from textllm.history import Sidecar, references
    from textllm.state import _last_conversation

    env = os.environ.copy()
    env["PYTHON_DOTENV_DISABLED"] = "1"
    env["TEXTLLM_DEFAULT_MODEL"] = "openai/test"
    env.pop("TEXTLLM_TEMPLATE_FILE", None)
    override = "[[textllm.mcp]]\ncommand=" + json.dumps(
        [sys.executable, str(MCP_FIXTURE)]
    )
    args = [
        sys.executable,
        str(LOCAL_LAUNCHER),
        "--temp",
        "--title",
        "off",
        "--prompt",
        "Hello",
        "-o",
        override,
        "-o",
        'textllm.backend="responses"',
        "-o",
        'llm.reasoning.summary="auto"',
    ]
    result = subprocess.run(args, env=env, cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    path = Path(_last_conversation("temp"))
    text = path.read_text()
    assert references(text) == []
    assert not Path(str(path) + ".db").exists()
    kept = tmp_path / "kept & renamed.md"
    copy_conversation(path, kept)
    assert kept.exists() and not Path(str(kept) + ".db").exists()
    assert path.exists() and not Path(str(path) + ".db").exists()
    result = subprocess.run(
        [
            sys.executable,
            str(LOCAL_LAUNCHER),
            str(kept),
            "--title",
            "off",
            "--prompt",
            "Next",
        ],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "2 user messages" in result.stdout


def test_mcp_corrupt_database_and_unknown_version(tmp_path):
    from textllm.history import Sidecar
    import sqlite3

    path = tmp_path / "corrupt.md"
    key = "827bc021-6da3-45fe-95c1-ec37a876ca9b"
    path.write_text("# Test\n\n--- Tools ---\n\n[@sidecar reasoning](" + key + ")")
    dbpath = Path(str(path) + ".db")
    dbpath.write_bytes(b"not a SQLite database")
    with pytest.raises(sqlite3.DatabaseError):
        textllm.Conversation(path)
    assert dbpath.read_bytes() == b"not a SQLite database"
    dbpath.unlink()
    Sidecar(path).save({"id": key, "kind": "reasoning", "version": 999})
    with pytest.raises(ValueError, match="version"):
        textllm.Conversation(path)


def test_mcp_token_usage_aggregates_rounds(tmp_path, monkeypatch):
    from textllm.tool_loop import iter_tool_events
    from textllm.tools import tool_limits
    import textllm.tool_loop as loop

    class Executor:
        tools = []

        def __init__(self, *args):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def validate(self, call):
            return {}

        def identity(self, call):
            return {"executor": "mcp"}

        def execute(self, *args):
            return {"content": []}

    count = 0

    def events(**kwargs):
        nonlocal count
        count += 1
        yield backends.ModelEvent(
            "usage",
            data={"input_tokens": 2, "output_tokens": 3, "details": {"cached": 1}},
        )
        if count == 1:
            yield backends.ModelEvent(
                "completed",
                data={
                    "tool_calls": [
                        {"id": "c1", "function": {"name": "echo", "arguments": "{}"}}
                    ]
                },
            )
        else:
            yield backends.ModelEvent("output_text_delta", text="Done")
            yield backends.ModelEvent("completed")

    monkeypatch.setattr(loop, "MCPExecutor", Executor)
    events = list(
        iter_tool_events(
            events=events,
            backend="completion",
            model="test",
            messages=[{"role": "user", "content": "Hi"}],
            settings={},
            servers=[{}],
            limits=tool_limits({}),
            filepath=tmp_path / "usage.md",
        )
    )
    usage = [event.data for event in events if event.kind == "usage"]
    assert usage[-1] == {
        "input_tokens": 4,
        "output_tokens": 6,
        "details": {"cached": 2},
    }


def test_mcp_replay_preserves_assistant_content_blocks():
    from textllm.history import replay_exchange

    visible = [
        {"type": "text", "text": "See image"},
        {"type": "image_url", "image_url": {"url": "https://example.com/a.png"}},
    ]
    messages = [
        {"role": "user", "content": "Hi"},
        {"role": "assistant", "content": visible},
    ]
    native = [{"type": "tool_use", "id": "c1", "name": "echo", "input": {}}]
    record = {
        "kind": "tools",
        "backend": "messages",
        "status": "complete",
        "native": native,
        "calls": [{"id": "c1"}],
        "results": [{"call_id": "c1", "output": "ok", "is_error": False}],
    }
    replay_exchange(messages, record, "messages")
    _, request = backends._messages_request(messages)
    assert request[1]["content"][0] == {"type": "text", "text": "See image"}
    assert request[1]["content"][1]["type"] == "image"
    assert request[1]["content"][-1] == native[0]


def test_mcp_plain_stream_writer_remains_compatible(tmp_path, monkeypatch):
    path = tmp_path / "writer.md"
    path.write_text(
        '# Test\n\n```toml\n[textllm]\nreasoning_summary="block"\n```\n\n--- User ---\n\nHi'
    )

    def events(**kwargs):
        yield backends.ModelEvent("reasoning_summary_delta", text="Reasoning")
        yield backends.ModelEvent("output_text_delta", text="Answer")
        yield backends.ModelEvent("completed")

    monkeypatch.setattr(conversation, "iter_model_events", events)
    writer = io.StringIO()
    assert (
        textllm.Conversation(path).call_llm([], stream_writer=writer).content
        == "Answer"
    )
    assert writer.getvalue() == "Answer"


def test_opaque_reasoning_without_tools_does_not_create_sidecar(tmp_path, monkeypatch):
    from textllm.history import references

    path = tmp_path / "opaque.md"
    path.write_text(
        '# Test\n\n```toml\n[textllm]\nbackend="responses"\n```\n\n--- User ---\n\nHi'
    )
    native = [
        {"type": "reasoning", "id": "r1", "summary": [], "encrypted_content": "opaque"}
    ]

    def events(**kwargs):
        yield backends.ModelEvent("output_text_delta", text="Answer")
        yield backends.ModelEvent("completed", data={"output": native})

    monkeypatch.setattr(conversation, "iter_model_events", events)
    textllm.Conversation(path).chat()
    assert references(path.read_text()) == []
    assert not Path(str(path) + ".db").exists()
    message = textllm.Conversation(path).messages[-1]
    assert message["role"] == "assistant" and message["content"] == "Answer"
    assert "_native" not in message


@pytest.mark.parametrize(
    "token_setting", ["${MCP_LOG_TOKEN}", "sensitive-bearer-token"]
)
@pytest.mark.parametrize("prefix", ["Checking.", "Checking.\n"])
@pytest.mark.parametrize(
    "outcome, visible",
    [
        ("success", True),
        ("success", False),
        ("tool_error", True),
        ("unsupported", True),
        ("oversized", True),
        ("interrupted", True),
    ],
)
def test_mcp_call_logging_lifecycle_and_stream_safety(
    tmp_path, monkeypatch, prefix, outcome, visible, token_setting
):
    import logging
    import textllm.tool_loop as loop
    from textllm.output import ConsoleHandler
    from textllm.history import Sidecar, references

    terminal, debug_log = io.StringIO(), io.StringIO()
    handler = ConsoleHandler(terminal)
    handler.setLevel(logging.INFO if visible else logging.WARNING)
    handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    debug_handler = logging.StreamHandler(debug_log)
    debug_handler.setLevel(logging.DEBUG)
    monkeypatch.setattr(sys, "stdout", terminal)
    monkeypatch.setattr(config.log, "handlers", [handler, debug_handler])
    monkeypatch.setattr(config.log, "level", logging.DEBUG)
    monkeypatch.setattr(config.log, "_cache", {})
    monkeypatch.setattr(config.log, "propagate", False)
    monkeypatch.setenv("MCP_LOG_TOKEN", "sensitive-bearer-token")
    clock = iter([10.0, 11.25, 12.0, 13.25])
    monkeypatch.setattr(loop, "monotonic", lambda: next(clock))
    executed = []

    class Executor:
        tools = [
            {
                "name": "search",
                "description": "Search",
                "parameters": {"type": "object"},
            }
        ]

        def __init__(self, *args):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def validate(self, call):
            return json.loads(call["arguments"])

        def identity(self, call):
            return {"executor": "mcp", "server": "fixture", "tool": "search"}

        def execute(self, call, arguments):
            # The start record must be emitted before the operation begins.
            assert (
                f"MCP call started: 'fixture.search' (call '{call['id']}'"
                in debug_log.getvalue()
            )
            executed.append(call["id"])
            if outcome == "interrupted":
                raise TimeoutError("sensitive-transport-details")
            if outcome == "unsupported":
                return {"content": [{"type": "image", "data": "sensitive-result"}]}
            text = "sensitive-result" * (20 if outcome == "oversized" else 1)
            return {
                "content": [{"type": "text", "text": text}],
                "isError": outcome == "tool_error",
            }

    path = tmp_path / "logged calls.md"
    path.write_text(
        "# Logging\n\n```toml\n[textllm.tools]\nmax_result_chars=100\n"
        '[[textllm.mcp]]\nurl="https://fixture.invalid/mcp"\n'
        f'bearer_token="{token_setting}"\n```\n\n--- User ---\n\nHi'
    )
    calls = 2 if outcome == "success" else 1

    def events(**kwargs):
        if not executed:
            yield backends.ModelEvent("output_text_delta", text=prefix)
            yield backends.ModelEvent(
                "completed",
                data={
                    "tool_calls": [
                        {
                            "id": f"c{i}",
                            "type": "function",
                            "function": {
                                "name": "search",
                                "arguments": '{"query":"sensitive-argument"}',
                            },
                        }
                        for i in range(calls)
                    ]
                },
            )
        else:
            yield backends.ModelEvent("output_text_delta", text="Done.")
            yield backends.ModelEvent("completed")

    monkeypatch.setattr(loop, "MCPExecutor", Executor)
    monkeypatch.setattr(conversation, "iter_model_events", events)
    if outcome == "interrupted":
        with pytest.raises(TimeoutError):
            textllm.Conversation(path).chat()
    else:
        textllm.Conversation(path).chat()
    logs = debug_log.getvalue()
    assert logs.count("MCP call started:") == calls
    assert logs.count("1.25s") == calls
    if outcome == "success":
        assert logs.count("MCP call completed:") == 2
        if not visible:
            assert terminal.getvalue() == "\n" + prefix + "Done.\n\n"
    elif outcome == "tool_error":
        assert "MCP call returned an error:" in logs
    elif outcome in ("unsupported", "oversized"):
        assert "MCP call returned unsupported or oversized output:" in logs
    else:
        assert "MCP call outcome unknown:" in logs
        assert "not retried automatically" in logs
        (key,) = references(path.read_text())
        assert Sidecar(path).read(key)["status"] == "unknown"
    if visible:
        assert "\nINFO: MCP call started:" in terminal.getvalue()
        assert "\n\nINFO: MCP call" not in terminal.getvalue()
    assert "MCP call" not in path.read_text()
    assert "sensitive-bearer-token" not in json.dumps(_sidecar_records(path))
    for secret in (
        "sensitive-bearer-token",
        "sensitive-argument",
        "sensitive-result",
        "sensitive-transport-details",
    ):
        assert secret not in logs
        assert secret not in terminal.getvalue()


def test_settings_environment_expansion_is_recursive_but_single_pass(monkeypatch):
    monkeypatch.setenv("VALUE", 'spaces, "quotes", ${MISSING_NESTED}')
    monkeypatch.setenv("EMPTY", "")
    monkeypatch.setenv("INDEX", "/folder with spaces/${MISSING_NESTED}")
    monkeypatch.delenv("MISSING_NESTED", raising=False)
    original = {
        "model": "${VALUE}",
        "nested": {"array": ["prefix:${VALUE}", "$${VALUE}", "${EMPTY}", 5, True]},
        "${KEY_STAYS_LITERAL}": "unchanged",
        "textllm": {"mcp": [{"command": "server --index ${INDEX}"}]},
    }
    expanded = config._resolve_settings(original)
    assert expanded["model"] == 'spaces, "quotes", ${MISSING_NESTED}'
    assert expanded["nested"]["array"] == [
        'prefix:spaces, "quotes", ${MISSING_NESTED}',
        "${VALUE}",
        "",
        5,
        True,
    ]
    assert expanded["textllm"]["mcp"][0]["command"] == [
        "server",
        "--index",
        "/folder with spaces/${MISSING_NESTED}",
    ]
    assert expanded["${KEY_STAYS_LITERAL}"] == "unchanged"
    assert original["model"] == "${VALUE}"
    with pytest.raises(ValueError, match="MISSING_NESTED"):
        config._resolve_settings({"nested": ["${MISSING_NESTED}"]})


@pytest.mark.parametrize("backend", ["completion", "responses", "messages"])
def test_settings_environment_reaches_chat_title_and_call_overrides(
    tmp_path, monkeypatch, caplog, backend
):
    import logging

    values = {
        "MODEL": "test/chat-${UNDEFINED}",
        "TITLE_MODEL": "test/title-${UNDEFINED}",
        "OVERRIDE_MODEL": "test/override-${UNDEFINED}",
        "BACKEND": backend,
        "DESCRIPTION": "description-${UNDEFINED}",
        "PRIVATE_KEY": "private-resolved-credential",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("UNDEFINED", raising=False)
    monkeypatch.setattr(config.log, "handlers", [])
    monkeypatch.setattr(config.log, "level", logging.DEBUG)
    monkeypatch.setattr(config.log, "_cache", {})
    monkeypatch.setattr(config.log, "propagate", True)
    source = """# Environment settings

```toml
[llm]
model = "${MODEL}"
api_key = "${PRIVATE_KEY}"
max_tokens = 1000
tools = [{type="function", function={name="test", description="${DESCRIPTION}"}}]
extra_body = { values = ["${DESCRIPTION}", "$${DESCRIPTION}"] }
[textllm]
backend = "${BACKEND}"
[textllm.title]
model = "${TITLE_MODEL}"
```

--- User ---

Hello
"""
    path = tmp_path / "environment.md"
    path.write_text(source)
    requests = []

    def events(**kwargs):
        requests.append(kwargs)
        yield backends.ModelEvent("output_text_delta", text="Answer")
        yield backends.ModelEvent("completed")

    monkeypatch.setattr(conversation, "iter_model_events", events)
    convo = textllm.Conversation(path)
    convo.call_llm(convo.messages)
    convo.call_llm(convo.messages, title_call=True)
    convo.call_llm(convo.messages, model="${OVERRIDE_MODEL}")
    assert [r["model"] for r in requests] == [
        values["MODEL"],
        values["TITLE_MODEL"],
        values["OVERRIDE_MODEL"],
    ]
    assert all(r["backend"] == backend for r in requests)
    assert (
        requests[0]["settings"]["tools"][0]["function"]["description"]
        == values["DESCRIPTION"]
    )
    assert all(
        r["settings"]["extra_body"]["values"]
        == [values["DESCRIPTION"], "${DESCRIPTION}"]
        for r in requests
    )
    assert all(r["settings"]["api_key"] == values["PRIVATE_KEY"] for r in requests)
    assert "tools" not in requests[1]["settings"]
    assert values["PRIVATE_KEY"] not in caplog.text
    assert path.read_text() == source


def test_template_creation_defers_environment_resolution_until_execution(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    monkeypatch.delenv("MCP_URL", raising=False)
    monkeypatch.delenv("MCP_BEARER_TOKEN", raising=False)
    template = (
        Path(__file__).resolve().parent / "examples/templates/mcp_bearer_openai.md"
    )
    destination = tmp_path / "bearer without environment.md"
    textllm.cli([str(destination), "--template", str(template)])
    source = destination.read_text()
    assert 'url = "${MCP_URL}"' in source
    with pytest.raises(ValueError, match="MCP_URL"):
        textllm.cli([str(destination), "--title", "off", "--prompt", "Hello"])
    assert destination.read_text() == source
    assert not Path(str(destination) + ".db").exists()


@pytest.mark.parametrize("backend", ["completion", "responses", "messages"])
def test_template_environment_expansion_without_mcp(tmp_path, monkeypatch, backend):
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    monkeypatch.delenv("TEXTLLM_TEST_CONFIG_MODEL", raising=False)
    monkeypatch.delenv("TEXTLLM_TEST_CONFIG_BACKEND", raising=False)
    template = tmp_path / "config template.md"
    template.write_text(
        '# Config\n\n```toml\n[llm]\nmodel="${TEXTLLM_TEST_CONFIG_MODEL}"\n'
        'max_tokens=123\n[textllm]\nbackend="${TEXTLLM_TEST_CONFIG_BACKEND}"\n'
        "```\n\n--- User ---\n\nHello"
    )
    path = tmp_path / "config chat.md"
    textllm.cli([str(path), "--template", str(template)])
    original = path.read_text()
    with pytest.raises(ValueError, match="TEXTLLM_TEST_CONFIG_MODEL"):
        textllm.cli([str(path), "--title", "off", "--prompt", "Do not append"])
    assert path.read_text() == original

    monkeypatch.setenv("TEXTLLM_TEST_CONFIG_MODEL", "test/environment")
    monkeypatch.setenv("TEXTLLM_TEST_CONFIG_BACKEND", backend)
    requests = []

    def events(**kwargs):
        requests.append(kwargs)
        yield backends.ModelEvent("output_text_delta", text="Answer")
        yield backends.ModelEvent("completed")

    monkeypatch.setattr(conversation, "iter_model_events", events)
    textllm.cli([str(path), "--title", "off"])
    assert len(requests) == 1
    assert requests[0]["model"] == "test/environment"
    assert requests[0]["backend"] == backend
    assert requests[0]["settings"]["max_tokens"] == 123
    assert original.rstrip() in path.read_text()
    assert not Path(str(path) + ".db").exists()


def test_unused_title_environment_reference_is_required_before_mutation(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    monkeypatch.delenv("TEXTLLM_TEST_TITLE_MODEL", raising=False)
    path = tmp_path / "title config.md"
    source = (
        '# Config\n\n```toml\n[llm]\nmodel="test/chat"\n'
        '[textllm.title]\nmodel="${TEXTLLM_TEST_TITLE_MODEL}"\n'
        "```\n\n--- User ---\n\nHello"
    )
    path.write_text(source)
    with pytest.raises(ValueError, match="TEXTLLM_TEST_TITLE_MODEL"):
        textllm.cli([str(path), "--title", "off", "--prompt", "Do not append"])
    assert path.read_text() == source
    convo = textllm.Conversation(
        path, overrides={"textllm": {"title": {"model": "test/override"}}}
    )
    assert convo.settings["textllm"]["title"]["model"] == "test/override"


def test_settings_environment_does_not_parse_types_or_expand_schema_literals(
    monkeypatch,
):
    monkeypatch.setenv("TEXTLLM_TEST_CONFIG_NUMBER", "123")
    monkeypatch.setenv("TEXTLLM_TEST_CONFIG_BOOLEAN", "false")
    monkeypatch.setenv("TEXTLLM_TEST_CONFIG_OBJECT", '{"enabled": true}')
    monkeypatch.delenv("TEXTLLM_TEST_SCHEMA_LITERAL", raising=False)
    settings = config._resolve_settings(
        {
            "max_tokens": "${TEXTLLM_TEST_CONFIG_NUMBER}",
            "extra_body": {
                "enabled": "${TEXTLLM_TEST_CONFIG_BOOLEAN}",
                "object": "${TEXTLLM_TEST_CONFIG_OBJECT}",
            },
            "tools": [{"description": "Example: $${TEXTLLM_TEST_SCHEMA_LITERAL}"}],
        }
    )
    assert settings["max_tokens"] == "123"
    assert settings["extra_body"] == {"enabled": "false", "object": '{"enabled": true}'}
    assert (
        settings["tools"][0]["description"] == "Example: ${TEXTLLM_TEST_SCHEMA_LITERAL}"
    )


def test_cli_resolves_settings_after_dotenv_and_override_merging(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    monkeypatch.setenv("MODEL", "wrong/shell-model")
    monkeypatch.delenv("OVERRIDDEN_MISSING", raising=False)
    source = '# Saved\n\n```toml\n[llm]\nmodel="${OVERRIDDEN_MISSING}"\n```\n\n--- User ---\n\nHi'
    path = tmp_path / "chat.md"
    path.write_text(source)
    requests = []

    def load_env(*args, **kwargs):
        monkeypatch.setenv("MODEL", "test/from-env-file-${NOT_EXPANDED}")
        return True

    def events(**kwargs):
        requests.append(kwargs)
        yield backends.ModelEvent("output_text_delta", text="Answer")
        yield backends.ModelEvent("completed")

    monkeypatch.setattr(cli_module, "load_dotenv", load_env)
    monkeypatch.setattr(conversation, "iter_model_events", events)
    textllm.cli([str(path), "--title", "off", "-o", 'llm.model="${MODEL}"'])
    assert requests[0]["model"] == "test/from-env-file-${NOT_EXPANDED}"
    assert source in path.read_text()


@pytest.mark.parametrize("async_value", [None, False, True])
def test_responses_replay_repairs_legacy_sdk_field_names(async_value):
    from openai.types.responses import ResponseFunctionToolCall

    native = ResponseFunctionToolCall(
        arguments='{"async_":null}',
        call_id="c1",
        name="search",
        type="function_call",
        **{"async": async_value},
    ).model_dump()
    original = json.loads(json.dumps(native))
    messages = [
        {
            "role": "assistant",
            "content": "",
            "_native": [native],
            "_backend": "responses",
        }
    ]
    (item,) = backends._responses_messages(messages)
    assert "async_" not in item
    assert all(value is not None for value in item.values())
    if async_value is None:
        assert "async" not in item
    else:
        assert item["async"] is async_value
    assert item["arguments"] == '{"async_":null}'
    assert native == original  # Legacy records remain unchanged on disk.


def test_responses_tool_loop_serializes_real_sdk_models(tmp_path, monkeypatch):
    import any_llm
    import textllm.tool_loop as loop
    from openai.types.responses import Response, ResponseFunctionToolCall
    from textllm.history import Sidecar, references

    monkeypatch.setattr(config, "TEST_MODE", False)
    monkeypatch.delenv("TEXTLLM_TEST_MODE", raising=False)

    class Executor:
        tools = [
            {
                "name": "search",
                "description": "Search",
                "parameters": {"type": "object"},
            }
        ]

        def __init__(self, *args):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def validate(self, call):
            return json.loads(call["arguments"])

        def identity(self, call):
            return {"executor": "mcp", "server": "fixture", "tool": "search"}

        def execute(self, call, args):
            return {"content": [{"type": "text", "text": "Found"}]}

    requests = []

    def responses(**kwargs):
        requests.append(kwargs)
        if len(requests) == 1:
            item = ResponseFunctionToolCall(
                arguments='{"async_":null}',
                call_id="c1",
                name="search",
                type="function_call",
            )
            yield {"type": "response.output_text.delta", "delta": "Checking."}
            yield {"type": "response.output_item.done", "item": item}
            yield {
                "type": "response.completed",
                "response": Response.model_construct(output=[item], usage=None),
            }
        else:
            (call,) = [
                item
                for item in kwargs["input_data"]
                if item.get("type") == "function_call"
            ]
            assert call == {
                "type": "function_call",
                "call_id": "c1",
                "name": "search",
                "arguments": '{"async_":null}',
            }
            yield {"type": "response.output_text.delta", "delta": "Answer."}
            yield {
                "type": "response.completed",
                "response": Response.model_construct(output=[], usage=None),
            }

    monkeypatch.setattr(loop, "MCPExecutor", Executor)
    monkeypatch.setattr(any_llm, "responses", responses)
    path = tmp_path / "SDK aliases.md"
    path.write_text(
        '# Test\n\n```toml\n[llm]\nmodel="openai/test"\n[textllm]\nbackend="responses"\n[[textllm.mcp]]\ncommand=["fixture"]\n```\n\n--- User ---\n\nHi'
    )
    textllm.Conversation(path).chat()
    assert len(requests) == 2
    (key,) = references(path.read_text())
    native = Sidecar(path).read(key)["native"][0]
    assert "async_" not in native and "async" not in native
    assert native["arguments"] == '{"async_":null}'


def test_template_environment_references_do_not_collide_with_placeholders(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    monkeypatch.delenv("model", raising=False)
    monkeypatch.delenv("version", raising=False)
    template = tmp_path / "environment template.md"
    template.write_text(
        '# {AUTO_TITLE}\n\n```toml\n[llm]\nmodel="${model}"\nextra_body={label="$${version}"}\n```\n\n--- User ---\n\n'
    )
    path = tmp_path / "runtime model.md"
    textllm.cli([str(path), "--template", str(template)])
    source = path.read_text()
    assert 'model="${model}"' in source
    assert 'label="$${version}"' in source
    monkeypatch.setenv("model", "test/runtime-model")
    settings = textllm.Conversation(path).settings
    assert settings["model"] == "test/runtime-model"
    assert settings["extra_body"]["label"] == "${version}"


@pytest.mark.parametrize("header_level", [0, 2])
@pytest.mark.parametrize("display_reasoning", [False, True])
@pytest.mark.parametrize("tools_display", ["comment", "block"])
def test_sidecar_reference_blocks_have_compact_spacing(
    header_level, display_reasoning, tools_display
):
    output = io.StringIO()
    writer = textllm.Conversation._ResponseBlockWriter(
        output, header_level, tools_display=tools_display
    )
    if display_reasoning:
        writer.write_reasoning("Think.")
        writer.write_reasoning(" More.")
    writer.write_reference("Tools", "reason-key", "reasoning")
    writer.write_reference("Tools", "tool-key-1", "tool exchange")
    writer.write_reference("Tools", "tool-key-2", "tool exchange")
    writer.write("Answer.")
    prefix = "#" * header_level + " " if header_level else ""
    expected = (
        (f"\n\n{prefix}--- Reasoning ---\n\nThink. More." if display_reasoning else "")
        + "\n\n"
        + ("<!--\n" if tools_display == "comment" else "")
        + f"{prefix}--- Tools ---\n\n[@sidecar reasoning](reason-key)"
        + "\n[@sidecar tool exchange](tool-key-1)"
        + "\n[@sidecar tool exchange](tool-key-2)"
        + ("\n-->" if tools_display == "comment" else "")
        + f"\n\n{prefix}--- Assistant ---\n\nAnswer."
    )
    assert output.getvalue() == expected
    assert "\n\n\n" not in output.getvalue()
    assert all(not line.endswith(" ") for line in output.getvalue().splitlines())


@pytest.mark.parametrize("backend", ["completion", "responses", "messages"])
def test_tools_reasoning_replay_is_controlled_by_explicit_reference(tmp_path, backend):
    from textllm.history import Sidecar, split_reasoning

    call = {"id": "c1", "name": "search", "arguments": "{}"}
    if backend == "completion":
        native = {
            "role": "assistant",
            "content": "Working",
            "reasoning_content": "Native thought",
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "search", "arguments": "{}"},
                }
            ],
        }
    elif backend == "responses":
        native = [
            {
                "type": "reasoning",
                "id": "r1",
                "encrypted_content": "opaque one",
                "summary": [],
            },
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "Working"}],
            },
            {
                "type": "reasoning",
                "id": "r2",
                "encrypted_content": "opaque two",
                "summary": [],
            },
            {
                "type": "function_call",
                "call_id": "c1",
                "name": "search",
                "arguments": "{}",
            },
        ]
    else:
        native = [
            {
                "type": "thinking",
                "thinking": "Native one",
                "signature": "signature one",
            },
            {"type": "text", "text": "Working"},
            {"type": "redacted_thinking", "data": "opaque two"},
            {"type": "tool_use", "id": "c1", "name": "search", "input": {}},
        ]
    path = tmp_path / "explicit reasoning.md"
    sidecar = Sidecar(path)
    tool_native, reasoning_native, positions = split_reasoning(native, backend)
    reason_key = sidecar.save(
        {
            "kind": "reasoning",
            "backend": backend,
            "status": "complete",
            "text": "Native thought",
            "native": reasoning_native,
        }
    )
    tool_key = sidecar.save(
        {
            "kind": "tools",
            "backend": backend,
            "status": "complete",
            "native": tool_native,
            "reasoning_key": reason_key,
            "reasoning_positions": positions,
            "calls": [call],
            "results": [{"call_id": "c1", "output": "Found", "is_error": False}],
        }
    )
    reason_link = f"[@sidecar reasoning]({reason_key})"
    tool_link = f"[@sidecar tool exchange]({tool_key})"
    source = (
        f'# Explicit\n\n```toml\n[textllm]\nbackend="{backend}"\n```\n\n--- User ---\n\nHi'
        "\n\n--- Reasoning ---\n\nDisplayed explanation\n\n--- Assistant ---\n\nWorking"
        f"\n\n## --- Tools ---\n\n{reason_link}\n{tool_link}\n\n--- Assistant ---\n\nDone"
    )
    path.write_text(source)
    messages = textllm.Conversation(path).messages
    assert messages[1]["_native"] == native
    assert messages[1]["content"] == "Working"
    assert "Displayed explanation" not in repr(messages)
    assert sidecar.read(tool_key)["native"] == tool_native
    assert sidecar.read(reason_key)["native"] == reasoning_native
    # Display edits affect no replayable payload. Removing its explicit Tools
    # reference excludes native reasoning, without reloading it implicitly.
    path.write_text(source.replace("Displayed explanation", "Edited explanation"))
    assert textllm.Conversation(path).messages[1]["_native"] == native
    path.write_text(source.replace(reason_link + "\n", ""))
    assert textllm.Conversation(path).messages[1]["_native"] == tool_native
    # First-pass databases can still be referenced with the new directive
    # syntax, but embedded reasoning must not become an implicit fallback.
    old_record = sidecar.read(tool_key)
    old_record["native"] = native
    old_record.pop("reasoning_key")
    old_record.pop("reasoning_positions")
    sidecar.save(old_record)
    assert textllm.Conversation(path).messages[1]["_native"] == tool_native
    path.write_text(source)
    assert textllm.Conversation(path).messages[1]["_native"] == native
    # Restore the association for the mismatch check below.
    old_record["reasoning_key"] = reason_key
    old_record["reasoning_positions"] = positions
    sidecar.save(old_record)
    # Reordering complete exchanges is allowed, but swapping a reasoning key
    # into a different exchange is an invalid association.
    other_key = sidecar.save(
        {
            "kind": "reasoning",
            "backend": backend,
            "status": "complete",
            "text": "Other",
            "native": reasoning_native,
        }
    )
    path.write_text(source.replace(reason_key, other_key))
    with pytest.raises(ValueError, match="different tool exchange"):
        textllm.Conversation(path)


@pytest.mark.parametrize(
    "description", ["", " reasoning", " tool exchange", " descriptive label"]
)
def test_sidecar_directive_syntax(description):
    from textllm.history import references

    key = "827bc021-6da3-45fe-95c1-ec37a876ca9b"
    assert references(f"[@sidecar{description}]({key})") == [key]
    with pytest.raises(ValueError, match="Malformed"):
        references(f"@sidecar:{key}")


def test_reasoning_reference_under_tools_requires_exchange(tmp_path):
    from textllm.history import Sidecar

    path = tmp_path / "dangling reasoning.md"
    key = Sidecar(path).save(
        {
            "kind": "reasoning",
            "backend": "completion",
            "status": "complete",
            "native": {},
        }
    )
    path.write_text(
        f"# Test\n\n--- User ---\n\nHi\n\n--- Tools ---\n\n[@sidecar reasoning]({key})"
    )
    with pytest.raises(ValueError, match="must precede"):
        textllm.Conversation(path)


@pytest.mark.parametrize("backend", ["responses", "messages"])
@pytest.mark.parametrize("streamed", [False, True])
def test_completed_native_payload_answer_is_not_lost_or_duplicated(
    tmp_path, monkeypatch, backend, streamed
):
    import any_llm

    monkeypatch.setattr(config, "TEST_MODE", False)
    monkeypatch.delenv("TEXTLLM_TEST_MODE", raising=False)
    answer = "5 + 5 = 10."

    def responses(**kwargs):
        if streamed:
            yield {"type": "response.output_text.delta", "delta": answer}
        yield {
            "type": "response.completed",
            "response": {
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": answer}],
                    }
                ]
            },
        }

    def messages(**kwargs):
        if streamed:
            yield {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": answer},
            }
        yield {
            "type": "message_stop",
            "message": {
                "content": [{"type": "text", "text": answer}],
                "stop_reason": "end_turn",
            },
        }

    monkeypatch.setattr(any_llm, "responses", responses)
    monkeypatch.setattr(any_llm, "messages", messages)
    answer_events = [
        event for event in backends.iter_model_events(
            backend=backend, model="test/model", messages=[],
            settings={"max_tokens": 1000},
        ) if event.kind == "output_text_delta"
    ]
    assert len(answer_events) == 1
    assert answer_events[0].data == (None if streamed else {"from_completed": True})
    path = tmp_path / "final native answer.md"
    path.write_text(
        f'# Test\n\n```toml\n[llm]\nmodel="test/model"\nmax_tokens=1000\n[textllm]\nbackend="{backend}"\n```\n\n--- User ---\n\nWhat is 5+5?'
    )
    with Capture() as captured:
        textllm.Conversation(path).chat()
    assert captured.out.count(answer) == 1
    assert path.read_text().count(answer) == 1
    assert path.read_text().endswith("--- User ---\n\n")


@pytest.mark.parametrize("reasoning_where", ["none", "tool_and_final", "final_only"])
@pytest.mark.parametrize("final_answer", [True, False])
def test_reasoning_link_conditions_and_user_ending(
    tmp_path, monkeypatch, reasoning_where, final_answer
):
    import textllm.tool_loop as loop
    from textllm.history import Sidecar, references

    class Executor:
        tools = [
            {"name": "add", "description": "Add", "parameters": {"type": "object"}}
        ]

        def __init__(self, *args):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def validate(self, call):
            return {}

        def identity(self, call):
            return {"executor": "mcp", "server": "math", "tool": "add"}

        def execute(self, *args):
            return {"content": [{"type": "text", "text": "10"}]}

    requests = []

    def events(**kwargs):
        requests.append(kwargs)
        if len(requests) == 1:
            # An intermediate preamble must not disguise a missing final answer.
            yield backends.ModelEvent("output_text_delta", text="I'll calculate.")
            native = [
                {
                    "type": "function_call",
                    "call_id": "c1",
                    "name": "add",
                    "arguments": "{}",
                }
            ]
            if reasoning_where == "tool_and_final":
                native.insert(
                    0,
                    {
                        "type": "reasoning",
                        "id": "r1",
                        "encrypted_content": "opaque",
                        "summary": [],
                    },
                )
            yield backends.ModelEvent("completed", data={"output": native})
        else:
            native = []
            if reasoning_where != "none":
                yield backends.ModelEvent(
                    "reasoning_summary_delta", text="The tool returned ten."
                )
                native.append({"type": "reasoning", "id": "r2", "summary": []})
            if final_answer:
                yield backends.ModelEvent("output_text_delta", text="5 + 5 = 10.")
            yield backends.ModelEvent("completed", data={"output": native})

    monkeypatch.setattr(loop, "MCPExecutor", Executor)
    monkeypatch.setattr(conversation, "iter_model_events", events)
    path = tmp_path / "reasoning conditions.md"
    path.write_text(
        '# Test\n\n```toml\n[llm]\nmodel="test/model"\n[textllm]\nbackend="responses"\nreasoning_summary="block"\nheader_level=2\n[[textllm.mcp]]\ncommand=["fixture"]\n```\n\n--- User ---\n\nWhat is 5+5?'
    )
    if final_answer:
        textllm.Conversation(path).chat()
    else:
        with pytest.raises(
            ValueError, match="without answer text after tool execution"
        ):
            textllm.Conversation(path).chat()
    text = path.read_text()
    assert text.endswith("## --- User ---\n\n")
    assert ("5 + 5 = 10." in text) == final_answer
    expected_reasoning = int(reasoning_where == "tool_and_final") + int(
        reasoning_where != "none"
    )
    assert text.count("[@sidecar reasoning]") == expected_reasoning
    keys = references(text)
    assert len(keys) == 1 + expected_reasoning
    assert Sidecar(path).read(keys[-1])["status"] == "complete"


@pytest.mark.parametrize("backend", ["completion", "responses", "messages"])
@pytest.mark.parametrize("mode", ["omit", "stderr", "block"])
@pytest.mark.parametrize("mcp_mode", ["disabled", "unused", "used"])
@pytest.mark.parametrize("final_payload", [False, True])
@pytest.mark.parametrize("tools_display", ["comment", "block"])
def test_final_native_reasoning_replays_from_sidecar_not_display(
    tmp_path, monkeypatch, backend, mode, mcp_mode, final_payload, tools_display
):
    import textllm.tool_loop as loop
    from textllm.history import Sidecar, references

    class Executor:
        tools = [{"name": "lookup", "parameters": {"type": "object"}}]

        def __init__(self, *args):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def validate(self, call):
            return {}

        def identity(self, call):
            return {"executor": "mcp", "server": "fixture", "tool": "lookup"}

        def execute(self, *args):
            return {"content": [{"type": "text", "text": "Found"}]}

    monkeypatch.setattr(loop, "MCPExecutor", Executor)
    answer = "Final answer."
    if backend == "completion":
        native = {
            "role": "assistant",
            "content": answer,
            "reasoning_content": "Native thought.",
            "reasoning_details": [
                {"type": "reasoning.encrypted", "data": "opaque-value"}
            ],
        }
        response = native
    elif backend == "responses":
        native = [
            {
                "type": "reasoning",
                "id": "r1",
                "summary": [],
                "encrypted_content": "opaque-value",
            },
            {
                "type": "message",
                "role": "assistant",
                "phase": "final_answer",
                "content": [{"type": "output_text", "text": answer}],
            },
        ]
        response = {"output": native}
    else:
        native = [
            {
                "type": "thinking",
                "thinking": "Native thought.",
                "signature": "opaque-value",
            },
            {"type": "text", "text": answer},
        ]
        response = {"content": native, "stop_reason": "end_turn"}
    requests = []

    def events(**kwargs):
        requests.append(kwargs)
        if mcp_mode == "used" and len(requests) == 1:
            assert not Path(str(path) + ".db").exists()
            if backend == "completion":
                tool_response = {
                    "tool_calls": [
                        {
                            "id": "c1",
                            "type": "function",
                            "function": {"name": "lookup", "arguments": "{}"},
                        }
                    ]
                }
            elif backend == "responses":
                tool_response = {
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "c1",
                            "name": "lookup",
                            "arguments": "{}",
                        }
                    ]
                }
            else:
                tool_response = {
                    "content": [
                        {"type": "tool_use", "id": "c1", "name": "lookup", "input": {}}
                    ]
                }
            yield backends.ModelEvent("completed", data=tool_response)
            return
        yield backends.ModelEvent("reasoning_summary_delta", text="Visible thought.")
        yield backends.ModelEvent(
            "output_text_delta", text=answer,
            data={"from_completed": True} if final_payload else None,
        )
        yield backends.ModelEvent("completed", data=response)

    monkeypatch.setattr(conversation, "iter_model_events", events)
    path = tmp_path / "final continuation.md"
    path.write_text(
        f'# Test\n\n```toml\n[llm]\nmodel="test/model"\n[textllm]\nbackend="{backend}"\nreasoning_summary="{mode}"\n'
        + f'tools_display="{tools_display}"\n'
        + ('[[textllm.mcp]]\ncommand=["fixture"]\n' if mcp_mode != "disabled" else "")
        + "```\n\n--- User ---\n\nHi"
    )
    textllm.Conversation(path).chat()
    text = path.read_text()
    assert ("Visible thought." in text) == (mode == "block")
    if mcp_mode != "used":
        assert references(text) == []
        assert not Path(str(path) + ".db").exists()
        assert all(
            "_native" not in item for item in textllm.Conversation(path).messages
        )
        return
    _, key = references(text)
    assert f"[@sidecar reasoning]({key})" in text
    assert text.count("[@sidecar tool exchange]") == 1
    answer_position = text.index("--- Assistant ---")
    reference_position = text.index(f"[@sidecar reasoning]({key})")
    assert (reference_position < answer_position) == final_payload
    if final_payload and mode != "block":
        assert text.count("--- Tools ---") == 1
        assert f")\n[@sidecar reasoning]({key})" in text
    record = Sidecar(path).read(key)
    assert "assistant_native" in record
    assert record["kind"] == "reasoning"
    # Display-only reasoning can be edited, added, or removed independently of
    # the opaque native state. It must never become ordinary model prose.
    edited = text.replace("Visible thought.", "FORGED DISPLAY")
    if "FORGED DISPLAY" not in edited:
        boundary = (
            "\n\n<!--\n--- Tools ---"
            if tools_display == "comment"
            else "\n\n--- Tools ---"
        )
        edited = edited.replace(
            boundary,
            "\n\n--- Reasoning ---\n\nFORGED DISPLAY" + boundary,
        )
    path.write_text(edited + "Next question")
    loaded = textllm.Conversation(path)
    loaded.call_llm(loaded.messages)
    messages = requests[-1]["messages"]
    assistant = [message for message in messages if message["role"] == "assistant"][-1]
    assert assistant["_native"] == native
    assert assistant["content"] == answer
    assert len([message for message in messages if message["role"] == "assistant"]) == 2
    if backend == "completion":
        wire = backends._completion_messages(messages)
    elif backend == "responses":
        wire = backends._responses_messages(messages)
        assert (
            next(item for item in wire if item.get("role") == "assistant")["phase"]
            == "final_answer"
        )
    else:
        _, wire = backends._messages_request(messages)
    serialized = json.dumps(wire)
    assert serialized.count("opaque-value") == 1
    assert "FORGED DISPLAY" not in serialized
    assert "_native" not in serialized
    # Removing the reference block excludes native state without discarding
    # the editable assistant answer or loading unreferenced database records.
    without_reference = edited.replace(f"[@sidecar reasoning]({key})", "")
    without_reference = re.sub(
        r"\n\n(?:<!--\n--- Tools ---\n\s*-->|--- Tools ---\n\s*(?=---|$))",
        "\n\n",
        without_reference,
    )
    path.write_text(without_reference + "Next question")
    assert "opaque-value" not in json.dumps(textllm.Conversation(path).messages)


@pytest.mark.parametrize("header_level", range(7))
def test_commented_tools_parse_like_visible_blocks(header_level):
    marker = "#" * header_level + " " if header_level else ""
    body = f"{marker}--- Tools ---\n\n[@sidecar tool exchange](key)"
    start = "# Test\n\n--- User ---\n\nQuestion\n\n"
    end = "\n\n--- Assistant ---\n\nAnswer\n\n--- User ---\n\nNext"
    visible = textllm.loads(start + body + end)
    hidden = textllm.loads(start + "<!--\n" + body + "\n-->" + end)
    assert hidden == visible
    mixed = textllm.loads(start + body + "\n\n<!--\n" + body + "\n-->" + end)
    assert [item["role"] for item in mixed["conversation"]] == [
        "user",
        "tools",
        "tools",
        "assistant",
        "user",
    ]
    assert textllm.loads("--- User ---\n\n<!-- ordinary comment -->")[
        "conversation"
    ] == [{"role": "user", "content": "<!-- ordinary comment -->"}]


@pytest.mark.parametrize(
    "tail",
    [
        "",
        "\n--- User ---\n\nNext\n-->",
        "\n<!-- nested -->\n-->",
        "\n--> trailing text",
        "\n--- Tools ---\n\nOther\n-->",
    ],
)
def test_malformed_tools_comments_fail_before_mutation(tmp_path, tail):
    path = tmp_path / "malformed comment.md"
    source = (
        "--- User ---\n\nHi\n\n<!--\n--- Tools ---\n\n[@sidecar tool exchange](key)"
        + tail
    )
    path.write_text(source)
    with pytest.raises(ValueError, match="Tools comment"):
        textllm.Conversation(path).chat()
    assert path.read_text() == source


@pytest.mark.parametrize("value", ['"hidden"', "true", "1", "[]", "{}"])
def test_invalid_tools_display_fails_before_mutation(tmp_path, value):
    path = tmp_path / "invalid display.md"
    source = f"```toml\n[textllm]\ntools_display={value}\n```\n\n--- User ---\n\nHi"
    path.write_text(source)
    with pytest.raises(ValueError, match="textllm.tools_display"):
        textllm.Conversation(path).chat()
    assert path.read_text() == source
    assert not Path(str(path) + ".db").exists()


@pytest.mark.parametrize("failure", [ValueError, KeyboardInterrupt])
@pytest.mark.parametrize("header_level", [0, 2])
def test_tools_comment_closes_on_chat_failure(
    tmp_path, monkeypatch, failure, header_level
):
    path = tmp_path / "interrupted hidden tools.md"
    path.write_text("--- User ---\n\nHi")

    def call(self, **kwargs):
        writer = kwargs["stream_writer"]
        writer.write("Checking.")
        writer.write_reference("Tools", "key", "tool exchange")
        writer.flush()
        raise failure("stopped")

    monkeypatch.setattr(textllm.Conversation, "call_llm", call)
    with pytest.raises(failure, match="stopped"):
        textllm.Conversation(path).chat(header_level=header_level)
    source = path.read_text()
    prefix = "#" * header_level + " " if header_level else ""
    assert source.endswith(f"\n-->\n\n{prefix}--- User ---\n\n")
    parsed = textllm.loads(source)
    assert parsed["conversation"][-1] == {
        "role": "tools",
        "content": "[@sidecar tool exchange](key)",
    }
    assert parsed["conversation"][1]["content"] == "Checking."


@pytest.mark.parametrize("tokens", ["none", "note"])
@pytest.mark.parametrize("tools_display", ["comment", "block"])
def test_final_tools_block_closes_before_notes_and_user(
    tmp_path, monkeypatch, tokens, tools_display
):
    from textllm.backends import LLMResponse
    from textllm.history import Sidecar

    path = tmp_path / "final tools and usage.md"
    source = '```toml\n[textllm]\ntools_display="block"\n```\n\n--- User ---\n\nHi'
    path.write_text(source)

    def call(self, **kwargs):
        key = Sidecar(path).save(
            {
                "kind": "reasoning",
                "backend": "completion",
                "status": "complete",
                "native": {"reasoning_content": "Native thought"},
                "assistant_native": {"role": "assistant", "content": "Answer."},
            }
        )
        writer = kwargs["stream_writer"]
        writer.write("Answer.")
        writer.write_reference("Tools", key, "reasoning")
        return LLMResponse(
            "Answer.", usage_metadata={"input_tokens": 2, "output_tokens": 3}
        )

    monkeypatch.setattr(textllm.Conversation, "call_llm", call)
    textllm.Conversation(
        path, overrides={"textllm": {"tools_display": tools_display}}
    ).chat(tokens=tokens)
    written = path.read_text()
    assert written.startswith(source)
    assert written.count("<!--") == written.count("-->") == (tools_display == "comment")
    assert written.endswith("--- User ---\n\n")
    if tools_display == "comment":
        assert written.index("-->") < written.rindex("--- User ---")
        if tokens == "note":
            assert written.index("-->") < written.index("--- Note ---")
    # Reading ignores the saved display choice and restores native state.
    loaded = textllm.Conversation(path)
    assert loaded.messages[-1]["content"] == "Answer."
    assert loaded.messages[-1]["_native"]["reasoning_content"] == "Native thought"
    assert ("--- Note ---" in written) == (tokens == "note")
