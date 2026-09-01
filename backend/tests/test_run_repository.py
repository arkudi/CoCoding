from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.agent.workspace import FileChangeEvidence
from app.db.database import create_schema
from app.db.models import SessionRecord
from app.db.run_repository import RunRepository


@pytest.fixture
def db() -> Iterator[Session]:
    engine = create_engine("sqlite://")
    create_schema(engine)
    database = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        yield database
    finally:
        database.close()
        engine.dispose()


@pytest.fixture
def workspace_session(db: Session) -> SessionRecord:
    record = SessionRecord(title="Workspace", workspace_path="C:/workspace")
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def test_repository_persists_complete_run_evidence(
    db: Session, workspace_session: SessionRecord
) -> None:
    repo = RunRepository(db)
    run = repo.create_run(
        session_id=workspace_session.id,
        prompt="change it",
        model="fake",
        prompt_version="coding_agent_v1",
        max_steps=20,
    )
    user = repo.add_message(run.id, workspace_session.id, "user", "change it")
    assistant = repo.add_message(run.id, workspace_session.id, "assistant", "done")
    call = repo.start_tool_call(run.id, "provider-1", "read_file", '{"path":"a.py"}')
    repo.start_tool_call(run.id, "provider-2", "get_diff", "{}")
    repo.finish_tool_call(call.id, "succeeded", '{"ok":true}', 7)
    repo.replace_file_changes(
        run.id,
        (
            FileChangeEvidence(
                path="a.py",
                operation="modified",
                before_hash="before",
                after_hash="after",
                unified_diff="diff",
            ),
        ),
    )
    repo.finish_run(run.id, "completed", step_count=1, final_response="done")

    detail = repo.get_run_detail(run.id)

    assert detail is not None
    assert detail.status == "completed"
    assert detail.step_count == 1
    assert detail.messages == (
        detail.messages[0],
        detail.messages[1],
    )
    assert [message.id for message in detail.messages] == [user.id, assistant.id]
    assert [call.provider_call_id for call in detail.tool_calls] == ["provider-1", "provider-2"]
    assert detail.tool_calls[0].result_json == '{"ok":true}'
    assert detail.file_changes[0].path == "a.py"
    with pytest.raises(AttributeError):
        detail.status = "failed"  # type: ignore[misc]


def test_each_mutation_helper_leaves_no_open_database_transaction(
    db: Session, workspace_session: SessionRecord
) -> None:
    """A post-commit refresh must not hold a transaction across external work."""
    repo = RunRepository(db)

    run = repo.create_run(
        session_id=workspace_session.id,
        prompt="change it",
        model="fake",
        prompt_version="coding_agent_v1",
        max_steps=20,
    )
    assert db.in_transaction() is False

    repo.add_message(run.id, workspace_session.id, "user", "change it")
    assert db.in_transaction() is False

    call = repo.start_tool_call(run.id, "provider-1", "read_file", "{}")
    assert db.in_transaction() is False

    repo.finish_tool_call(call.id, "succeeded", '{"ok":true}', 1)
    assert db.in_transaction() is False

    repo.replace_file_changes(run.id, ())
    assert db.in_transaction() is False

    repo.finish_run(run.id, "completed", step_count=1, final_response="done")
    assert db.in_transaction() is False


@pytest.mark.parametrize(
    "status", ["completed", "failed", "max_steps", "cancelled", "interrupted"]
)
def test_finish_run_persists_each_terminal_status(
    db: Session, workspace_session: SessionRecord, status: str
) -> None:
    repo = RunRepository(db)
    run = repo.create_run(
        session_id=workspace_session.id,
        prompt="prompt",
        model="fake",
        prompt_version="v1",
        max_steps=1,
    )

    finished = repo.finish_run(run.id, status, step_count=1, error_text="safe error")

    assert finished.status == status
    assert finished.error_text == "safe error"
    assert repo.get_run_detail(run.id).status == status  # type: ignore[union-attr]


def test_tool_result_persistence_is_capped_at_twenty_thousand_characters(
    db: Session, workspace_session: SessionRecord
) -> None:
    repo = RunRepository(db)
    run = repo.create_run(
        session_id=workspace_session.id,
        prompt="prompt",
        model="fake",
        prompt_version="v1",
        max_steps=1,
    )
    call = repo.start_tool_call(run.id, "call", "read_file", "{}")

    repo.finish_tool_call(call.id, "succeeded", "x" * 20_001, 1)

    detail = repo.get_run_detail(run.id)
    assert detail is not None
    assert detail.tool_calls[0].result_json == "x" * 20_000


def test_tool_message_content_is_capped_at_twenty_thousand_characters(
    db: Session, workspace_session: SessionRecord
) -> None:
    repo = RunRepository(db)
    run = repo.create_run(
        session_id=workspace_session.id,
        prompt="prompt",
        model="fake",
        prompt_version="v1",
        max_steps=1,
    )

    message = repo.add_message(run.id, workspace_session.id, "tool", "x" * 20_001)

    assert message.content == "x" * 20_000


