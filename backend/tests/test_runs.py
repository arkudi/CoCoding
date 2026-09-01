from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.agent.types import AssistantTurn, ToolCall
from app.agent.workspace import WorkspaceService
from app.config import Settings
from app.db.models import RunRecord
from app.db.run_repository import RunRepository
from app.main import create_app
from tests.agent.fakes import ScriptedModelClient


def _create_session(client: TestClient, workspace: Path) -> dict[str, object]:
    response = client.post(
        "/api/sessions", json={"title": "Edit", "workspace_path": str(workspace)}
    )
    assert response.status_code == 201
    return response.json()


def _run_count(client: TestClient) -> int:
    db = client.app.state.session_factory()
    try:
        return int(db.scalar(select(func.count()).select_from(RunRecord)) or 0)
    finally:
        db.close()


def test_submit_run_and_reload_complete_evidence(app_factory, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "a.txt").write_text("old\n", encoding="utf-8")
    model = ScriptedModelClient(
        [
            AssistantTurn(
                None,
                (ToolCall("w1", "write_file", '{"path":"a.txt","content":"new\\n"}'),),
            ),
            AssistantTurn("Updated a.txt."),
        ]
    )

    with app_factory(model) as client:
        session = _create_session(client, workspace)
        response = client.post(
            f"/api/sessions/{session['id']}/runs",
            json={"prompt": "  update it  ", "max_steps": 20},
        )

        assert response.status_code == 201
        run = response.json()
        reloaded = client.get(f"/api/runs/{run['id']}")

    assert reloaded.status_code == 200
    evidence = reloaded.json()
    assert evidence["prompt"] == "update it"
    assert evidence["status"] == "completed"
    assert evidence["final_response"] == "Updated a.txt."
    assert evidence["step_count"] == 2
    assert evidence["messages"][0]["role"] == "user"
    assert evidence["tool_calls"][0]["provider_call_id"] == "w1"
    assert len(evidence["file_changes"]) == 1
    assert evidence["file_changes"][0]["relative_path"] == "a.txt"
    assert evidence["file_changes"][0]["operation"] == "modified"
    for field in ("created_at", "updated_at", "finished_at"):
        timestamp = datetime.fromisoformat(evidence[field].replace("Z", "+00:00"))
        assert timestamp.utcoffset() == timedelta(0)
    assert (workspace / "a.txt").read_text(encoding="utf-8") == "new\n"


