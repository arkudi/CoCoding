from __future__ import annotations

import json
import threading
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.agent.orchestration import ROLE_TOOLS
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
            finish_subtask("The file is correct.", "reviewer-finish"),
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
