from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.agent.loop import AgentLoop, CancellationToken
from app.agent.tools import ToolRegistry
from app.agent.types import AssistantTurn, ToolCall, ToolError, ToolResult
from app.agent.workspace import WorkspaceService
from app.db.database import create_schema
from app.db.models import SessionRecord
from app.db.run_repository import RunRepository
from tests.agent.fakes import ScriptedModelClient


@dataclass
class RunContext:
    session: SessionRecord
    run: object
    repository: RunRepository
    registry: ToolRegistry
    workspace: WorkspaceService


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
        session_id=session.id, prompt="inspect", model="fake", prompt_version="coding_agent_v1", max_steps=20,
    )
    context = RunContext(session, run, repository, ToolRegistry(workspace), workspace)
    try:
        yield context
    finally:
        database.close()
        engine.dispose()


def execute(name: str, arguments: dict[str, object], call_id: str) -> ToolCall:
    return ToolCall(call_id, name, json.dumps(arguments))


def test_loop_executes_tool_then_completes(run_context):
    (run_context.workspace.root / "a.txt").write_text("hello", encoding="utf-8")
    model = ScriptedModelClient([
        AssistantTurn(None, (execute("read_file", {"path": "a.txt"}, "c1"),)),
        AssistantTurn("Read the file; no changes were needed."),
    ])
    loop = AgentLoop(model, run_context.registry, run_context.repository, run_context.workspace)

    result = loop.run(
        run_id=run_context.run.id, session_id=run_context.session.id,
        prompt="inspect a.txt", prior_messages=[], max_steps=20,
    )

    assert result.status == "completed"
    assert result.step_count == 2
    assert model.calls[1]["messages"][-1]["role"] == "tool"
    assert model.calls[1]["messages"][-1]["tool_call_id"] == "c1"
    detail = run_context.repository.get_run_detail(run_context.run.id)
    assert [message.role for message in detail.messages] == ["user", "assistant", "tool", "assistant"]


def test_loop_executes_multiple_tool_calls_sequentially(run_context):
    model = ScriptedModelClient([
        AssistantTurn(None, (
            execute("write_file", {"path": "first.txt", "content": "one"}, "c1"),
            execute("write_file", {"path": "second.txt", "content": "two"}, "c2"),
        )),
        AssistantTurn("Created both files."),
    ])
    loop = AgentLoop(model, run_context.registry, run_context.repository, run_context.workspace)

    result = loop.run(run_id=run_context.run.id, session_id=run_context.session.id, prompt="write", prior_messages=[], max_steps=20)

    detail = run_context.repository.get_run_detail(run_context.run.id)
    assert result.status == "completed"
    assert [call.provider_call_id for call in detail.tool_calls] == ["c1", "c2"]
    assert (run_context.workspace.root / "first.txt").read_text() == "one"
    assert (run_context.workspace.root / "second.txt").read_text() == "two"


def test_loop_continues_after_recoverable_tool_failure(run_context):
    model = ScriptedModelClient([
        AssistantTurn(None, (execute("read_file", {"path": "missing.txt"}, "c1"),)),
        AssistantTurn(None, (execute("write_file", {"path": "missing.txt", "content": "fixed"}, "c2"),)),
        AssistantTurn("Corrected the missing file."),
    ])
    loop = AgentLoop(model, run_context.registry, run_context.repository, run_context.workspace)

    result = loop.run(run_id=run_context.run.id, session_id=run_context.session.id, prompt="repair", prior_messages=[], max_steps=20)

    detail = run_context.repository.get_run_detail(run_context.run.id)
    assert result.status == "completed"
    assert detail.tool_calls[0].status == "failed"
    assert detail.tool_calls[1].status == "succeeded"


def test_loop_stops_at_max_steps_and_persists_file_evidence(run_context):
    model = ScriptedModelClient([
        AssistantTurn(None, (execute("read_file", {"path": "result.txt"}, "c1"),)),
        AssistantTurn(None, (execute("read_file", {"path": "result.txt"}, "c2"),)),
    ])
    run_context.workspace.write_file("result.txt", "evidence")
    loop = AgentLoop(model, run_context.registry, run_context.repository, run_context.workspace)

    result = loop.run(run_id=run_context.run.id, session_id=run_context.session.id, prompt="work", prior_messages=[], max_steps=2)

    detail = run_context.repository.get_run_detail(run_context.run.id)
    assert result.status == "max_steps"
    assert detail.file_changes[0].path == "result.txt"


def test_loop_fails_for_blank_terminal_content_and_persists_evidence(run_context):
    run_context.workspace.write_file("result.txt", "evidence")
    loop = AgentLoop(ScriptedModelClient([AssistantTurn("  \n")] ), run_context.registry, run_context.repository, run_context.workspace)

    result = loop.run(run_id=run_context.run.id, session_id=run_context.session.id, prompt="work", prior_messages=[], max_steps=20)

    detail = run_context.repository.get_run_detail(run_context.run.id)
    assert result.status == "failed"
    assert result.error_text == "The model returned an empty final response."
    assert detail.file_changes[0].path == "result.txt"


def test_loop_fails_with_safe_provider_error(run_context):
    loop = AgentLoop(ScriptedModelClient([RuntimeError("secret internal detail")]), run_context.registry, run_context.repository, run_context.workspace)

    result = loop.run(run_id=run_context.run.id, session_id=run_context.session.id, prompt="work", prior_messages=[], max_steps=20)

    assert result.status == "failed"
    assert result.error_text == "The model provider request failed."
    assert "secret" not in run_context.repository.get_run_detail(run_context.run.id).error_text