def test_second_run_replays_only_completed_user_and_terminal_assistant_messages(
    app_factory, tmp_path: Path
) -> None:
    """Replaying an intermediate tool-request turn would orphan its tool calls."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "a.txt").write_text("hello", encoding="utf-8")
    model = ScriptedModelClient(
        [
            AssistantTurn(
                None,
                (ToolCall("read-1", "read_file", '{"path":"a.txt"}'),),
            ),
            AssistantTurn("The first run inspected a.txt."),
            AssistantTurn("The second run is complete."),
        ]
    )

    with app_factory(model) as client:
        session = _create_session(client, workspace)
        first = client.post(
            f"/api/sessions/{session['id']}/runs",
            json={"prompt": "Inspect a.txt."},
        )
        second = client.post(
            f"/api/sessions/{session['id']}/runs",
            json={"prompt": "Use the prior result."},
        )

    assert first.status_code == 201
    assert second.status_code == 201
    assert model.calls[2]["messages"][1:] == [
        {"role": "user", "content": "Inspect a.txt."},
        {"role": "assistant", "content": "The first run inspected a.txt."},
        {"role": "user", "content": "Use the prior result."},
    ]


def test_run_endpoint_returns_404_for_missing_session_and_run(app_factory, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    model = ScriptedModelClient([])

    with app_factory(model) as client:
        missing_session = client.post(
            "/api/sessions/missing/runs", json={"prompt": "update it"}
        )
        missing_run = client.get("/api/runs/missing")

    assert missing_session.status_code == 404
    assert missing_session.json()["detail"] == "Session not found"
    assert missing_run.status_code == 404
    assert missing_run.json()["detail"] == "Run not found"


def test_run_request_rejects_blank_prompt_and_step_count_outside_range(app_factory, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with app_factory(ScriptedModelClient([])) as client:
        session = _create_session(client, workspace)
        blank = client.post(f"/api/sessions/{session['id']}/runs", json={"prompt": "   "})
        too_few = client.post(
            f"/api/sessions/{session['id']}/runs", json={"prompt": "x", "max_steps": 0}
        )
        too_many = client.post(
            f"/api/sessions/{session['id']}/runs", json={"prompt": "x", "max_steps": 51}
        )
        oversized_prompt = client.post(
            f"/api/sessions/{session['id']}/runs", json={"prompt": "x" * 20_001}
        )

    assert blank.status_code == 422
    assert too_few.status_code == 422
    assert too_many.status_code == 422
    assert oversized_prompt.status_code == 422


def test_missing_production_key_returns_503_before_run_creation(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'missing-key.db'}",
        frontend_dist=tmp_path / "missing-dist",
        deepseek_api_key="   ",
    )

    with TestClient(create_app(settings)) as client:
        session = _create_session(client, workspace)
        response = client.post(f"/api/sessions/{session['id']}/runs", json={"prompt": "update it"})

        assert response.status_code == 503
        assert _run_count(client) == 0


def test_deleted_workspace_returns_stable_conflict_without_run_creation(app_factory, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with app_factory(ScriptedModelClient([])) as client:
        session = _create_session(client, workspace)
        workspace.rmdir()
        response = client.post(f"/api/sessions/{session['id']}/runs", json={"prompt": "update it"})

        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "WORKSPACE_UNAVAILABLE"
        assert _run_count(client) == 0


def test_active_execution_lock_returns_stable_conflict_without_run_creation(app_factory, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with app_factory(ScriptedModelClient([])) as client:
        session = _create_session(client, workspace)
        lock = client.app.state.execution_lock
        assert lock.acquire(blocking=False)
        try:
            response = client.post(f"/api/sessions/{session['id']}/runs", json={"prompt": "update it"})
        finally:
            lock.release()

        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "RUN_ALREADY_ACTIVE"
        assert _run_count(client) == 0


def test_history_load_failure_creates_no_run(app_factory, tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    class HistoryLoadError(Exception):
        pass

    def fail_history_load(self, session_id: str):
        raise HistoryLoadError("safe test exception")

    monkeypatch.setattr(RunRepository, "completed_history", fail_history_load)

    with app_factory(ScriptedModelClient([])) as client:
        session = _create_session(client, workspace)
        with pytest.raises(HistoryLoadError, match="safe test exception"):
            client.post(f"/api/sessions/{session['id']}/runs", json={"prompt": "update it"})

        assert _run_count(client) == 0


def test_injected_model_runs_without_production_key(app_factory, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    model = ScriptedModelClient([AssistantTurn("Finished.")])

    with app_factory(model) as client:
        session = _create_session(client, workspace)
        response = client.post(f"/api/sessions/{session['id']}/runs", json={"prompt": "update it"})

    assert response.status_code == 201
    assert response.json()["status"] == "completed"
    assert model.calls


def test_provider_failures_are_safe_and_do_not_expose_exception_text(app_factory, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    model = ScriptedModelClient([RuntimeError("provider secret must not be exposed")])

    with app_factory(model) as client:
        session = _create_session(client, workspace)
        response = client.post(f"/api/sessions/{session['id']}/runs", json={"prompt": "update it"})

    assert response.status_code == 201
    assert response.json()["status"] == "failed"
    assert response.json()["error_text"] == "The model provider request failed."
    assert "provider secret" not in response.text


def test_message_persistence_failure_marks_created_run_failed_with_safe_error(
    app_factory, tmp_path: Path, monkeypatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    original_add_message = RunRepository.add_message
    failed_once = False

    def fail_first_message(self, *args, **kwargs):
        nonlocal failed_once
        if not failed_once:
            failed_once = True
            raise RuntimeError("raw persistence detail")
        return original_add_message(self, *args, **kwargs)

    monkeypatch.setattr(RunRepository, "add_message", fail_first_message)

    with app_factory(ScriptedModelClient([AssistantTurn("Unused.")])) as client:
        session = _create_session(client, workspace)
        response = client.post(
            f"/api/sessions/{session['id']}/runs",
            json={"prompt": "Update it."},
        )
        reloaded = client.get(f"/api/runs/{response.json()['id']}")

    assert response.status_code == 201
    assert response.json()["status"] == "failed"
    assert response.json()["error_text"] == "The run failed because of an internal error."
    assert reloaded.json()["error_text"] == response.json()["error_text"]
    assert "raw persistence detail" not in response.text


def test_file_change_generation_failure_still_marks_created_run_failed(
    app_factory, tmp_path: Path, monkeypatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    def fail_changes(self):
        raise RuntimeError("raw evidence detail")

    monkeypatch.setattr(WorkspaceService, "changes", fail_changes)

    with app_factory(ScriptedModelClient([AssistantTurn("Finished.")])) as client:
        session = _create_session(client, workspace)
        response = client.post(
            f"/api/sessions/{session['id']}/runs",
            json={"prompt": "Update it."},
        )
        reloaded = client.get(f"/api/runs/{response.json()['id']}")

    assert response.status_code == 201
    assert response.json()["status"] == "failed"
    assert response.json()["error_text"] == "The run failed because of an internal error."
    assert reloaded.json()["error_text"] == response.json()["error_text"]
    assert "raw evidence detail" not in response.text


def test_post_loop_repository_failure_is_terminalized_by_service_boundary(
    app_factory, tmp_path: Path, monkeypatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    original_get_detail = RunRepository.get_run_detail
    detail_load_count = 0

    def fail_first_detail_load(self, run_id):
        nonlocal detail_load_count
        detail_load_count += 1
        if detail_load_count == 3:
            raise RuntimeError("raw detail-load failure")
        return original_get_detail(self, run_id)

    monkeypatch.setattr(RunRepository, "get_run_detail", fail_first_detail_load)

    with app_factory(ScriptedModelClient([AssistantTurn("Finished.")])) as client:
        session = _create_session(client, workspace)
        response = client.post(
            f"/api/sessions/{session['id']}/runs",
            json={"prompt": "Update it."},
        )

    assert response.status_code == 201
    assert response.json()["status"] == "failed"
    assert response.json()["error_text"] == "The run failed because of an internal error."
    assert "raw detail-load failure" not in response.text
