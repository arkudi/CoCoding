"""Single-slot background execution and cooperative cancellation."""

from __future__ import annotations

import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import asdict, dataclass

from sqlalchemy.orm import sessionmaker

from app.agent.events import RunEvent, RunEventHub
from app.agent.loop import CancellationToken
from app.agent.service import (
    AgentBusyError,
    AgentService,
    RunNotFoundError,
)
from app.agent.types import ModelClient
from app.agent.verifier import VerificationPolicy
from app.db.run_repository import RunDetail, RunRepository


_INTERNAL_ERROR = "The run failed because of an internal error."
_ORPHANED_ERROR = "Run was no longer active in this application process."
logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CancelResult:
    run_id: str
    status: str
    requested: bool


@dataclass(slots=True)
class _ActiveRun:
    run_id: str
    token: CancellationToken
    future: Future[None] | None = None


class RunManager:
    def __init__(
        self,
        session_factory: sessionmaker,
        event_hub: RunEventHub,
        hard_step_limit: int = 50,
        verification_policy: VerificationPolicy | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._hub = event_hub
        self._hard_step_limit = hard_step_limit
        self._verification_policy = verification_policy or VerificationPolicy()
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="cocoding-agent"
        )
        self._state_lock = threading.Lock()
        self._active: _ActiveRun | None = None
        self._last_future: Future[None] | None = None
        self._closed = False

    def start(
        self,
        session_id: str,
        prompt: str,
        model_client: ModelClient,
    ) -> RunDetail:
        with self._state_lock:
            if self._closed:
                raise RuntimeError("Run manager is shut down")
            if self._active is not None:
                raise AgentBusyError()
            service = AgentService(
                self._session_factory,
                model_client,
                verification_policy=self._verification_policy,
            )
            detail = service.create_run(session_id, prompt, self._hard_step_limit)
            token = CancellationToken()
            active = _ActiveRun(detail.id, token)
            self._active = active
            try:
                future = self._executor.submit(
                    self._execute, detail.id, token, model_client
                )
            except Exception:
                self._fail_run(detail.id)
                self._active = None
                raise
            active.future = future
            self._last_future = future
            return detail

    def cancel(self, run_id: str) -> CancelResult:
        with self._state_lock:
            active = self._active
            if active is not None and active.run_id == run_id:
                active.token.cancel()
                return CancelResult(run_id, "running", True)
        with self._session_factory() as db:
            repository = RunRepository(db)
            detail = repository.get_run_detail(run_id)
            if detail is None:
                raise RunNotFoundError()
            if detail.status == "running":
                repository.interrupt_run(run_id, _ORPHANED_ERROR)
                repository.set_session_status(detail.session_id, "interrupted")
                return CancelResult(run_id, "interrupted", False)
            return CancelResult(run_id, detail.status, False)

    @property
    def active_run_id(self) -> str | None:
        with self._state_lock:
            return None if self._active is None else self._active.run_id

    def wait_for_idle(self, timeout: float) -> None:
        with self._state_lock:
            future = self._last_future
        if future is not None:
            future.result(timeout=timeout)

    def shutdown(self, wait: bool = True) -> None:
        with self._state_lock:
            self._closed = True
            active = self._active
            if active is not None:
                active.token.cancel()
        self._executor.shutdown(wait=wait, cancel_futures=False)

    def _execute(
        self,
        run_id: str,
        token: CancellationToken,
        model_client: ModelClient,
    ) -> None:
        try:
            with self._session_factory() as db:
                detail = RunRepository(db).get_run_detail(run_id)
                if detail is None:
                    return
                RunRepository(db).set_session_status(detail.session_id, "running")
            self._hub.publish(
                RunEvent.create("run.started", run_id, {"status": "running"})
            )
            finished = AgentService(
                self._session_factory,
                model_client,
                verification_policy=self._verification_policy,
            ).execute_existing(run_id, token, self._hub.publish)
            with self._session_factory() as db:
                RunRepository(db).set_session_status(
                    finished.session_id, finished.status
                )
        except Exception as error:
            logger.exception(
                "Unexpected background Run failure (type=%s)", type(error).__name__
            )
            self._fail_run(run_id)
        finally:
            with self._state_lock:
                if self._active is not None and self._active.run_id == run_id:
                    self._active = None

    def _fail_run(self, run_id: str) -> None:
        with self._session_factory() as db:
            repository = RunRepository(db)
            detail = repository.get_run_detail(run_id)
            if detail is None or detail.status != "running":
                return
            repository.finish_run(
                run_id,
                "failed",
                step_count=detail.step_count,
                error_text=_INTERNAL_ERROR,
            )
            repository.set_session_status(detail.session_id, "failed")
            finished = repository.get_run_detail(run_id)
        if finished is not None:
            self._hub.publish(
                RunEvent.create("run.finished", run_id, asdict(finished))
            )


__all__ = ["AgentBusyError", "CancelResult", "RunManager"]
