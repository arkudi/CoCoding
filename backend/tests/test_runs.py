from datetime import datetime, timedelta
from pathlib import Path
import threading

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.agent.types import AssistantTurn, ToolCall
from app.agent.workspace import WorkspaceService
from app.config import Settings
from app.db.models import RunRecord
from app.db.run_repository import RunRepository
from app.main import create_app
from tests.agent.fakes import ScriptedModelClient, finish


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


def _wait_for_terminal(client: TestClient, run_id: str) -> dict[str, object]:
    client.app.state.run_manager.wait_for_idle(2)
    response = client.get(f"/api/runs/{run_id}")
    assert response.status_code == 200
    return response.json()


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
            finish("Updated a.txt.", changed_files=["a.txt"]),
        ]
    )

    with app_factory(model) as client:
        session = _create_session(client, workspace)
        response = client.post(
            f"/api/sessions/{session['id']}/runs",
            json={"prompt": "  update it  "},
        )

        assert response.status_code == 202
        run = response.json()
        evidence = _wait_for_terminal(client, run["id"])

    assert evidence["prompt"] == "update it"
    assert evidence["status"] == "completed"
    assert evidence["max_steps"] == 50
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
            finish("The first run inspected a.txt."),
            finish("The second run is complete."),
        ]
    )

    with app_factory(model) as client:
        session = _create_session(client, workspace)
        first = client.post(
            f"/api/sessions/{session['id']}/runs",
            json={"prompt": "Inspect a.txt."},
        )
        assert first.status_code == 202
        _wait_for_terminal(client, first.json()["id"])
        second = client.post(
            f"/api/sessions/{session['id']}/runs",
            json={"prompt": "Use the prior result."},
        )
        assert second.status_code == 202
        _wait_for_terminal(client, second.json()["id"])

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


