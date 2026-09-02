"""Run coding-agent evaluation cases in isolated temporary workspaces."""

from __future__ import annotations

import json
import tempfile
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.agent.loop import CancellationToken
from app.agent.service import AgentService
from app.agent.types import ModelClient
from app.db.database import build_engine, build_session_factory, create_schema
from app.db.models import SessionRecord


class EvalExpectations(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = "completed"
    files_equal: dict[str, str] = Field(default_factory=dict)
    files_contain: dict[str, str] = Field(default_factory=dict)
    required_tools: list[str] = Field(default_factory=list)
    final_response_contains: list[str] = Field(default_factory=list)
    max_tool_failures: int = Field(default=0, ge=0)
    max_steps: int | None = Field(default=None, ge=1)
    required_agent_roles: list[str] = Field(default_factory=list)


class EvalCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    max_steps: int = Field(default=20, ge=1, le=50)
    orchestration: Literal["single", "multi"] = "single"
    files: dict[str, str] = Field(default_factory=dict)
    expect: EvalExpectations = Field(default_factory=EvalExpectations)


class EvalSuite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    cases: list[EvalCase] = Field(min_length=1)


@dataclass(frozen=True, slots=True)
class EvalCheck:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True, slots=True)
class EvalCaseResult:
    case_id: str
    orchestration: str
    passed: bool
    duration_ms: int
    status: str
    step_count: int
    tool_calls: int
    tool_failures: int
    agent_executions: int
    checks: tuple[EvalCheck, ...]


@dataclass(frozen=True, slots=True)
class EvalModeSummary:
    orchestration: str
    passed_cases: int
    total_cases: int
    average_steps: float
    average_tool_failures: float


@dataclass(frozen=True, slots=True)
class EvalReport:
    suite: str
    passed: bool
    passed_cases: int
    total_cases: int
    duration_ms: int
    cases: tuple[EvalCaseResult, ...]
    modes: tuple[EvalModeSummary, ...]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def load_suite(path: str | Path) -> EvalSuite:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return EvalSuite.model_validate(payload)


class EvalRunner:
    """Evaluate each case with a fresh database, workspace, and model client."""

    def __init__(self, model_factory: Callable[[EvalCase], ModelClient]) -> None:
        self._model_factory = model_factory

    def run(self, suite: EvalSuite) -> EvalReport:
        started = time.perf_counter()
        results = tuple(self._run_case(case) for case in suite.cases)
        return EvalReport(
            suite=suite.name,
            passed=all(result.passed for result in results),
            passed_cases=sum(result.passed for result in results),
            total_cases=len(results),
            duration_ms=self._duration_ms(started),
            cases=results,
            modes=self._mode_summaries(results),
        )

    def _run_case(self, case: EvalCase) -> EvalCaseResult:
        started = time.perf_counter()
        with tempfile.TemporaryDirectory(prefix=f"cocoding-eval-{case.id}-") as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            self._seed_workspace(workspace, case.files)
            engine = build_engine(f"sqlite:///{root / 'eval.db'}")
            create_schema(engine)
            session_factory = build_session_factory(engine)
            try:
                with session_factory() as db:
                    session = SessionRecord(title=case.id, workspace_path=str(workspace))
                    db.add(session)
                    db.commit()
                    db.refresh(session)
                    session_id = session.id
                service = AgentService(
                    session_factory,
                    self._model_factory(case),
                    multi_agent_enabled=case.orchestration == "multi",
                )
                created = service.create_run(session_id, case.prompt, case.max_steps)
                detail = service.execute_existing(created.id, CancellationToken())
                checks = self._checks(case, detail, workspace)
                failures = sum(call.status == "failed" for call in detail.tool_calls)
                return EvalCaseResult(
                    case_id=case.id,
                    orchestration=case.orchestration,
                    passed=all(check.passed for check in checks),
                    duration_ms=self._duration_ms(started),
                    status=detail.status,
                    step_count=detail.step_count,
                    tool_calls=len(detail.tool_calls),
                    tool_failures=failures,
                    agent_executions=len(detail.agent_executions),
                    checks=tuple(checks),
                )
            finally:
                engine.dispose()

    @staticmethod
    def _seed_workspace(workspace: Path, files: dict[str, str]) -> None:
        for relative, content in files.items():
            path = (workspace / relative).resolve()
            if Path(relative).is_absolute() or not path.is_relative_to(workspace):
                raise ValueError(f"Eval fixture path escapes workspace: {relative}")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

    @staticmethod
    def _checks(case: EvalCase, detail: object, workspace: Path) -> list[EvalCheck]:
        checks: list[EvalCheck] = []

        def add(name: str, passed: bool, detail_text: str) -> None:
            checks.append(EvalCheck(name, passed, detail_text))

        expected = case.expect
        status = getattr(detail, "status")
        add("status", status == expected.status, f"expected {expected.status}, got {status}")
        step_count = getattr(detail, "step_count")
        if expected.max_steps is not None:
            add(
                "max_steps",
                step_count <= expected.max_steps,
                f"expected <= {expected.max_steps}, got {step_count}",
            )
        tool_calls = getattr(detail, "tool_calls")
        tool_names = [call.name for call in tool_calls]
        for name in expected.required_tools:
            add(f"tool:{name}", name in tool_names, f"observed tools: {tool_names}")
        failures = sum(call.status == "failed" for call in tool_calls)
        add(
            "tool_failures",
            failures <= expected.max_tool_failures,
            f"expected <= {expected.max_tool_failures}, got {failures}",
        )
        agent_roles = [execution.role for execution in getattr(detail, "agent_executions")]
        for role in expected.required_agent_roles:
            add(
                f"agent_role:{role}",
                role in agent_roles,
                f"observed agent roles: {agent_roles}",
            )
        final_response = getattr(detail, "final_response") or ""
        for text in expected.final_response_contains:
            add(
                f"response_contains:{text}",
                text in final_response,
                f"final response did not contain {text!r}",
            )
        for relative, wanted in expected.files_equal.items():
            actual = EvalRunner._read_result_file(workspace, relative)
            add(f"file_equal:{relative}", actual == wanted, "file content mismatch")
        for relative, wanted in expected.files_contain.items():
            actual = EvalRunner._read_result_file(workspace, relative)
            add(f"file_contains:{relative}", actual is not None and wanted in actual, "text missing")
        return checks

    @staticmethod
    def _read_result_file(workspace: Path, relative: str) -> str | None:
        path = (workspace / relative).resolve()
        if Path(relative).is_absolute() or not path.is_relative_to(workspace) or not path.is_file():
            return None
        return path.read_text(encoding="utf-8")

    @staticmethod
    def _duration_ms(started: float) -> int:
        return int((time.perf_counter() - started) * 1_000)

    @staticmethod
    def _mode_summaries(
        results: tuple[EvalCaseResult, ...],
    ) -> tuple[EvalModeSummary, ...]:
        summaries: list[EvalModeSummary] = []
        for mode in ("single", "multi"):
            selected = [result for result in results if result.orchestration == mode]
            if not selected:
                continue
            summaries.append(
                EvalModeSummary(
                    orchestration=mode,
                    passed_cases=sum(result.passed for result in selected),
                    total_cases=len(selected),
                    average_steps=sum(result.step_count for result in selected) / len(selected),
                    average_tool_failures=(
                        sum(result.tool_failures for result in selected) / len(selected)
                    ),
                )
            )
        return tuple(summaries)
