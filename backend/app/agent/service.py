"""Synchronous orchestration of one persisted agent run."""

from __future__ import annotations

import threading
from pathlib import Path

from sqlalchemy.orm import sessionmaker

from app.agent.loop import AgentLoop, CancellationToken
from app.agent.tools import ToolRegistry
from app.agent.types import ModelClient
from app.agent.workspace import WorkspaceService
from app.db.models import SessionRecord
from app.db.run_repository import RunDetail, RunRepository


_PROMPT_VERSION = "coding_agent_v1"


class AgentBusyError(Exception):
    """Raised when another run is executing in this application process."""


class WorkspaceUnavailableError(Exception):
    """Raised when a session's saved workspace no longer exists."""


class SessionNotFoundError(Exception):
    """Raised when the supplied session does not exist."""


class AgentService:
    def __init__(
        self,
        session_factory: sessionmaker,
        model_client: ModelClient,
        execution_lock: threading.Lock,
    ) -> None:
        self.session_factory = session_factory
        self.model_client = model_client
        self.execution_lock = execution_lock

    def execute(self, session_id: str, prompt: str, max_steps: int) -> RunDetail:
        if not self.execution_lock.acquire(blocking=False):
            raise AgentBusyError()
        try:
            with self.session_factory() as db:
                session = db.get(SessionRecord, session_id)
                if session is None:
                    raise SessionNotFoundError()
                workspace_path = Path(session.workspace_path).expanduser().resolve()
                if not workspace_path.is_dir():
                    raise WorkspaceUnavailableError()

                repository = RunRepository(db)
                prior_messages = repository.completed_history(session_id)
                run = repository.create_run(
                    session_id=session_id,
                    prompt=prompt,
                    model=self._model_name(),
                    prompt_version=_PROMPT_VERSION,
                    max_steps=max_steps,
                )
                workspace = WorkspaceService(workspace_path)
                loop = AgentLoop(self.model_client, ToolRegistry(workspace), repository, workspace)
                loop.run(
                    run_id=run.id,
                    session_id=session_id,
                    prompt=prompt,
                    prior_messages=prior_messages,
                    max_steps=max_steps,
                    cancellation=CancellationToken(),
                )
                detail = repository.get_run_detail(run.id)
                if detail is None:  # pragma: no cover - database contract guard
                    raise RuntimeError("Run detail was not persisted")
                return detail
        finally:
            self.execution_lock.release()

    def _model_name(self) -> str:
        model_name = getattr(self.model_client, "model", None)
        if isinstance(model_name, str) and model_name:
            return model_name
        configured_name = getattr(self.model_client, "_model", None)
        if isinstance(configured_name, str) and configured_name:
            return configured_name
        return type(self.model_client).__name__
