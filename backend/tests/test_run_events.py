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

    def complete(self, messages, tools, on_text_delta=None):
        self.entered.set()
        assert self.release.wait(2)
        assert on_text_delta is not None
        on_text_delta("Hel")
        on_text_delta("lo")
        return AssistantTurn("Hello")


def test_websocket_sends_assistant_lifecycle_then_terminal_event(
    app_factory, tmp_path: Path, monkeypatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    model = BlockingModel()
    with app_factory(model) as client:
        started = threading.Event()
        release_started = threading.Event()
        original_publish = client.app.state.event_hub.publish

        def publish(event):
            if event.type == "assistant.started":
                started.set()
                assert release_started.wait(2)
            original_publish(event)

        monkeypatch.setattr(client.app.state.event_hub, "publish", publish)
        session = _create_session(client, workspace)
        created = client.post(
            f"/api/sessions/{session['id']}/runs", json={"prompt": "inspect"}
        ).json()
        assert started.wait(1)

        with client.websocket_connect(f"/api/runs/{created['id']}/events") as socket:
            snapshot = socket.receive_json()
            assert snapshot["type"] == "run.snapshot"
            assert snapshot["data"]["id"] == created["id"]
            release_started.set()
            assert model.entered.wait(1)
            model.release.set()
            events = []
            while True:
                event = socket.receive_json()
                events.append(event)
                if event["type"] == "run.finished":
                    break

        assert event["data"]["status"] == "completed"
        lifecycle = [event for event in events if event["type"].startswith("assistant.")]
        assert [(event["type"], event["data"]) for event in lifecycle] == [
            ("assistant.started", {}),
            ("assistant.delta", {"delta": "Hel"}),
            ("assistant.delta", {"delta": "lo"}),
            ("assistant.finished", {}),
        ]
        assistant_messages = [
            event
            for event in events
            if event["type"] == "message.created"
            and event["data"]["role"] == "assistant"
        ]
        assert len(assistant_messages) == 1
        assert assistant_messages[0]["data"]["content"] == "Hello"
        assert events.index(assistant_messages[0]) < events.index(lifecycle[-1])


def test_terminal_run_websocket_returns_one_snapshot(app_factory, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with app_factory(
        type(
            "Model",
            (),
            {
                "complete": lambda self, messages, tools, on_text_delta=None: AssistantTurn(
                    "Done."
                )
            },
        )()
    ) as client:
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