def test_run_request_rejects_blank_prompt_and_user_step_limit(app_factory, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with app_factory(ScriptedModelClient([])) as client:
        session = _create_session(client, workspace)
        blank = client.post(f"/api/sessions/{session['id']}/runs", json={"prompt": "   "})
        user_step_limit = client.post(
            f"/api/sessions/{session['id']}/runs", json={"prompt": "x", "max_steps": 0}
        )
        oversized_prompt = client.post(
            f"/api/sessions/{session['id']}/runs", json={"prompt": "x" * 20_001}
        )

    assert blank.status_code == 422
    assert user_step_limit.status_code == 422
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

    class BlockingModel:
        def __init__(self) -> None:
            self.entered = threading.Event()
            self.release = threading.Event()

        def complete(self, messages, tools, on_text_delta=None):
            self.entered.set()
            assert self.release.wait(2)
            return finish("Finished.")

    model = BlockingModel()
    with app_factory(model) as client:
        session = _create_session(client, workspace)
        first = client.post(
            f"/api/sessions/{session['id']}/runs", json={"prompt": "first"}
        )
        assert first.status_code == 202
        assert model.entered.wait(1)
        response = client.post(
            f"/api/sessions/{session['id']}/runs", json={"prompt": "second"}
        )

        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "RUN_ALREADY_ACTIVE"
        assert _run_count(client) == 1
        model.release.set()
        _wait_for_terminal(client, first.json()["id"])


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
    model = ScriptedModelClient([finish("Finished.")])

    with app_factory(model) as client:
        session = _create_session(client, workspace)
        response = client.post(f"/api/sessions/{session['id']}/runs", json={"prompt": "update it"})
        terminal = _wait_for_terminal(client, response.json()["id"])

    assert response.status_code == 202
    assert terminal["status"] == "completed"
    assert model.calls


def test_provider_failures_are_safe_and_do_not_expose_exception_text(app_factory, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    model = ScriptedModelClient([RuntimeError("provider secret must not be exposed")])

    with app_factory(model) as client:
        session = _create_session(client, workspace)
        response = client.post(f"/api/sessions/{session['id']}/runs", json={"prompt": "update it"})
        terminal = _wait_for_terminal(client, response.json()["id"])

    assert response.status_code == 202
    assert terminal["status"] == "failed"
    assert terminal["error_text"] == "The model provider request failed."
    assert "provider secret" not in str(terminal)


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

    with app_factory(ScriptedModelClient([finish("Unused.")])) as client:
        session = _create_session(client, workspace)
        response = client.post(
            f"/api/sessions/{session['id']}/runs",
            json={"prompt": "Update it."},
        )
        terminal = _wait_for_terminal(client, response.json()["id"])

    assert response.status_code == 202
    assert terminal["status"] == "failed"
    assert terminal["error_text"] == "The run failed because of an internal error."
    assert "raw persistence detail" not in str(terminal)


def test_file_change_generation_failure_still_marks_created_run_failed(
    app_factory, tmp_path: Path, monkeypatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    def fail_changes(self):
        raise RuntimeError("raw evidence detail")

    monkeypatch.setattr(WorkspaceService, "changes", fail_changes)

    with app_factory(ScriptedModelClient([finish("Finished.")])) as client:
        session = _create_session(client, workspace)
        response = client.post(
            f"/api/sessions/{session['id']}/runs",
            json={"prompt": "Update it."},
        )
        terminal = _wait_for_terminal(client, response.json()["id"])

    assert response.status_code == 202
    assert terminal["status"] == "failed"
    assert terminal["error_text"] == "The run failed because of an internal error."
    assert "raw evidence detail" not in str(terminal)


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

    with app_factory(ScriptedModelClient([finish("Finished.")])) as client:
        session = _create_session(client, workspace)
        response = client.post(
            f"/api/sessions/{session['id']}/runs",
            json={"prompt": "Update it."},
        )
        terminal = _wait_for_terminal(client, response.json()["id"])

    assert response.status_code == 202
    assert terminal["status"] == "failed"
    assert terminal["error_text"] == "The run failed because of an internal error."
    assert "raw detail-load failure" not in str(terminal)


def test_run_history_and_terminal_cancel_are_durable(app_factory, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with app_factory(ScriptedModelClient([finish("Finished.")])) as client:
        session = _create_session(client, workspace)
        created = client.post(
            f"/api/sessions/{session['id']}/runs", json={"prompt": "inspect"}
        )
        terminal = _wait_for_terminal(client, created.json()["id"])

        history = client.get(f"/api/sessions/{session['id']}/runs")
        cancelled = client.post(f"/api/runs/{terminal['id']}/cancel")

    assert history.status_code == 200
    assert [item["id"] for item in history.json()] == [terminal["id"]]
    assert cancelled.status_code == 200
    assert cancelled.json() == {
        "run_id": terminal["id"],
        "status": "completed",
        "requested": False,
    }


def test_cancel_endpoint_requests_cooperative_cancellation(app_factory, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    class BlockingToolModel:
        def __init__(self) -> None:
            self.entered = threading.Event()
            self.release = threading.Event()

        def complete(self, messages, tools, on_text_delta=None):
            self.entered.set()
            assert self.release.wait(2)
            return AssistantTurn(
                None,
                (ToolCall("read-1", "read_file", '{"path":"missing.txt"}'),),
            )

    model = BlockingToolModel()
    with app_factory(model) as client:
        session = _create_session(client, workspace)
        created = client.post(
            f"/api/sessions/{session['id']}/runs", json={"prompt": "inspect"}
        )
        assert model.entered.wait(1)
        first = client.post(f"/api/runs/{created.json()['id']}/cancel")
        second = client.post(f"/api/runs/{created.json()['id']}/cancel")
        model.release.set()
        terminal = _wait_for_terminal(client, created.json()["id"])

    assert first.json()["requested"] is True
    assert second.json()["requested"] is True
    assert terminal["status"] == "cancelled"
