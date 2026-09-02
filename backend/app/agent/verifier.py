"""Independent evidence checks for structured agent completion requests."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.agent.workspace import WorkspaceService
from app.db.run_repository import RunRepository


class CompletionTest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: str = Field(min_length=1)
    exit_code: Literal[0] = 0


class AcceptanceCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    criterion: str = Field(min_length=1, max_length=2_000)
    status: Literal["passed", "failed", "not_run"]
    evidence: str = Field(min_length=1, max_length=2_000)


class TaskCompletion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=20_000)
    changed_files: list[str] = Field(default_factory=list)
    tests: list[CompletionTest] = Field(default_factory=list)
    verification_note: str | None = Field(default=None, min_length=1, max_length=2_000)
    acceptance_checks: list[AcceptanceCheck] = Field(default_factory=list)
    unresolved_issues: list[str] = Field(default_factory=list)


@dataclass(frozen=True, slots=True)
class VerificationPolicy:
    require_code_verification: bool = True
    allow_unverified_code_with_reason: bool = True
    require_resolved_test_failures: bool = True


_CODE_SUFFIXES = frozenset({
    ".c", ".cc", ".cpp", ".cs", ".css", ".go", ".h", ".hpp", ".html",
    ".java", ".js", ".jsx", ".kt", ".php", ".py", ".rb", ".rs", ".sh",
    ".sql", ".swift", ".ts", ".tsx", ".vue",
})


@dataclass(frozen=True, slots=True)
class CompletionVerification:
    ok: bool
    completion: TaskCompletion | None
    errors: tuple[str, ...]

    def payload(self) -> dict[str, object]:
        if self.ok:
            return {"verified": True}
        return {"verified": False, "errors": list(self.errors)}


def finish_task_schema() -> dict[str, object]:
    return {
        "type": "function",
        "function": {
            "name": "finish_task",
            "description": (
                "Submit the final result for evidence verification. Use this only when the task "
                "is complete or cannot progress further."
            ),
            "parameters": TaskCompletion.model_json_schema(),
        },
    }


class CompletionVerifier:
    def __init__(
        self,
        repository: RunRepository,
        workspace: WorkspaceService,
        policy: VerificationPolicy | None = None,
    ) -> None:
        self._repository = repository
        self._workspace = workspace
        self._policy = policy or VerificationPolicy()

    def verify(self, run_id: str, arguments_json: str) -> CompletionVerification:
        try:
            completion = TaskCompletion.model_validate(json.loads(arguments_json))
        except (json.JSONDecodeError, ValidationError, TypeError):
            return CompletionVerification(
                False,
                None,
                ("finish_task arguments must match the required schema.",),
            )

        errors: list[str] = []
        workspace_changes = self._workspace.changes()
        actual_files = {change.path for change in workspace_changes}
        declared_files = set(completion.changed_files)
        missing = sorted(actual_files - declared_files)
        unsupported = sorted(declared_files - actual_files)
        if missing:
            errors.append(f"Changed files were not declared: {', '.join(missing)}")
        if unsupported:
            errors.append(f"Declared files have no write evidence: {', '.join(unsupported)}")

        detail = self._repository.get_run_detail(run_id)
        command_results: list[tuple[str, str, int]] = []
        if detail is None:
            errors.append("Run evidence is unavailable.")
        else:
            for call in detail.tool_calls:
                if call.name not in {"run_command", "run_tests"} or call.status != "succeeded":
                    continue
                try:
                    arguments = json.loads(call.arguments_json)
                    result = json.loads(call.result_json or "null")
                    command = arguments.get("command")
                    exit_code = (result.get("data") or {}).get("exit_code")
                except (json.JSONDecodeError, AttributeError, TypeError):
                    continue
                if isinstance(command, str) and isinstance(exit_code, int):
                    command_results.append((call.name, command, exit_code))
            successful_commands = {
                (command, exit_code)
                for tool_name, command, exit_code in command_results
                if tool_name == "run_tests"
            }
            for test in completion.tests:
                if (test.command, test.exit_code) not in successful_commands:
                    errors.append(
                        f"No successful run_tests evidence for test: {test.command!r} "
                        f"with exit code {test.exit_code}."
                    )

        changed_code = sorted(
            change.path
            for change in workspace_changes
            if Path(change.path).suffix.casefold() in _CODE_SUFFIXES
        )
        if self._policy.require_code_verification and changed_code and not completion.tests:
            if not completion.verification_note:
                errors.append(
                    "Code changed without test evidence or an explicit verification_note: "
                    + ", ".join(changed_code)
                )
            elif not self._policy.allow_unverified_code_with_reason:
                errors.append("Policy requires successful test evidence for code changes.")

        if self._policy.require_resolved_test_failures:
            latest_tests: dict[str, int] = {}
            for tool_name, command, exit_code in command_results:
                if tool_name == "run_tests":
                    latest_tests[command] = exit_code
            failed_tests = sorted(
                command for command, exit_code in latest_tests.items() if exit_code != 0
            )
            if failed_tests and not completion.unresolved_issues:
                errors.append(
                    "Latest test runs failed but no unresolved issue was declared: "
                    + ", ".join(failed_tests)
                )

        incomplete_acceptance = [
            item.criterion for item in completion.acceptance_checks if item.status != "passed"
        ]
        if incomplete_acceptance and not completion.unresolved_issues:
            errors.append(
                "Acceptance checks are incomplete but no unresolved issue was declared: "
                + ", ".join(incomplete_acceptance)
            )

        return CompletionVerification(not errors, completion, tuple(errors))
