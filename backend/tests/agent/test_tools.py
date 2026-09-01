import json
import subprocess
import sys

import pytest

from app.agent.tools import ToolRegistry
from app.agent.types import ToolCall
from app.agent.workspace import WorkspaceService


def call(name: str, arguments: object, call_id: str = "call-1") -> ToolCall:
    return ToolCall(id=call_id, name=name, arguments_json=json.dumps(arguments))


def fail_if_command_starts(*args, **kwargs):
    raise AssertionError("destructive command reached subprocess.Popen")


def test_execute_rejects_malformed_arguments_without_raising(tmp_path):
    result = ToolRegistry(WorkspaceService(tmp_path)).execute(
        ToolCall(id="c1", name="read_file", arguments_json="{")
    )

    assert result.ok is False
    assert result.data is None
    assert result.error is not None
    assert result.error.code == "INVALID_TOOL_ARGUMENTS"


@pytest.mark.parametrize(
    ("arguments", "reason"),
    [
        ({}, "missing required path"),
        ({"path": "a.txt", "unexpected": True}, "extra field"),
    ],
)
def test_execute_rejects_missing_or_extra_arguments(tmp_path, arguments, reason):
    result = ToolRegistry(WorkspaceService(tmp_path)).execute(call("read_file", arguments))

    assert result.ok is False, reason
    assert result.error is not None
    assert result.error.code == "INVALID_TOOL_ARGUMENTS"


def test_execute_rejects_unknown_tool(tmp_path):
    result = ToolRegistry(WorkspaceService(tmp_path)).execute(call("rename_file", {}))

    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "UNKNOWN_TOOL"


def test_schemas_export_all_six_strict_function_definitions(tmp_path):
    schemas = ToolRegistry(WorkspaceService(tmp_path)).schemas()

    assert {schema["function"]["name"] for schema in schemas} == {
        "list_files", "read_file", "write_file", "replace_in_file", "run_command", "get_diff",
    }
    read_file = next(schema for schema in schemas if schema["function"]["name"] == "read_file")
    assert read_file["type"] == "function"
    assert read_file["function"]["parameters"]["additionalProperties"] is False


def test_execute_dispatches_each_workspace_tool_and_serializes_result(tmp_path):
    (tmp_path / "seed.txt").write_text("one\n", encoding="utf-8")
    registry = ToolRegistry(WorkspaceService(tmp_path))

    listed = registry.execute(call("list_files", {}))
    read = registry.execute(call("read_file", {"path": "seed.txt"}))
    wrote = registry.execute(call("write_file", {"path": "new.txt", "content": "before"}))
    replaced = registry.execute(call("replace_in_file", {
        "path": "new.txt", "old_text": "before", "new_text": "after",
    }))
    diff = registry.execute(call("get_diff", {}))

    assert listed.ok is True and listed.data == {"files": ["seed.txt"], "truncated": False}
    assert read.ok is True and read.data["content"] == "one"
    assert wrote.ok is True and wrote.data["content"] == "before"
    assert replaced.ok is True and replaced.data["content"] == "after"
    assert diff.ok is True and "+after" in diff.data["diff"]
    assert json.loads(diff.to_json()) == {
        "ok": True,
        "data": {"diff": diff.data["diff"]},
        "error": None,
        "meta": {"duration_ms": diff.duration_ms, "truncated": False},
    }


def test_run_command_returns_nonzero_output(tmp_path):
    registry = ToolRegistry(WorkspaceService(tmp_path))
    command = f'"{sys.executable}" -c "import sys; print(\'bad\'); sys.exit(3)"'
    result = registry.execute(call("run_command", {"command": command, "timeout": 30}, "c2"))

    assert result.ok is True
    assert result.data["exit_code"] == 3
    assert "bad" in result.data["stdout"]


def test_run_command_times_out_with_inherited_child_pipe_handles(tmp_path):
    registry = ToolRegistry(WorkspaceService(tmp_path))
    script = tmp_path / "linger.py"
    script.write_text(
        "import subprocess, sys, time\n"
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )
    command = f'"{sys.executable}" "{script}"'

    result = registry.execute(call("run_command", {"command": command, "timeout": 1}))

    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "COMMAND_TIMEOUT"
    assert result.duration_ms < 5_000


def test_run_command_rejects_timeout_above_maximum(tmp_path):
    result = ToolRegistry(WorkspaceService(tmp_path)).execute(
        call("run_command", {"command": "echo safe", "timeout": 121})
    )

    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "INVALID_TOOL_ARGUMENTS"