@pytest.mark.parametrize("role", ["user", "assistant"])
def test_non_tool_message_content_is_not_capped(
    db: Session, workspace_session: SessionRecord, role: str
) -> None:
    repo = RunRepository(db)
    run = repo.create_run(
        session_id=workspace_session.id,
        prompt="prompt",
        model="fake",
        prompt_version="v1",
        max_steps=1,
    )

    message = repo.add_message(run.id, workspace_session.id, role, "x" * 20_001)

    assert message.content == "x" * 20_001


def test_completed_history_selects_newest_messages_within_budget_and_excludes_tool_messages(
    db: Session, workspace_session: SessionRecord
) -> None:
    repo = RunRepository(db)
    old_run = repo.create_run(
        session_id=workspace_session.id,
        prompt="old",
        model="fake",
        prompt_version="v1",
        max_steps=1,
    )
    repo.add_message(old_run.id, workspace_session.id, "user", "old user")
    repo.add_message(old_run.id, workspace_session.id, "assistant", "x" * 20_000)
    repo.finish_run(old_run.id, "completed", step_count=1)
    new_run = repo.create_run(
        session_id=workspace_session.id,
        prompt="new",
        model="fake",
        prompt_version="v1",
        max_steps=1,
    )
    repo.add_message(new_run.id, workspace_session.id, "user", "new user")
    repo.add_message(new_run.id, workspace_session.id, "tool", "tool result")
    repo.add_message(new_run.id, workspace_session.id, "assistant", "y" * 20_000)
    repo.finish_run(new_run.id, "completed", step_count=1)

    history = repo.completed_history(workspace_session.id)

    assert history == [
        {"role": "user", "content": "new user"},
        {"role": "assistant", "content": "y" * 20_000},
    ]


def test_completed_history_excludes_whitespace_only_messages(
    db: Session, workspace_session: SessionRecord
) -> None:
    repo = RunRepository(db)
    run = repo.create_run(
        session_id=workspace_session.id,
        prompt="prompt",
        model="fake",
        prompt_version="v1",
        max_steps=1,
    )
    repo.add_message(run.id, workspace_session.id, "user", "prompt")
    repo.add_message(run.id, workspace_session.id, "assistant", "\n\t")
    repo.add_message(run.id, workspace_session.id, "assistant", "terminal")
    repo.finish_run(run.id, "completed", step_count=1)

    assert repo.completed_history(workspace_session.id) == [
        {"role": "user", "content": "prompt"},
        {"role": "assistant", "content": "terminal"},
    ]


def test_add_message_rejects_a_session_that_does_not_own_the_run(
    db: Session, workspace_session: SessionRecord
) -> None:
    other_session = SessionRecord(title="Other", workspace_path="C:/other")
    db.add(other_session)
    db.commit()
    repo = RunRepository(db)
    run = repo.create_run(
        session_id=workspace_session.id,
        prompt="prompt",
        model="fake",
        prompt_version="v1",
        max_steps=1,
    )

    with pytest.raises(ValueError, match="does not belong"):
        repo.add_message(run.id, other_session.id, "user", "prompt")


def test_replace_file_changes_replaces_only_one_runs_existing_evidence(
    db: Session, workspace_session: SessionRecord
) -> None:
    repo = RunRepository(db)
    first_run = repo.create_run(
        session_id=workspace_session.id,
        prompt="first",
        model="fake",
        prompt_version="v1",
        max_steps=1,
    )
    second_run = repo.create_run(
        session_id=workspace_session.id,
        prompt="second",
        model="fake",
        prompt_version="v1",
        max_steps=1,
    )
    first = FileChangeEvidence("first.py", "created", None, "first", "first diff")
    second = FileChangeEvidence("second.py", "created", None, "second", "second diff")

    repo.replace_file_changes(first_run.id, (first,))
    repo.replace_file_changes(second_run.id, (second,))
    repo.replace_file_changes(first_run.id, (second, first))

    first_detail = repo.get_run_detail(first_run.id)
    second_detail = repo.get_run_detail(second_run.id)
    assert first_detail is not None
    assert second_detail is not None
    assert [change.path for change in first_detail.file_changes] == ["second.py", "first.py"]
    assert [change.path for change in second_detail.file_changes] == ["second.py"]


def test_interrupt_running_runs_marks_all_running_rows_with_a_safe_error(
    db: Session, workspace_session: SessionRecord
) -> None:
    repo = RunRepository(db)
    first = repo.create_run(
        session_id=workspace_session.id,
        prompt="first",
        model="fake",
        prompt_version="v1",
        max_steps=1,
    )
    second = repo.create_run(
        session_id=workspace_session.id,
        prompt="second",
        model="fake",
        prompt_version="v1",
        max_steps=1,
    )
    completed = repo.create_run(
        session_id=workspace_session.id,
        prompt="done",
        model="fake",
        prompt_version="v1",
        max_steps=1,
    )
    repo.finish_run(completed.id, "completed", step_count=1)

    interrupted_count = repo.interrupt_running_runs()

    assert interrupted_count == 2
    assert repo.get_run_detail(first.id).status == "interrupted"  # type: ignore[union-attr]
    assert repo.get_run_detail(second.id).error_text == "Run interrupted during startup recovery."  # type: ignore[union-attr]
    assert repo.get_run_detail(completed.id).status == "completed"  # type: ignore[union-attr]
