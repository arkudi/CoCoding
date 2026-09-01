from __future__ import annotations

import threading
from pathlib import Path

import pytest

from app.agent.events import RunEventHub
from app.agent.run_manager import AgentBusyError, RunManager
from app.agent.types import AssistantTurn, ToolCall
from app.db.database import build_engine, build_session_factory, create_schema
from app.db.models import SessionRecord
from app.db.run_repository import RunRepository


class BlockingModel:
    def __init__(self, turn: AssistantTurn) -> None:
        self.turn = turn
        self.entered = threading.Event()
        self.release = threading.Event()

    def complete(self, messages, tools, on_text_delta=None):
        self.entered.set()
        if not self.release.wait(2):
            raise RuntimeError("test did not release model")
        return self.turn


@pytest.fixture
def manager_context(tmp_path: Path):
    engine = build_engine(f"sqlite:///{tmp_path / 'manager.db'}")
    create_schema(engine)
    session_factory = build_session_factory(engine)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with session_factory() as db:
        session = SessionRecord(title="Workspace", workspace_path=str(workspace))
        db.add(session)
        db.commit()
        db.refresh(session)
        session_id = session.id
    manager = RunManager(session_factory, RunEventHub())
    try:
        yield manager, session_factory, session_id
    finally:
        manager.shutdown(wait=True)
        engine.dispose()


def test_start_returns_before_background_model_completes(manager_context) -> None:
    manager, session_factory, session_id = manager_context
    model = BlockingModel(AssistantTurn("Finished."))

    detail = manager.start(session_id, "inspect", 20, model)

    assert detail.status == "running"
    assert model.entered.wait(1)
    assert manager.active_run_id == detail.id
    model.release.set()
    manager.wait_for_idle(2)
    with session_factory() as db:
        assert RunRepository(db).get_run_detail(detail.id).status == "completed"  # type: ignore[union-attr]


def test_second_start_is_rejected_without_creating_run(manager_context) -> None:
    manager, session_factory, session_id = manager_context
    model = BlockingModel(AssistantTurn("Finished."))
    first = manager.start(session_id, "first", 20, model)
    assert model.entered.wait(1)

    with pytest.raises(AgentBusyError):
        manager.start(session_id, "second", 20, model)

    with session_factory() as db:
        assert [run.id for run in RunRepository(db).list_runs(session_id)] == [first.id]
    model.release.set()
    manager.wait_for_idle(2)


def test_cancel_is_cooperative_and_idempotent(manager_context) -> None:
    manager, session_factory, session_id = manager_context
    model = BlockingModel(
        AssistantTurn(
            None,
            (ToolCall("read-1", "read_file", '{"path":"missing.txt"}'),),
        )
    )
    run = manager.start(session_id, "work", 20, model)
    assert model.entered.wait(1)

    assert manager.cancel(run.id).requested is True
    assert manager.cancel(run.id).requested is True
    model.release.set()
    manager.wait_for_idle(2)

    with session_factory() as db:
        assert RunRepository(db).get_run_detail(run.id).status == "cancelled"  # type: ignore[union-attr]
