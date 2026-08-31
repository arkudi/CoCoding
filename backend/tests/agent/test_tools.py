import json
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


def test_run_command_times_out_without_leaving_execution_running(tmp_path):
    registry = ToolRegistry(WorkspaceService(tmp_path))
    command = f'"{sys.executable}" -c "import time; time.sleep(10)"'

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


def test_run_command_limits_combined_output_to_twenty_thousand_characters(tmp_path):
    command = f'"{sys.executable}" -c "print(\'x\' * 15000); import sys; print(\'y\' * 15000, file=sys.stderr)"'
    result = ToolRegistry(WorkspaceService(tmp_path)).execute(
        call("run_command", {"command": command})
    )

    assert result.ok is True
    assert len(result.data["stdout"]) + len(result.data["stderr"]) == 20_000
    assert result.truncated is True


def test_run_command_rejects_empty_command(tmp_path):
    result = ToolRegistry(WorkspaceService(tmp_path)).execute(call("run_command", {"command": ""}))

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
        "ruff format backend",
        "echo rm -rf /",
        "python -m black .",
    ],
)
def test_run_command_allows_non_destructive_commands_with_similar_substrings(tmp_path, command):
    result = ToolRegistry(WorkspaceService(tmp_path)).execute(call("run_command", {"command": command}))

    assert result.ok is True