def test_loop_checks_cancellation_before_each_provider_call(run_context):
    token = CancellationToken()

    class CancellingModel(ScriptedModelClient):
        def complete(self, messages, tools):
            turn = super().complete(messages, tools)
            token.cancel()
            return turn

    model = CancellingModel([AssistantTurn(None, (execute("read_file", {"path": "a.txt"}, "c1"),))])
    loop = AgentLoop(model, run_context.registry, run_context.repository, run_context.workspace)

    result = loop.run(run_id=run_context.run.id, session_id=run_context.session.id, prompt="work", prior_messages=[], max_steps=20, cancellation=token)

    assert result.status == "cancelled"
    assert len(model.calls) == 1


def test_loop_checks_cancellation_before_each_tool_call(run_context, monkeypatch):
    token = CancellationToken()
    calls = []
    run_context.workspace.write_file("evidence.txt", "changed")

    def cancelling_execute(call):
        calls.append(call.id)
        token.cancel()
        return ToolResult(True, {}, None, 1)

    monkeypatch.setattr(run_context.registry, "execute", cancelling_execute)
    model = ScriptedModelClient([AssistantTurn(None, (
        execute("read_file", {"path": "a.txt"}, "c1"),
        execute("read_file", {"path": "b.txt"}, "c2"),
    ))])
    loop = AgentLoop(model, run_context.registry, run_context.repository, run_context.workspace)

    result = loop.run(run_id=run_context.run.id, session_id=run_context.session.id, prompt="work", prior_messages=[], max_steps=20, cancellation=token)

    assert result.status == "cancelled"
    assert calls == ["c1"]
    assert run_context.repository.get_run_detail(run_context.run.id).file_changes[0].path == "evidence.txt"


def test_assistant_turn_persists_exactly_once_and_history_precedes_current_user(run_context):
    model = ScriptedModelClient([AssistantTurn("Complete.")])
    loop = AgentLoop(model, run_context.registry, run_context.repository, run_context.workspace)
    history = [{"role": "user", "content": "old user"}, {"role": "assistant", "content": "old assistant"}]

    loop.run(run_id=run_context.run.id, session_id=run_context.session.id, prompt="current user", prior_messages=history, max_steps=20)

    detail = run_context.repository.get_run_detail(run_context.run.id)
    assert [message.role for message in detail.messages] == ["user", "assistant"]
    assert len([message for message in detail.messages if message.role == "assistant"]) == 1
    messages = model.calls[0]["messages"]
    assert [message["role"] for message in messages] == ["system", "user", "assistant", "user"]
    assert "coding_agent_v1" in messages[0]["content"]
    assert messages[-1]["content"] == "current user"


def test_loop_uses_bounded_user_and_assistant_history_only(run_context):
    model = ScriptedModelClient([AssistantTurn("Complete.")])
    loop = AgentLoop(model, run_context.registry, run_context.repository, run_context.workspace)
    history = [
        {"role": "tool", "content": "omit this"},
        {"role": "user", "content": "x" * 40_000},
        {"role": "assistant", "content": "keep this"},
    ]

    loop.run(run_id=run_context.run.id, session_id=run_context.session.id, prompt="current", prior_messages=history, max_steps=20)

    messages = model.calls[0]["messages"]
    assert messages == [
        messages[0],
        {"role": "assistant", "content": "keep this"},
        {"role": "user", "content": "current"},
    ]


def test_loop_uses_one_valid_bounded_payload_for_oversized_tool_result(run_context, monkeypatch):
    oversized = ToolResult(True, {"content": "x" * 1_048_576}, None, 1)
    monkeypatch.setattr(run_context.registry, "execute", lambda call: oversized)
    model = ScriptedModelClient([
        AssistantTurn(None, (execute("read_file", {"path": "large.txt"}, "c1"),)),
        AssistantTurn("Handled the result."),
    ])
    loop = AgentLoop(model, run_context.registry, run_context.repository, run_context.workspace)

    loop.run(run_id=run_context.run.id, session_id=run_context.session.id, prompt="inspect", prior_messages=[], max_steps=20)

    detail = run_context.repository.get_run_detail(run_context.run.id)
    persisted = detail.tool_calls[0].result_json
    persisted_message = next(message.content for message in detail.messages if message.role == "tool")
    model_content = model.calls[1]["messages"][-1]["content"]
    assert len(persisted) <= 20_000
    assert json.loads(persisted)["meta"]["truncated"] is True
    assert persisted == persisted_message == model_content


def test_loop_preserves_non_oversized_tool_payload_exactly(run_context, monkeypatch):
    result = ToolResult(True, {"content": "small"}, None, 1)
    monkeypatch.setattr(run_context.registry, "execute", lambda call: result)
    model = ScriptedModelClient([
        AssistantTurn(None, (execute("read_file", {"path": "small.txt"}, "c1"),)),
        AssistantTurn("Handled the result."),
    ])
    loop = AgentLoop(model, run_context.registry, run_context.repository, run_context.workspace)

    loop.run(run_id=run_context.run.id, session_id=run_context.session.id, prompt="inspect", prior_messages=[], max_steps=20)

    persisted = run_context.repository.get_run_detail(run_context.run.id).tool_calls[0].result_json
    assert persisted == result.to_json()
    assert model.calls[1]["messages"][-1]["content"] == result.to_json()
