from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.agent.loop import AgentLoop
from app.agent.tools import ToolRegistry
from app.agent.types import AssistantTurn, ToolCall
from app.agent.verifier import CompletionVerifier, finish_task_schema
from app.agent.workspace import WorkspaceService
from app.db.database import create_schema
from app.db.models import SessionRecord
from app.db.run_repository import RunRepository

from .fakes import ScriptedModelClient, finish


@dataclass
class VerificationContext:
    session: SessionRecord
    run: object
    repository: RunRepository
    registry: ToolRegistry
    workspace: WorkspaceService

    def loop(self, model: ScriptedModelClient) -> AgentLoop:
        return AgentLoop(model, self.registry, self.repository, self.workspace)


@pytest.fixture
def run_context(tmp_path: Path):
    engine = create_engine("sqlite://")
    create_schema(engine)
    database = sessionmaker(bind=engine, expire_on_commit=False)()
    workspace = WorkspaceService(tmp_path)
    session = SessionRecord(title="Agent", workspace_path=str(tmp_path))
    database.add(session)
    database.commit()
    database.refresh(session)
    repository = RunRepository(database)
    run = repository.create_run(
        session_id=session.id,
        prompt="inspect",
        model="fake",
        prompt_version="coding_agent_v1",
        max_steps=20,
    )
    try:
        yield VerificationContext(
            session, run, repository, ToolRegistry(workspace), workspace
        )
    finally:
        database.close()
        engine.dispose()


def call(name: str, arguments: dict[str, object], call_id: str) -> ToolCall:
    return ToolCall(call_id, name, json.dumps(arguments))


def test_finish_task_schema_is_strict() -> None:
    schema = finish_task_schema()["function"]["parameters"]

    assert schema["additionalProperties"] is False
    assert "summary" in schema["required"]


def test_loop_completes_from_verified_finish_task(run_context) -> None:
    model = ScriptedModelClient([
        AssistantTurn(None, (call("write_file", {"path": "done.txt", "content": "done"}, "c1"),)),
        AssistantTurn(None, (call("finish_task", {
            "summary": "Implemented the requested file.",
            "changed_files": ["done.txt"],
            "tests": [],
            "unresolved_issues": [],
        }, "c2"),)),
    ])

    result = run_context.loop(model).run(
        run_id=run_context.run.id,
        session_id=run_context.session.id,
        prompt="create done.txt",
        prior_messages=[],
        max_steps=5,
    )

    assert result.status == "completed"
    assert result.final_response == "Implemented the requested file."
    detail = run_context.repository.get_run_detail(run_context.run.id)
    assert [item.name for item in detail.tool_calls] == ["write_file", "finish_task"]
    assert detail.tool_calls[-1].status == "succeeded"
    assert any(tool["function"]["name"] == "finish_task" for tool in model.calls[0]["tools"])


def test_plain_response_is_progress_and_must_be_followed_by_finish_task(run_context) -> None:
    model = ScriptedModelClient([
        AssistantTurn("I think the task is done."),
        finish("Verified completion."),
    ])

    result = run_context.loop(model).run(
        run_id=run_context.run.id,
        session_id=run_context.session.id,
        prompt="inspect",
        prior_messages=[],
        max_steps=5,
    )

    assert result.status == "completed"
    assert result.final_response == "Verified completion."
    assert "plain response cannot complete" in model.calls[1]["messages"][-1]["content"]


def test_loop_returns_failed_completion_evidence_to_model(run_context) -> None:
    model = ScriptedModelClient([
        AssistantTurn(None, (call("finish_task", {
            "summary": "Incorrect claim.",
            "changed_files": ["missing.txt"],
        }, "c1"),)),
        AssistantTurn(None, (call("finish_task", {
            "summary": "No files were changed.",
            "changed_files": [],
        }, "c2"),)),
    ])

    result = run_context.loop(model).run(
        run_id=run_context.run.id,
        session_id=run_context.session.id,
        prompt="inspect",
        prior_messages=[],
        max_steps=5,
    )

    assert result.status == "completed"
    assert "COMPLETION_VERIFICATION_FAILED" in model.calls[1]["messages"][-1]["content"]
    detail = run_context.repository.get_run_detail(run_context.run.id)
    assert [item.status for item in detail.tool_calls] == ["failed", "succeeded"]


def test_verifier_rejects_test_claim_without_command_evidence(run_context) -> None:
    verifier = CompletionVerifier(run_context.repository, run_context.workspace)

    result = verifier.verify(run_context.run.id, json.dumps({
        "summary": "All tests pass.",
        "changed_files": [],
        "tests": [{"command": "pytest", "exit_code": 0}],
    }))

    assert result.ok is False
    assert "No successful run_command evidence" in result.errors[0]


def test_loop_accepts_test_claim_with_matching_command_evidence(run_context) -> None:
    command = "python --version"
    model = ScriptedModelClient([
        AssistantTurn(None, (call("run_command", {"command": command}, "c1"),)),
        AssistantTurn(None, (call("finish_task", {
            "summary": "Verification completed.",
            "changed_files": [],
            "tests": [{"command": command, "exit_code": 0}],
        }, "c2"),)),
    ])

    result = run_context.loop(model).run(
        run_id=run_context.run.id,
        session_id=run_context.session.id,
        prompt="verify",
        prior_messages=[],
        max_steps=5,
    )

    assert result.status == "completed"
    assert run_context.repository.get_run_detail(run_context.run.id).tool_calls[-1].status == "succeeded"
