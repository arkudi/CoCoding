import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.agent.types import AssistantTurn
from tests.test_runs import _create_session, _wait_for_terminal


class BlockingModel:
    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()

    def complete(self, messages, tools):
        self.entered.set()
        assert self.release.wait(2)
        return AssistantTurn("Finished.")


def test_websocket_sends_snapshot_then_terminal_event(app_factory, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    model = BlockingModel()
    with app_factory(model) as client:
        session = _create_session(client, workspace)
        created = client.post(
            f"/api/sessions/{session['id']}/runs", json={"prompt": "inspect"}
        ).json()
        assert model.entered.wait(1)

        with client.websocket_connect(f"/api/runs/{created['id']}/events") as socket:
            snapshot = socket.receive_json()
            assert snapshot["type"] == "run.snapshot"
            assert snapshot["data"]["id"] == created["id"]
            model.release.set()
            while True:
                event = socket.receive_json()
                if event["type"] == "run.finished":
                    break

        assert event["data"]["status"] == "completed"


def test_terminal_run_websocket_returns_one_snapshot(app_factory, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with app_factory(type("Model", (), {"complete": lambda self, messages, tools: AssistantTurn("Done.")})()) as client:
        session = _create_session(client, workspace)
        created = client.post(
            f"/api/sessions/{session['id']}/runs", json={"prompt": "inspect"}
        ).json()
        _wait_for_terminal(client, created["id"])
        with client.websocket_connect(f"/api/runs/{created['id']}/events") as socket:
            snapshot = socket.receive_json()
        assert snapshot["type"] == "run.snapshot"
        assert snapshot["data"]["status"] == "completed"


def test_unknown_run_websocket_closes_with_4404(client: TestClient) -> None:
    with pytest.raises(WebSocketDisconnect) as error:
        with client.websocket_connect("/api/runs/missing/events"):
            pass
    assert error.value.code == 4404
