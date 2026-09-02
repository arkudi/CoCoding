"""Independent evidence checks for structured agent completion requests."""

from __future__ import annotations

import json
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.agent.workspace import WorkspaceService
from app.db.run_repository import RunRepository


class CompletionTest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: str = Field(min_length=1)
    exit_code: int = 0


class TaskCompletion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=20_000)
    changed_files: list[str] = Field(default_factory=list)
    tests: list[CompletionTest] = Field(default_factory=list)
    unresolved_issues: list[str] = Field(default_factory=list)


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
    def __init__(self, repository: RunRepository, workspace: WorkspaceService) -> None:
        self._repository = repository
        self._workspace = workspace

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
        actual_files = {change.path for change in self._workspace.changes()}
        declared_files = set(completion.changed_files)
        missing = sorted(actual_files - declared_files)
        unsupported = sorted(declared_files - actual_files)
        if missing:
            errors.append(f"Changed files were not declared: {', '.join(missing)}")
        if unsupported:
            errors.append(f"Declared files have no write evidence: {', '.join(unsupported)}")

        detail = self._repository.get_run_detail(run_id)
        if detail is None:
            errors.append("Run evidence is unavailable.")
        else:
            successful_commands: set[tuple[str, int]] = set()
            for call in detail.tool_calls:
                if call.name != "run_command" or call.status != "succeeded":
                    continue
                try:
                    arguments = json.loads(call.arguments_json)
                    result = json.loads(call.result_json or "null")
                    command = arguments.get("command")
                    exit_code = (result.get("data") or {}).get("exit_code")
                except (json.JSONDecodeError, AttributeError, TypeError):
                    continue
                if isinstance(command, str) and isinstance(exit_code, int):
                    successful_commands.add((command, exit_code))
            for test in completion.tests:
                if (test.command, test.exit_code) not in successful_commands:
                    errors.append(
                        f"No successful run_command evidence for test: {test.command!r} "
                        f"with exit code {test.exit_code}."
                    )

        return CompletionVerification(not errors, completion, tuple(errors))
