"""Validated workspace tools, including a bounded local command runner.

The command tool limits duration and returned output, but it is not a sandbox.
"""

import json
import logging
import os
import re
import signal
import subprocess
import threading
import time
from typing import Callable, TextIO

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.agent.types import ToolCall, ToolError, ToolResult
from app.agent.workspace import WorkspaceError, WorkspacePatch, WorkspaceService

logger = logging.getLogger(__name__)

_MAX_COMMAND_OUTPUT_CHARS = 20_000
_COMMAND_READ_CHUNK_CHARS = 4_096
_PROCESS_CLEANUP_TIMEOUT_SECONDS = 2.0
_COMMAND_OPERATORS = frozenset({";", "&&", "||", "|", "&", "\n"})


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


class SearchTextArgs(StrictArgs):
    query: str = Field(min_length=1)
    path: str = "."
    regex: bool = False
    case_sensitive: bool = False
    max_results: int = Field(default=100, ge=1, le=500)


class PatchEditArgs(StrictArgs):
    path: str
    old_text: str | None
    new_text: str


class ApplyPatchArgs(StrictArgs):
    patches: list[PatchEditArgs] = Field(min_length=1, max_length=50)


class RunCommandArgs(StrictArgs):
    command: str = Field(min_length=1)
    timeout: int = Field(default=30, ge=1, le=120)

    @field_validator("command")
    @classmethod
    def normalize_command(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("command must not be blank")
        return normalized


class RunTestsArgs(StrictArgs):
    command: str = Field(min_length=1)
    timeout: int = Field(default=120, ge=1, le=300)

    @field_validator("command")
    @classmethod
    def normalize_command(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("command must not be blank")
        return normalized


class GitStatusArgs(StrictArgs):
    pass


class GitDiffArgs(StrictArgs):
    staged: bool = False


class GetDiffArgs(StrictArgs):
    pass


class _CommandTimeout(Exception):
    def __init__(self, data: dict[str, object], truncated: bool):
        super().__init__("command timed out")
        self.data = data
        self.truncated = truncated


class _DestructiveCommand(Exception):
    pass


class _UnsupportedTestCommand(Exception):
    pass


class _BoundedCommandOutput:
    """Drain both pipes fully while retaining one shared bounded prefix."""

    def __init__(self, limit: int) -> None:
        self._remaining = limit
        self._parts: dict[str, list[str]] = {"stdout": [], "stderr": []}
        self._truncated = False
        self._errors: list[Exception] = []
        self._lock = threading.Lock()

    def drain(self, stream: TextIO, output_name: str) -> None:
        try:
            while True:
                chunk = stream.read(_COMMAND_READ_CHUNK_CHARS)
                if not chunk:
                    return
                with self._lock:
                    kept = chunk[: self._remaining]
                    if kept:
                        self._parts[output_name].append(kept)
                        self._remaining -= len(kept)
                    if len(kept) != len(chunk):
                        self._truncated = True
        except (OSError, ValueError) as error:
            with self._lock:
                self._errors.append(error)
        finally:
            try:
                stream.close()
            except OSError:
                pass

    def mark_truncated(self) -> None:
        with self._lock:
            self._truncated = True

    def snapshot(self) -> tuple[str, str, bool, tuple[Exception, ...]]:
        with self._lock:
            return (
                "".join(self._parts["stdout"]),
                "".join(self._parts["stderr"]),
                self._truncated,
                tuple(self._errors),
            )


class ToolRegistry:
    """Dispatch validated calls against one session's workspace service."""

    _tool_definitions: tuple[tuple[str, str, type[StrictArgs]], ...] = (
        ("list_files", "List non-ignored files in the workspace.", ListFilesArgs),
        ("read_file", "Read a UTF-8 text file from the workspace.", ReadFileArgs),
        ("search_text", "Search workspace text and return matching lines.", SearchTextArgs),
        ("write_file", "Write UTF-8 text to a workspace file.", WriteFileArgs),
        ("replace_in_file", "Replace exactly one occurrence in a workspace file.", ReplaceInFileArgs),
        ("apply_patch", "Apply one or more exact text edits after validating the entire patch.", ApplyPatchArgs),
        ("run_command", "Run a bounded local command in the workspace.", RunCommandArgs),
        ("run_tests", "Run a recognized test command and return a structured summary.", RunTestsArgs),
        ("git_status", "Get concise Git working-tree status.", GitStatusArgs),
        ("git_diff", "Get the staged or unstaged Git diff.", GitDiffArgs),
        ("get_diff", "Get the workspace changes made during this run.", GetDiffArgs),
    )

    def __init__(self, workspace: WorkspaceService):
        self._workspace = workspace
        self._tools = {name: arguments for name, _, arguments in self._tool_definitions}
        self._handlers: dict[str, Callable[[StrictArgs], tuple[object, bool]]] = {
            "list_files": self._list_files,
            "read_file": self._read_file,
            "search_text": self._search_text,
            "write_file": self._write_file,
            "replace_in_file": self._replace_in_file,
            "apply_patch": self._apply_patch,
            "run_command": self._run_command,
            "run_tests": self._run_tests,
            "git_status": self._git_status,
            "git_diff": self._git_diff,
            "get_diff": self._get_diff,
        }

    def schemas(self, allowed_names: set[str] | frozenset[str] | None = None) -> list[dict[str, object]]:
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
            if allowed_names is None or name in allowed_names
        ]

    def execute(
        self,
        call: ToolCall,
        allowed_names: set[str] | frozenset[str] | None = None,
    ) -> ToolResult:
        started = time.perf_counter()
        try:
            if allowed_names is not None and call.name not in allowed_names:
                return self._failure(
                    "TOOL_NOT_ALLOWED",
                    "This agent role is not allowed to use the requested tool.",
                    started,
                )
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
        except _UnsupportedTestCommand as error:
            return self._failure("UNSUPPORTED_TEST_COMMAND", str(error), started)
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

    def _search_text(self, arguments: StrictArgs) -> tuple[object, bool]:
        assert isinstance(arguments, SearchTextArgs)
        data = self._workspace.search_text(
            arguments.query,
            arguments.path,
            regex=arguments.regex,
            case_sensitive=arguments.case_sensitive,
            max_results=arguments.max_results,
        )
        return data, bool(data["truncated"])

    def _write_file(self, arguments: StrictArgs) -> tuple[object, bool]:
        assert isinstance(arguments, WriteFileArgs)
        return self._workspace.write_file(arguments.path, arguments.content), False

    def _replace_in_file(self, arguments: StrictArgs) -> tuple[object, bool]:
        assert isinstance(arguments, ReplaceInFileArgs)
        return self._workspace.replace_in_file(arguments.path, arguments.old_text, arguments.new_text), False

    def _apply_patch(self, arguments: StrictArgs) -> tuple[object, bool]:
        assert isinstance(arguments, ApplyPatchArgs)
        patches = tuple(
            WorkspacePatch(item.path, item.old_text, item.new_text)
            for item in arguments.patches
        )
        return self._workspace.apply_patch(patches), False

    def _get_diff(self, arguments: StrictArgs) -> tuple[object, bool]:
        assert isinstance(arguments, GetDiffArgs)
        return {"diff": self._workspace.get_diff()}, False

    def _run_command(self, arguments: StrictArgs) -> tuple[object, bool]:
        assert isinstance(arguments, RunCommandArgs)
        return self._execute_command(arguments.command, arguments.timeout)

    def _run_tests(self, arguments: StrictArgs) -> tuple[object, bool]:
        assert isinstance(arguments, RunTestsArgs)
        if not self._is_supported_test_command(arguments.command):
            raise _UnsupportedTestCommand(
                "run_tests accepts a single recognized test-runner command without shell operators."
            )
        data, truncated = self._execute_command(arguments.command, arguments.timeout)
        assert isinstance(data, dict)
        combined = f"{data['stdout']}\n{data['stderr']}"
        data["test_summary"] = self._test_summary(combined)
        data["command"] = arguments.command
        return data, truncated

    def _git_status(self, arguments: StrictArgs) -> tuple[object, bool]:
        assert isinstance(arguments, GitStatusArgs)
        data, truncated = self._execute_command("git status --short", 30)
        return data, truncated

    def _git_diff(self, arguments: StrictArgs) -> tuple[object, bool]:
        assert isinstance(arguments, GitDiffArgs)
        command = "git diff --cached --no-ext-diff --" if arguments.staged else "git diff --no-ext-diff --"
        data, truncated = self._execute_command(command, 30)
        return data, truncated

    def _execute_command(self, command: str, timeout: int) -> tuple[object, bool]:
        if self._is_explicit_destructive_command(command):
            raise _DestructiveCommand("The command is explicitly destructive and cannot be run.")
        process = subprocess.Popen(
            command,
            cwd=self._workspace.root,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            start_new_session=os.name != "nt",
        )
        assert process.stdout is not None
        assert process.stderr is not None
        output = _BoundedCommandOutput(_MAX_COMMAND_OUTPUT_CHARS)
        readers = (
            threading.Thread(
                target=output.drain,
                args=(process.stdout, "stdout"),
                daemon=True,
            ),
            threading.Thread(
                target=output.drain,
                args=(process.stderr, "stderr"),
                daemon=True,
            ),
        )
        for reader in readers:
            reader.start()

        timed_out = False
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            cleanup_deadline = time.monotonic() + _PROCESS_CLEANUP_TIMEOUT_SECONDS
            self._terminate_process_tree(process, cleanup_deadline)

        cleanup_deadline = time.monotonic() + _PROCESS_CLEANUP_TIMEOUT_SECONDS
        readers_finished = self._join_readers(readers, cleanup_deadline)
        if not readers_finished:
            output.mark_truncated()
            logger.warning("Command pipe cleanup exceeded its deadline")

        stdout, stderr, truncated, read_errors = output.snapshot()
        if read_errors and not timed_out:
            raise read_errors[0]
        data = {
            "exit_code": process.returncode,
            "stdout": stdout,
            "stderr": stderr,
        }
        if timed_out:
            raise _CommandTimeout(data, truncated)
        return data, truncated

    @staticmethod
    def _is_supported_test_command(command: str) -> bool:
        tokens = ToolRegistry._tokenize_command(command)
        if not tokens or any(token in _COMMAND_OPERATORS for token in tokens):
            return False
        folded = [token.casefold() for token in tokens]
        executable = re.split(r"[\\/]", folded[0])[-1]
        if executable in {"pytest", "pytest.exe"}:
            return True
        if executable in {"python", "python.exe", "python3", "python3.exe", "py", "py.exe"}:
            return len(folded) >= 3 and folded[1:3] == ["-m", "pytest"]
        if executable in {"npm", "npm.cmd", "pnpm", "pnpm.cmd", "yarn", "yarn.cmd", "bun", "bun.exe"}:
            return len(folded) >= 2 and (
                folded[1] == "test"
                or (folded[1] == "run" and len(folded) >= 3 and folded[2].startswith("test"))
            )
        return len(folded) >= 2 and (executable, folded[1]) in {
            ("cargo", "test"),
            ("cargo.exe", "test"),
            ("go", "test"),
            ("go.exe", "test"),
            ("dotnet", "test"),
            ("dotnet.exe", "test"),
        }

    @staticmethod
    def _test_summary(output: str) -> dict[str, int]:
        summary: dict[str, int] = {}
        for label in ("passed", "failed", "skipped", "errors"):
            matches = re.findall(rf"(\d+)\s+{label}\b", output, flags=re.IGNORECASE)
            if matches:
                summary[label] = max(int(value) for value in matches)
        return summary

    @staticmethod
    def _join_readers(
        readers: tuple[threading.Thread, threading.Thread], deadline: float
    ) -> bool:
        for reader in readers:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            reader.join(remaining)
        return all(not reader.is_alive() for reader in readers)

    @staticmethod
    def _terminate_process_tree(
        process: subprocess.Popen[str], deadline: float
    ) -> None:
        if os.name == "nt":
            remaining = max(0.01, deadline - time.monotonic())
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    timeout=remaining,
                )
            except (OSError, subprocess.TimeoutExpired):
                logger.warning("Windows process-tree termination did not complete in time")
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

        remaining = deadline - time.monotonic()
        if remaining > 0:
            try:
                process.wait(timeout=remaining)
                return
            except subprocess.TimeoutExpired:
                pass
        if process.poll() is None:
            try:
                process.kill()
            except OSError:
                return
            remaining = deadline - time.monotonic()
            if remaining > 0:
                try:
                    process.wait(timeout=remaining)
                except subprocess.TimeoutExpired:
                    logger.warning("Command process cleanup exceeded its deadline")

    @staticmethod
    def _is_explicit_destructive_command(command: str) -> bool:
        tokens = [token.casefold() for token in ToolRegistry._tokenize_command(command)]
        segment: list[str] = []
        for token in tokens:
            if token in _COMMAND_OPERATORS:
                if ToolRegistry._is_explicit_destructive_segment(segment):
                    return True
                segment = []
            else:
                segment.append(token)
        return ToolRegistry._is_explicit_destructive_segment(segment)

    @staticmethod
    def _tokenize_command(command: str) -> list[str]:
        """Split focused shell syntax while preserving separators inside quotes."""
        tokens: list[str] = []
        word: list[str] = []
        quote: str | None = None
        index = 0
        while index < len(command):
            character = command[index]
            if quote is not None:
                if character == quote:
                    quote = None
                else:
                    word.append(character)
                index += 1
                continue
            if character in {"\"", "'"}:
                quote = character
            elif character in {"\r", "\n"}:
                if word:
                    tokens.append("".join(word))
                    word = []
                tokens.append("\n")
                if character == "\r" and index + 1 < len(command) and command[index + 1] == "\n":
                    index += 1
            elif character.isspace():
                if word:
                    tokens.append("".join(word))
                    word = []
            elif character in {";", "|", "&"}:
                if word:
                    tokens.append("".join(word))
                    word = []
                if character in {"|", "&"} and index + 1 < len(command) and command[index + 1] == character:
                    tokens.append(character * 2)
                    index += 1
                else:
                    tokens.append(character)
            else:
                word.append(character)
            index += 1
        if word:
            tokens.append("".join(word))
        return tokens

    @staticmethod
    def _is_explicit_destructive_segment(tokens: list[str]) -> bool:
        if not tokens:
            return False
        leading = re.split(r"[\\/]", tokens[0])[-1]
        if leading in {"format", "format.com", "format.exe", "shutdown", "shutdown.exe"}:
            return True
        if leading not in {"rm", "rm.exe", "remove-item", "del", "del.exe", "erase", "erase.exe"}:
            return False
        return any(ToolRegistry._is_root_target(token) for token in tokens[1:])

    @staticmethod
    def _is_root_target(token: str) -> bool:
        return token == "/" or re.fullmatch(r"[a-z]:(?:[\\/]+)?", token) is not None
