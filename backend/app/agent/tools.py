"""Validated workspace tools, including a bounded local command runner.

The command tool limits duration and returned output, but it is not a sandbox.
"""

import json
import logging
import os
import re
import signal
import subprocess
import time
from typing import Callable

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.agent.types import ToolCall, ToolError, ToolResult
from app.agent.workspace import WorkspaceError, WorkspaceService

logger = logging.getLogger(__name__)

_MAX_COMMAND_OUTPUT_CHARS = 20_000
_COMMAND_TOKEN = re.compile(r'''(?:"[^"]*"|'[^']*'|\S+)''')


class StrictArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ListFilesArgs(StrictArgs):
    path: str = "."


class ReadFileArgs(StrictArgs):
    path: str
    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)


class WriteFileArgs(StrictArgs):
    path: str
    content: str


class ReplaceInFileArgs(StrictArgs):
    path: str
    old_text: str
    new_text: str


class RunCommandArgs(StrictArgs):
    command: str = Field(min_length=1)
    timeout: int = Field(default=30, ge=1, le=120)


class GetDiffArgs(StrictArgs):
    pass


class _CommandTimeout(Exception):
    def __init__(self, data: dict[str, object], truncated: bool):
        super().__init__("command timed out")
        self.data = data
        self.truncated = truncated


class _DestructiveCommand(Exception):
    pass


class ToolRegistry:
    """Dispatch validated calls against one session's workspace service."""

    _tool_definitions: tuple[tuple[str, str, type[StrictArgs]], ...] = (
        ("list_files", "List non-ignored files in the workspace.", ListFilesArgs),
        ("read_file", "Read a UTF-8 text file from the workspace.", ReadFileArgs),
        ("write_file", "Write UTF-8 text to a workspace file.", WriteFileArgs),
        ("replace_in_file", "Replace exactly one occurrence in a workspace file.", ReplaceInFileArgs),
        ("run_command", "Run a bounded local command in the workspace.", RunCommandArgs),
        ("get_diff", "Get the workspace changes made during this run.", GetDiffArgs),
    )

    def __init__(self, workspace: WorkspaceService):
        self._workspace = workspace
        self._tools = {name: arguments for name, _, arguments in self._tool_definitions}
        self._handlers: dict[str, Callable[[StrictArgs], tuple[object, bool]]] = {
            "list_files": self._list_files,
            "read_file": self._read_file,
            "write_file": self._write_file,
            "replace_in_file": self._replace_in_file,
            "run_command": self._run_command,
            "get_diff": self._get_diff,
        }

    def schemas(self) -> list[dict[str, object]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": arguments.model_json_schema(),
                },
            }
            for name, description, arguments in self._tool_definitions
        ]

    def execute(self, call: ToolCall) -> ToolResult:
        started = time.perf_counter()
        try:
            arguments_type = self._tools.get(call.name)
            if arguments_type is None:
                return self._failure("UNKNOWN_TOOL", "The requested tool is not available.", started)
            try:
                arguments = arguments_type.model_validate(json.loads(call.arguments_json))
            except (json.JSONDecodeError, ValidationError, TypeError):
                return self._failure(
                    "INVALID_TOOL_ARGUMENTS", "Tool arguments must match the required schema.", started
                )
            data, truncated = self._handlers[call.name](arguments)
            return ToolResult(True, data, None, self._duration_ms(started), truncated)
        except _CommandTimeout as timeout:
            return ToolResult(
                False,
                timeout.data,
                ToolError("COMMAND_TIMEOUT", "The command exceeded its timeout."),
                self._duration_ms(started),
                timeout.truncated,
            )
        except WorkspaceError as error:
            return self._failure(error.code, error.message, started)
        except _DestructiveCommand as error:
            return self._failure("DESTRUCTIVE_COMMAND", str(error), started)
        except Exception:
            logger.exception("Agent tool execution failed (tool=%s)", call.name)
            return self._failure("TOOL_EXECUTION_ERROR", "The tool could not be executed.", started)

    @staticmethod
    def _duration_ms(started: float) -> int:
        return int((time.perf_counter() - started) * 1_000)

    def _failure(self, code: str, message: str, started: float) -> ToolResult:
        return ToolResult(False, None, ToolError(code, message), self._duration_ms(started))

    def _list_files(self, arguments: StrictArgs) -> tuple[object, bool]:
        assert isinstance(arguments, ListFilesArgs)
        data = self._workspace.list_files(arguments.path)
        return data, bool(data["truncated"])

    def _read_file(self, arguments: StrictArgs) -> tuple[object, bool]:
        assert isinstance(arguments, ReadFileArgs)
        return self._workspace.read_file(arguments.path, arguments.start_line, arguments.end_line), False

    def _write_file(self, arguments: StrictArgs) -> tuple[object, bool]:
        assert isinstance(arguments, WriteFileArgs)
        return self._workspace.write_file(arguments.path, arguments.content), False

    def _replace_in_file(self, arguments: StrictArgs) -> tuple[object, bool]:
        assert isinstance(arguments, ReplaceInFileArgs)
        return self._workspace.replace_in_file(arguments.path, arguments.old_text, arguments.new_text), False

    def _get_diff(self, arguments: StrictArgs) -> tuple[object, bool]:
        assert isinstance(arguments, GetDiffArgs)
        return {"diff": self._workspace.get_diff()}, False

    def _run_command(self, arguments: StrictArgs) -> tuple[object, bool]:
        assert isinstance(arguments, RunCommandArgs)
        if self._is_explicit_destructive_command(arguments.command):
            raise _DestructiveCommand("The command is explicitly destructive and cannot be run.")
        process = subprocess.Popen(
            arguments.command,
            cwd=self._workspace.root,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            start_new_session=os.name != "nt",
        )
        try:
            stdout, stderr = process.communicate(timeout=arguments.timeout)
        except subprocess.TimeoutExpired:
            self._terminate_process_tree(process)
            stdout, stderr = process.communicate()
            data, truncated = self._command_data(process.returncode, stdout, stderr)
            raise _CommandTimeout(data, truncated)
        return self._command_data(process.returncode, stdout, stderr)

    @staticmethod
    def _command_data(exit_code: int | None, stdout: str, stderr: str) -> tuple[dict[str, object], bool]:
        remaining = _MAX_COMMAND_OUTPUT_CHARS
        bounded_stdout = stdout[:remaining]
        remaining -= len(bounded_stdout)
        bounded_stderr = stderr[:remaining]
        truncated = len(bounded_stdout) != len(stdout) or len(bounded_stderr) != len(stderr)
        return {
            "exit_code": exit_code,
            "stdout": bounded_stdout,
            "stderr": bounded_stderr,
        }, truncated

    @staticmethod
    def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        else:
            os.killpg(process.pid, signal.SIGKILL)

    @staticmethod
    def _is_explicit_destructive_command(command: str) -> bool:
        tokens = [token.strip("\"'").casefold() for token in _COMMAND_TOKEN.findall(command.strip())]
        if not tokens:
            return False
        leading = tokens[0]
        if leading in {"format", "format.com", "format.exe", "shutdown", "shutdown.exe"}:
            return True
        if leading not in {"rm", "rm.exe", "remove-item", "del", "del.exe", "erase", "erase.exe"}:
            return False
        return any(ToolRegistry._is_root_target(token) for token in tokens[1:])

    @staticmethod
    def _is_root_target(token: str) -> bool:
        return token == "/" or re.fullmatch(r"[a-z]:(?:[\\/]+)?", token) is not None
