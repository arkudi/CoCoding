from __future__ import annotations

import json
import threading
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.agent.orchestration import ROLE_TOOLS, SharedStepBudget
from app.agent.service import AgentService
from app.agent.types import AssistantTurn, ToolCall
from app.db.database import create_schema
from app.db.models import SessionRecord
from tests.agent.fakes import ScriptedModelClient, finish


def call(name: str, arguments: dict[str, object], call_id: str) -> AssistantTurn:
    return AssistantTurn(None, (ToolCall(call_id, name, json.dumps(arguments)),))


def finish_subtask(
    summary: str,
    call_id: str,
    *,
    changed_files: list[str] | None = None,
    verdict: str | None = None,
) -> AssistantTurn:
    return call(
        "finish_subtask",
        {
            "summary": summary,
            "relevant_files": [],
            "findings": [],
            "changed_files": changed_files or [],
            "tests": [],
            "unresolved_issues": [],
            "verdict": verdict,
        },
        call_id,
    )


def delegate(role: str, task: str, call_id: str) -> AssistantTurn:
    return call(
        "delegate_task",
        {"role": role, "task": task, "expected_output": "A concise structured result."},
        call_id,
    )


def service_context(tmp_path: Path, model: ScriptedModelClient):
    engine = create_engine(f"sqlite:///{tmp_path / 'multi-agent.db'}")
    create_schema(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        session = SessionRecord(title="Team", workspace_path=str(tmp_path))
        db.add(session)
        db.commit()
        db.refresh(session)
        session_id = session.id
    service = AgentService(
        factory,
        model,
        execution_lock=threading.Lock(),
        multi_agent_enabled=True,
        max_delegations=3,
        child_step_limit=5,
    )
    return engine, service, session_id


def test_manager_runs_specialized_workers_serially_with_one_shared_budget(tmp_path: Path) -> None:
    model = ScriptedModelClient(
        [
            delegate("explorer", "Find the target file.", "manager-explore"),
            finish_subtask("The target should be note.txt.", "explorer-finish"),
            delegate("implementer", "Create note.txt.", "manager-implement"),
            call(
                "write_file",
                {"path": "note.txt", "content": "implemented"},
                "implementer-write",
            ),
            finish_subtask(
                "Created note.txt.", "implementer-finish", changed_files=["note.txt"]
            ),
            delegate("reviewer", "Review note.txt.", "manager-review"),
            call("read_file", {"path": "note.txt"}, "reviewer-read"),
            finish_subtask("The file is correct.", "reviewer-finish", verdict="approved"),
            finish("Implemented and reviewed.", changed_files=["note.txt"]),
        ]
    )
    engine, service, session_id = service_context(tmp_path, model)
    try:
        detail = service.execute(session_id, "Create note.txt", max_steps=20)
    finally:
        engine.dispose()

    assert detail.status == "completed"
    assert detail.step_count == 9
    assert (tmp_path / "note.txt").read_text(encoding="utf-8") == "implemented"
    assert [execution.role for execution in detail.agent_executions] == [
        "manager",
        "explorer",
        "implementer",
        "reviewer",
    ]
    assert all(execution.status == "completed" for execution in detail.agent_executions)
    assert all(tool.agent_execution_id is not None for tool in detail.tool_calls)
    assert [task.role for task in detail.agent_tasks] == [
        "explorer", "implementer", "reviewer",
    ]
    manager_tool_names = {
        tool["function"]["name"] for tool in model.calls[0]["tools"]
    }
    assert "delegate_task" in manager_tool_names
    assert "write_file" not in manager_tool_names
    implementer_tool_names = {
        tool["function"]["name"] for tool in model.calls[3]["tools"]
    }
    reviewer_tool_names = {
        tool["function"]["name"] for tool in model.calls[6]["tools"]
    }
    assert "write_file" in implementer_tool_names
    assert "write_file" not in reviewer_tool_names


def test_shared_budget_stops_worker_and_manager_at_total_limit(tmp_path: Path) -> None:
    model = ScriptedModelClient(
        [
            delegate("explorer", "Inspect files.", "manager-explore"),
            AssistantTurn("Still inspecting."),
        ]
    )
    engine, service, session_id = service_context(tmp_path, model)
    try:
        detail = service.execute(session_id, "Inspect", max_steps=2)
    finally:
        engine.dispose()

    assert detail.status == "max_steps"
    assert detail.step_count == 2
    assert [execution.status for execution in detail.agent_executions] == [
        "failed",
        "failed",
    ]


def test_only_implementer_role_has_write_permissions() -> None:
    write_tools = {"write_file", "replace_in_file", "apply_patch", "run_command"}

    assert write_tools <= ROLE_TOOLS["implementer"]
    assert write_tools.isdisjoint(ROLE_TOOLS["explorer"])
    assert write_tools.isdisjoint(ROLE_TOOLS["reviewer"])


def test_finish_is_rejected_until_latest_implementation_is_reviewed(tmp_path: Path) -> None:
    model = ScriptedModelClient(
        [
            delegate("implementer", "Create note.txt.", "delegate-implementer"),
            call("write_file", {"path": "note.txt", "content": "done"}, "write"),
            finish_subtask("Created it.", "implementer-finish", changed_files=["note.txt"]),
            finish("Premature.", changed_files=["note.txt"], call_id="early-finish"),
            delegate("reviewer", "Review note.txt.", "delegate-reviewer"),
            finish_subtask("Approved.", "reviewer-finish", verdict="approved"),
            finish("Reviewed and complete.", changed_files=["note.txt"], call_id="final-finish"),
        ]
    )
    engine, service, session_id = service_context(tmp_path, model)
    try:
        detail = service.execute(session_id, "Create note.txt", max_steps=20)
    finally:
        engine.dispose()

    assert detail.status == "completed"
    early = next(tool for tool in detail.tool_calls if tool.provider_call_id == "early-finish")
    assert early.status == "failed"
    assert "REVIEW_REQUIRED" in (early.result_json or "")


def test_shared_budget_enforces_tokens_tools_and_delegations() -> None:
    token_budget = SharedStepBudget(10, token_limit=1)
    tool_budget = SharedStepBudget(10, tool_call_limit=1)
    delegation_budget = SharedStepBudget(10, delegation_limit=1)

    assert token_budget.consume([{"role": "user", "content": "12345678"}]) is False
    assert "token" in token_budget.last_error
    assert tool_budget.consume_tool_call() is True
    assert tool_budget.consume_tool_call() is False
    assert "tool-call" in tool_budget.last_error
    assert delegation_budget.consume_delegation() is True
    assert delegation_budget.consume_delegation() is False
    assert "delegation" in delegation_budget.last_error


def test_independent_read_only_workers_run_concurrently(tmp_path: Path) -> None:
    class ParallelModel:
        def __init__(self) -> None:
            self._manager_calls = 0
            self._lock = threading.Lock()
            self._barrier = threading.Barrier(2)
            self.active = 0
            self.max_active = 0

        def complete(self, messages, tools, on_text_delta=None):
            system = str(messages[0]["content"])
            if "bounded explorer worker" in system:
                with self._lock:
                    self.active += 1
                    self.max_active = max(self.max_active, self.active)
                self._barrier.wait(timeout=3)
                with self._lock:
                    self.active -= 1
                task = str(messages[1]["content"])
                return finish_subtask(f"Completed {task}", f"finish-{threading.get_ident()}")
            with self._lock:
                self._manager_calls += 1
                manager_call = self._manager_calls
            if manager_call == 1:
                return call(
                    "delegate_tasks",
                    {
                        "tasks": [
                            {"role": "explorer", "task": "Inspect A", "expected_output": "A"},
                            {"role": "explorer", "task": "Inspect B", "expected_output": "B"},
                        ]
                    },
                    "parallel-delegate",
                )
            return finish("Parallel inspection complete.")

    model = ParallelModel()
    engine, service, session_id = service_context(tmp_path, model)  # type: ignore[arg-type]
    try:
        detail = service.execute(session_id, "Inspect two areas", max_steps=10)
    finally:
        engine.dispose()

    assert detail.status == "completed"
    assert model.max_active == 2
    assert [execution.role for execution in detail.agent_executions] == [
        "manager", "explorer", "explorer",
    ]
    assert all(task.status == "completed" for task in detail.agent_tasks)
