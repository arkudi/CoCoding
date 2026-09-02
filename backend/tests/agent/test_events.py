import asyncio
import threading

from app.agent.events import RunEvent, RunEventHub


def test_publish_from_worker_thread_reaches_matching_subscription() -> None:
    async def exercise() -> None:
        hub = RunEventHub(queue_size=4)
        matching = hub.subscribe("run-1")
        other = hub.subscribe("run-2")
        event = RunEvent.create("run.started", "run-1", {"status": "running"})

        thread = threading.Thread(target=hub.publish, args=(event,))
        thread.start()
        thread.join()

        assert await asyncio.wait_for(matching.receive(), 1) == event
        assert other.queue.empty()
        hub.unsubscribe(matching)
        hub.unsubscribe(other)
        assert hub.subscriber_count == 0

    asyncio.run(exercise())


def test_queue_overflow_collapses_to_resync_event() -> None:
    async def exercise() -> None:
        hub = RunEventHub(queue_size=1)
        subscription = hub.subscribe("run-1")

        hub.publish(RunEvent.create("tool.started", "run-1", {"id": "one"}))
        hub.publish(RunEvent.create("tool.finished", "run-1", {"id": "one"}))
        await asyncio.sleep(0)

        event = await asyncio.wait_for(subscription.receive(), 1)
        assert event.type == "run.resync_required"
        assert event.run_id == "run-1"

    asyncio.run(exercise())


def test_queue_overflow_preserves_incoming_terminal_event() -> None:
    async def exercise() -> None:
        hub = RunEventHub(queue_size=1)
        subscription = hub.subscribe("run-1")
        terminal = RunEvent.create("run.finished", "run-1", {"status": "failed"})

        hub.publish(RunEvent.create("tool.finished", "run-1", {"id": "one"}))
        hub.publish(terminal)
        await asyncio.sleep(0)

        assert await asyncio.wait_for(subscription.receive(), 1) == terminal

    asyncio.run(exercise())
