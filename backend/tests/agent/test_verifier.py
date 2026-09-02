from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.agent.loop import AgentLoop
from app.agent.tools import ToolRegistry
from app.agent.types import AssistantTurn, ToolCall
from app.agent.verifier import CompletionVerifier, VerificationPolicy, finish_task_schema
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
    assert "No successful run_tests evidence" in result.errors[0]


def test_verifier_requires_evidence_or_reason_for_code_changes(run_context) -> None:
    run_context.workspace.write_file("module.py", "value = 1\n")
    verifier = CompletionVerifier(run_context.repository, run_context.workspace)

    rejected = verifier.verify(run_context.run.id, json.dumps({
        "summary": "Changed code.",
        "changed_files": ["module.py"],
    }))
    accepted = verifier.verify(run_context.run.id, json.dumps({
        "summary": "Changed code without runnable tests.",
        "changed_files": ["module.py"],
        "verification_note": "The isolated fixture has no test runner configured.",
    }))

    assert rejected.ok is False
    assert "Code changed without test evidence" in rejected.errors[0]
    assert accepted.ok is True


def test_strict_policy_rejects_unverified_code_even_with_reason(run_context) -> None:
    run_context.workspace.write_file("module.py", "value = 1\n")
    verifier = CompletionVerifier(
        run_context.repository,
        run_context.workspace,
        VerificationPolicy(allow_unverified_code_with_reason=False),
    )

    result = verifier.verify(run_context.run.id, json.dumps({
        "summary": "Changed code.",
        "changed_files": ["module.py"],
        "verification_note": "No tests exist.",
    }))

    assert result.ok is False
    assert "Policy requires successful test evidence" in result.errors[0]


def test_verifier_requires_latest_test_failure_to_be_disclosed(run_context) -> None:
    tool = run_context.repository.start_tool_call(
        run_context.run.id, "test-1", "run_tests", '{"command":"pytest"}'
    )
    run_context.repository.finish_tool_call(
        tool.id,
        "succeeded",
        json.dumps({
            "ok": True,
            "data": {"command": "pytest", "exit_code": 1},
            "error": None,
            "meta": {"duration_ms": 1, "truncated": False},
        }),
        1,
    )
    verifier = CompletionVerifier(run_context.repository, run_context.workspace)

    rejected = verifier.verify(run_context.run.id, json.dumps({
        "summary": "Tests failed.", "changed_files": [],
    }))
    accepted = verifier.verify(run_context.run.id, json.dumps({
        "summary": "Tests failed.",
        "changed_files": [],
        "unresolved_issues": ["pytest is still failing"],
    }))

    assert rejected.ok is False
    assert "Latest test runs failed" in rejected.errors[0]
    assert accepted.ok is True


def test_incomplete_acceptance_check_requires_unresolved_issue(run_context) -> None:
    verifier = CompletionVerifier(run_context.repository, run_context.workspace)
    payload = {
        "summary": "Partially complete.",
        "changed_files": [],
        "acceptance_checks": [{
            "criterion": "All tests pass",
            "status": "not_run",
            "evidence": "No test command is available",
        }],
    }

    result = verifier.verify(run_context.run.id, json.dumps(payload))

    assert result.ok is False
    assert "Acceptance checks are incomplete" in result.errors[0]


def test_loop_accepts_test_claim_with_matching_run_tests_evidence(run_context) -> None:
    (run_context.workspace.root / "test_sample.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )
    command = f'"{sys.executable}" -m pytest -q'
    model = ScriptedModelClient([
        AssistantTurn(None, (call("run_tests", {"command": command}, "c1"),)),
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


def test_finish_task_verifies_file_created_by_command(run_context) -> None:
    command = (
        f'"{sys.executable}" -c "from pathlib import Path; '
        "Path('command.txt').write_text('made', encoding='utf-8')\""
    )
    model = ScriptedModelClient([
        AssistantTurn(None, (call("run_command", {"command": command}, "c1"),)),
        finish("Created by command.", changed_files=["command.txt"]),
    ])

    result = run_context.loop(model).run(
        run_id=run_context.run.id,
        session_id=run_context.session.id,
        prompt="create through a command",
        prior_messages=[],
        max_steps=5,
    )

    assert result.status == "completed"
    detail = run_context.repository.get_run_detail(run_context.run.id)
    assert detail.file_changes[0].path == "command.txt"
    assert detail.file_changes[0].operation == "created"
