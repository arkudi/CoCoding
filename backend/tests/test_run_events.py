import asyncio
import threading
from dataclasses import asdict
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.agent.events import RunEvent, RunEventHub
from app.agent.types import AssistantTurn
from app.api.runs import run_events
from app.db.run_repository import RunRepository
from tests.agent.fakes import finish
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
        return finish("Hello")


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


def test_websocket_receives_terminal_event_after_assistant_persistence_failure(
    app_factory, tmp_path: Path, monkeypatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    model = BlockingModel()
    original_add_message = RunRepository.add_message
    add_message_count = 0

    def fail_assistant_message(self, *args, **kwargs):
        nonlocal add_message_count
        add_message_count += 1
        if add_message_count == 2:
            raise RuntimeError("raw assistant persistence detail")
        return original_add_message(self, *args, **kwargs)

    monkeypatch.setattr(RunRepository, "add_message", fail_assistant_message)

    with app_factory(model) as client:
        started = threading.Event()
        release_started = threading.Event()
        original_publish = client.app.state.event_hub.publish
        terminal_persisted_statuses = []

        def publish(event):
            if event.type == "assistant.started":
                started.set()
                assert release_started.wait(2)
            if event.type == "run.finished":
                with client.app.state.session_factory() as db:
                    detail = RunRepository(db).get_run_detail(event.run_id)
                terminal_persisted_statuses.append(detail.status if detail else None)
            original_publish(event)

        monkeypatch.setattr(client.app.state.event_hub, "publish", publish)
        session = _create_session(client, workspace)
        created = client.post(
            f"/api/sessions/{session['id']}/runs", json={"prompt": "inspect"}
        ).json()
        assert started.wait(1)

        received = []
        read_errors = []
        socket_ready = threading.Event()
        reader_done = threading.Event()
        sockets = []

        def read_events() -> None:
            try:
                with client.websocket_connect(
                    f"/api/runs/{created['id']}/events"
                ) as socket:
                    sockets.append(socket)
                    snapshot = socket.receive_json()
                    assert snapshot["type"] == "run.snapshot"
                    socket_ready.set()
                    while True:
                        event = socket.receive_json()
                        received.append(event)
                        if event["type"] == "run.finished":
                            break
            except BaseException as error:
                read_errors.append(error)
            finally:
                reader_done.set()

        reader = threading.Thread(target=read_events, daemon=True)
        reader.start()
        assert socket_ready.wait(1)
        release_started.set()
        assert model.entered.wait(1)
        model.release.set()
        try:
            assert reader_done.wait(2), "WebSocket did not receive run.finished"
        finally:
            if not reader_done.is_set():
                sockets[0].exit_stack.close()
            reader.join(2)

        assert read_errors == []
        terminal = received[-1]
        assert terminal["type"] == "run.finished"
        assert terminal["data"]["status"] == "failed"
        assert terminal_persisted_statuses == ["failed"]
        durable = client.get(f"/api/runs/{created['id']}").json()
        assert durable["status"] == "failed"
        assert durable["error_text"] == "The run failed because of an internal error."


def test_websocket_exits_when_terminal_event_overflows_full_queue(
    app_factory, tmp_path: Path
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with app_factory(object()) as client:
        session = _create_session(client, workspace)
        with client.app.state.session_factory() as db:
            run = RunRepository(db).create_run(
                session_id=session["id"],
                prompt="inspect",
                model="fake",
                prompt_version="coding_agent_v1",
                max_steps=20,
            )
            run_id = run.id

        hub = RunEventHub(queue_size=1)
        client.app.state.event_hub = hub

        class OverflowingWebSocket:
            def __init__(self) -> None:
                self.app = client.app
                self.sent: list[dict[str, object]] = []

            async def accept(self) -> None:
                pass

            async def close(self, code: int) -> None:
                raise AssertionError(f"unexpected close: {code}")

            async def send_json(self, payload: dict[str, object]) -> None:
                self.sent.append(payload)
                if len(self.sent) != 1:
                    return
                with client.app.state.session_factory() as db:
                    repository = RunRepository(db)
                    repository.finish_run(
                        run_id,
                        "failed",
                        step_count=1,
                        error_text="durable failure",
                    )
                    detail = repository.get_run_detail(run_id)
                assert detail is not None
                hub.publish(RunEvent.create("files.changed", run_id, []))
                hub.publish(RunEvent.create("run.finished", run_id, asdict(detail)))

        websocket = OverflowingWebSocket()

        async def exercise() -> None:
            await asyncio.wait_for(run_events(websocket, run_id), 1)  # type: ignore[arg-type]

        asyncio.run(exercise())

        assert [payload["type"] for payload in websocket.sent] == [
            "run.snapshot",
            "run.finished",
        ]
        assert websocket.sent[-1]["data"]["status"] == "failed"  # type: ignore[index]
        durable = client.get(f"/api/runs/{run_id}").json()
        assert durable["status"] == "failed"
        assert durable["error_text"] == "durable failure"


def test_terminal_run_websocket_returns_one_snapshot(app_factory, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with app_factory(
        type(
            "Model",
            (),
            {
                "complete": lambda self, messages, tools, on_text_delta=None: finish("Done.")
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
