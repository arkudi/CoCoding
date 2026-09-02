from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db.models import (
    AgentExecutionRecord,
    AgentTaskRecord,
    AgentToolCallRecord,
    FileChangeRecord,
    MessageRecord,
    RunRecord,
    SessionRecord,
    ToolCallRecord,
)
from app.db.run_repository import RunRepository


def test_create_and_list_session(client: TestClient, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    created = client.post(
        "/api/sessions",
        json={"title": "  Fix calculator  ", "workspace_path": str(workspace)},
    )

    assert created.status_code == 201
    payload = created.json()
    assert payload["title"] == "Fix calculator"
    assert payload["workspace_path"] == str(workspace.resolve())
    assert payload["status"] == "idle"
    assert str(UUID(payload["id"])) == payload["id"]
    for field in ("created_at", "updated_at"):
        timestamp = datetime.fromisoformat(payload[field].replace("Z", "+00:00"))
        assert timestamp.utcoffset() == timedelta(0)

    listed = client.get("/api/sessions")
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [payload["id"]]


def test_create_session_rejects_missing_workspace(
    client: TestClient, tmp_path: Path
) -> None:
    response = client.post(
        "/api/sessions",
        json={"title": "Invalid", "workspace_path": str(tmp_path / "missing")},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "Workspace directory does not exist"


def test_create_session_rejects_whitespace_only_title(
    client: TestClient, tmp_path: Path
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    response = client.post(
        "/api/sessions",
        json={"title": "   ", "workspace_path": str(workspace)},
    )

    assert response.status_code == 422


def test_create_session_generates_temporary_title_from_workspace(
    client, tmp_path: Path
) -> None:
    workspace = tmp_path / "calculator"
    workspace.mkdir()

    response = client.post(
        "/api/sessions", json={"workspace_path": str(workspace)}
    )

    assert response.status_code == 201
    assert response.json()["title"] == "新任务 · calculator"


def test_select_workspace_uses_native_picker(app_factory, tmp_path: Path) -> None:
    from tests.agent.fakes import ScriptedModelClient

    with app_factory(
        ScriptedModelClient([]), directory_picker=lambda _initial: str(tmp_path)
    ) as client:
        response = client.post("/api/sessions/select-workspace")

    assert response.status_code == 200
    assert response.json() == {"path": str(tmp_path)}


def test_select_workspace_returns_null_when_user_cancels(app_factory) -> None:
    from tests.agent.fakes import ScriptedModelClient

    with app_factory(
        ScriptedModelClient([]), directory_picker=lambda _initial: None
    ) as client:
        response = client.post("/api/sessions/select-workspace")

    assert response.status_code == 200
    assert response.json() == {"path": None}


def test_select_workspace_forwards_current_workspace_as_initial_directory(
    app_factory, tmp_path: Path
) -> None:
    from tests.agent.fakes import ScriptedModelClient

    received: list[str | None] = []
    with app_factory(
        ScriptedModelClient([]),
        directory_picker=lambda initial: received.append(initial) or None,
    ) as client:
        response = client.post(
            "/api/sessions/select-workspace",
            json={"initial_path": str(tmp_path)},
        )

    assert response.status_code == 200
    assert received == [str(tmp_path)]


def test_delete_session_removes_history_but_preserves_workspace(
    client: TestClient, tmp_path: Path
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = workspace / "keep.py"
    source.write_text("print('keep')", encoding="utf-8")
    created = client.post(
        "/api/sessions", json={"title": "Disposable", "workspace_path": str(workspace)}
    ).json()

    with client.app.state.session_factory() as db:  # type: ignore[attr-defined]
        repository = RunRepository(db)
        run = repository.create_run(
            session_id=created["id"], prompt="inspect", model="fake",
            prompt_version="test", max_steps=5,
        )
        execution = repository.start_agent_execution(run.id, role="manager", task="inspect")
        task = repository.create_agent_task(
            run.id, role="explorer", description="inspect", expected_output="findings"
        )
        repository.start_agent_task(task.id, execution.id)
        tool_call = repository.start_tool_call(
            run.id, "call-1", "read_file", "{}", agent_execution_id=execution.id
        )
        repository.add_message(
            run.id, created["id"], "tool", "{}", tool_call_id=tool_call.provider_call_id
        )
        db.add(FileChangeRecord(
            run_id=run.id, path="keep.py", operation="modified",
            before_hash="a" * 64, after_hash="b" * 64, unified_diff="diff",
        ))
        db.commit()
        repository.finish_run(run.id, "completed", step_count=1, final_response="done")

    response = client.delete(f"/api/sessions/{created['id']}")

    assert response.status_code == 204
    assert response.content == b""
    assert source.read_text(encoding="utf-8") == "print('keep')"
    assert client.get("/api/sessions").json() == []
    with client.app.state.session_factory() as db:  # type: ignore[attr-defined]
        for model in (
            SessionRecord, RunRecord, AgentExecutionRecord, AgentTaskRecord,
            MessageRecord, ToolCallRecord, AgentToolCallRecord, FileChangeRecord,
        ):
            assert db.scalar(select(func.count()).select_from(model)) == 0


def test_delete_session_rejects_running_task(client: TestClient, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    created = client.post(
        "/api/sessions", json={"title": "Running", "workspace_path": str(workspace)}
    ).json()
    with client.app.state.session_factory() as db:  # type: ignore[attr-defined]
        RunRepository(db).create_run(
            session_id=created["id"], prompt="work", model="fake",
            prompt_version="test", max_steps=5,
        )

    response = client.delete(f"/api/sessions/{created['id']}")

    assert response.status_code == 409
    assert response.json()["detail"] == "正在执行的任务不能删除，请先取消任务"
    assert client.get("/api/sessions").json()[0]["id"] == created["id"]


def test_delete_missing_session_returns_not_found(client: TestClient) -> None:
    response = client.delete("/api/sessions/missing")

    assert response.status_code == 404
    assert response.json()["detail"] == "任务不存在"