def test_run_command_streams_verbose_output_without_using_communicate(
    tmp_path, monkeypatch
):
    real_popen = subprocess.Popen

    def popen_without_communicate(*args, **kwargs):
        process = real_popen(*args, **kwargs)

        def fail_communicate(*communicate_args, **communicate_kwargs):
            raise AssertionError("command output must be drained incrementally")

        process.communicate = fail_communicate
        return process

    monkeypatch.setattr("app.agent.tools.subprocess.Popen", popen_without_communicate)
    command = (
        f'"{sys.executable}" -c "print(\'x\' * 250000); '
        "import sys; print('y' * 250000, file=sys.stderr)\""
    )
    result = ToolRegistry(WorkspaceService(tmp_path)).execute(
        call("run_command", {"command": command})
    )

    assert result.ok is True
    assert len(result.data["stdout"]) + len(result.data["stderr"]) == 20_000
    assert result.truncated is True


def test_run_command_drains_mixed_stdout_and_stderr(tmp_path):
    command = (
        f'"{sys.executable}" -c "import sys; print(\'out\'); '
        "print('err', file=sys.stderr)\""
    )

    result = ToolRegistry(WorkspaceService(tmp_path)).execute(
        call("run_command", {"command": command})
    )

    assert result.ok is True
    assert result.data["stdout"] == "out\n"
    assert result.data["stderr"] == "err\n"
    assert result.truncated is False


def test_run_command_rejects_empty_command(tmp_path):
    result = ToolRegistry(WorkspaceService(tmp_path)).execute(call("run_command", {"command": ""}))

    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "INVALID_TOOL_ARGUMENTS"


def test_run_command_rejects_whitespace_only_command_before_subprocess(
    tmp_path, monkeypatch
):
    monkeypatch.setattr("app.agent.tools.subprocess.Popen", fail_if_command_starts)
    registry = ToolRegistry(WorkspaceService(tmp_path))

    result = registry.execute(
        call("run_command", {"command": " \t\r\n "})
    )

    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "INVALID_TOOL_ARGUMENTS"


@pytest.mark.parametrize(
    "command",
    [
        "rm -rf /",
        r"Remove-Item -Recurse C:\\",
        r"del /s /q C:\\",
        "format",
        "shutdown",
    ],
)
def test_run_command_rejects_explicit_destructive_system_commands(tmp_path, command, monkeypatch):
    monkeypatch.setattr("app.agent.tools.subprocess.Popen", fail_if_command_starts)
    result = ToolRegistry(WorkspaceService(tmp_path)).execute(call("run_command", {"command": command}))

    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "DESTRUCTIVE_COMMAND"


@pytest.mark.parametrize(
    "command",
    [
        "rm -rf /; echo safe",
        "rm -rf /&& echo safe",
        "rm -rf /|| echo safe",
        "rm -rf /| cat",
        r"Remove-Item -Recurse C:\; Write-Output safe",
        r"del /s /q C:\& echo safe",
    ],
)
def test_run_command_rejects_root_deletion_when_followed_by_attached_shell_separator(
    tmp_path, command, monkeypatch
):
    monkeypatch.setattr("app.agent.tools.subprocess.Popen", fail_if_command_starts)
    result = ToolRegistry(WorkspaceService(tmp_path)).execute(call("run_command", {"command": command}))

    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "DESTRUCTIVE_COMMAND"


@pytest.mark.parametrize(
    "command",
    [
        "echo safe\nrm -rf /",
        "echo safe\rRemove-Item -Recurse C:/",
        "echo safe\r\nC:\\Windows\\System32\\shutdown.exe",
        "/usr/bin/rm -rf /",
        "C:\\tools\\rm.exe -rf C:\\",
    ],
)
def test_run_command_rejects_destructive_segments_after_newlines_and_direct_paths(
    tmp_path, command, monkeypatch
):
    monkeypatch.setattr("app.agent.tools.subprocess.Popen", fail_if_command_starts)
    registry = ToolRegistry(WorkspaceService(tmp_path))

    result = registry.execute(call("run_command", {"command": command}))

    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "DESTRUCTIVE_COMMAND"


def test_preflight_preserves_newlines_inside_quotes(monkeypatch):
    command = 'echo "safe\nrm -rf /"'
    monkeypatch.setattr("app.agent.tools.subprocess.Popen", fail_if_command_starts)

    assert ToolRegistry._is_explicit_destructive_command(command) is False


@pytest.mark.parametrize(
    "command",
    [
        'rm -rf /";"safe',
        'rm -rf /"&&"safe',
        'rm -rf /"||"safe',
        'rm -rf /"|"safe',
        'rm -rf /"&"safe',
    ],
)
def test_preflight_allows_quoted_shell_operators_embedded_in_non_root_targets(command, monkeypatch):
    monkeypatch.setattr("app.agent.tools.subprocess.Popen", fail_if_command_starts)

    assert ToolRegistry._is_explicit_destructive_command(command) is False


@pytest.mark.parametrize(
    "command",
    [
        "ruff format backend",
        "echo rm -rf /",
        "python -m black .",
    ],
)
def test_preflight_allows_non_destructive_leading_commands_with_similar_substrings(command, monkeypatch):
    monkeypatch.setattr("app.agent.tools.subprocess.Popen", fail_if_command_starts)

    assert ToolRegistry._is_explicit_destructive_command(command) is False
