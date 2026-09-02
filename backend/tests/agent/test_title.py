from __future__ import annotations

import threading

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.agent.events import RunEvent
from app.agent.loop import CancellationToken
from app.agent.service import AgentService
from app.agent.title import generate_task_title, needs_generated_title
from app.agent.types import AssistantTurn
from app.db.database import create_schema
from app.db.models import SessionRecord
from tests.agent.fakes import ScriptedModelClient, finish


def test_generated_title_is_normalized_and_bounded() -> None:
    model = ScriptedModelClient([AssistantTurn('  "修复计算器中的加法错误"  ')])

    assert generate_task_title(model, "fix it") == "修复计算器中的加法错误"
    assert needs_generated_title("新任务 · calculator") is True
    assert needs_generated_title("Existing title") is False


def test_first_run_generates_persists_and_emits_session_title(tmp_path) -> None:
    engine = create_engine("sqlite://")
    create_schema(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        session = SessionRecord(
            title="新任务 · calculator", workspace_path=str(tmp_path)
        )
        db.add(session)
        db.commit()
        db.refresh(session)
        session_id = session.id
    model = ScriptedModelClient(
        [AssistantTurn("修复计算器"), finish("No changes needed.")]
    )
    events: list[RunEvent] = []
    service = AgentService(factory, model, execution_lock=threading.Lock())

    created = service.create_run(session_id, "修复计算器", 10)
    result = service.execute_existing(created.id, CancellationToken(), events.append)

    with factory() as db:
        renamed = db.get(SessionRecord, session_id)
        assert renamed is not None
        assert renamed.title == "修复计算器"
    assert result.status == "completed"
    rename_event = next(event for event in events if event.type == "session.renamed")
    assert rename_event.data == {"id": session_id, "title": "修复计算器"}
    engine.dispose()
