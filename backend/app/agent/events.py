"""Thread-safe live projections for one-process Run WebSockets."""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from datetime import datetime

from app.db.models import utc_now


@dataclass(frozen=True, slots=True)
class RunEvent:
    type: str
    run_id: str
    occurred_at: datetime
    data: object

    @classmethod
    def create(cls, event_type: str, run_id: str, data: object) -> "RunEvent":
        return cls(event_type, run_id, utc_now(), data)


@dataclass(eq=False, slots=True)
class RunEventSubscription:
    run_id: str
    loop: asyncio.AbstractEventLoop
    queue: asyncio.Queue[RunEvent]

    async def receive(self) -> RunEvent:
        return await self.queue.get()


class RunEventHub:
    """Fan out worker-thread events without blocking agent execution."""

    def __init__(self, queue_size: int = 64) -> None:
        if queue_size < 1:
            raise ValueError("queue_size must be positive")
        self._queue_size = queue_size
        self._subscriptions: dict[str, set[RunEventSubscription]] = {}
        self._lock = threading.Lock()

    def subscribe(self, run_id: str) -> RunEventSubscription:
        subscription = RunEventSubscription(
            run_id=run_id,
            loop=asyncio.get_running_loop(),
            queue=asyncio.Queue(maxsize=self._queue_size),
        )
        with self._lock:
            self._subscriptions.setdefault(run_id, set()).add(subscription)
        return subscription

    def unsubscribe(self, subscription: RunEventSubscription) -> None:
        with self._lock:
            subscribers = self._subscriptions.get(subscription.run_id)
            if subscribers is None:
                return
            subscribers.discard(subscription)
            if not subscribers:
                self._subscriptions.pop(subscription.run_id, None)

    def publish(self, event: RunEvent) -> None:
        with self._lock:
            subscribers = tuple(self._subscriptions.get(event.run_id, ()))
        for subscription in subscribers:
            try:
                subscription.loop.call_soon_threadsafe(
                    self._enqueue, subscription, event
                )
            except RuntimeError:
                self.unsubscribe(subscription)

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return sum(len(items) for items in self._subscriptions.values())

    @staticmethod
    def _enqueue(subscription: RunEventSubscription, event: RunEvent) -> None:
        if subscription.queue.full():
            while not subscription.queue.empty():
                subscription.queue.get_nowait()
            event = RunEvent.create("run.resync_required", subscription.run_id, {})
        subscription.queue.put_nowait(event)
