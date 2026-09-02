"""Prepare and execute persisted agent runs."""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Callable

from sqlalchemy.orm import sessionmaker

from app.agent.events import RunEvent
from app.agent.loop import AgentLoop, CancellationToken
from app.agent.orchestration import (
    MANAGER_TOOLS,
    MultiAgentCoordinator,
    SharedStepBudget,
    build_manager_prompt,
)
from app.agent.tools import ToolRegistry
from app.agent.types import ModelClient
from app.agent.workspace import WorkspaceService
from app.agent.verifier import VerificationPolicy
from app.db.models import SessionRecord
from app.db.run_repository import RunDetail, RunRepository


_PROMPT_VERSION = "coding_agent_v1"
_MULTI_AGENT_PROMPT_VERSION = "manager_worker_v1"
_INTERNAL_ERROR = "The run failed because of an internal error."

logger = logging.getLogger(__name__)


class AgentBusyError(Exception):
    """Raised when another run is executing in this application process."""


class WorkspaceUnavailableError(Exception):
    """Raised when a session's saved workspace no longer exists."""


class SessionNotFoundError(Exception):
    """Raised when the supplied session does not exist."""


class RunNotFoundError(Exception):
    """Raised when the supplied Run does not exist."""


class AgentService:
    def __init__(
        self,
        session_factory: sessionmaker,
        model_client: ModelClient,
        execution_lock: threading.Lock | None = None,
        verification_policy: VerificationPolicy | None = None,
        multi_agent_enabled: bool = False,
        max_delegations: int = 3,
        child_step_limit: int = 10,
        token_budget: int = 200_000,
        tool_call_limit: int = 200,
        wall_clock_limit_seconds: int = 900,
    ) -> None:
        self.session_factory = session_factory
        self.model_client = model_client
        self.execution_lock = execution_lock
        self.verification_policy = verification_policy or VerificationPolicy()
        self.multi_agent_enabled = multi_agent_enabled
        self.max_delegations = max_delegations
        self.child_step_limit = child_step_limit
        self.token_budget = token_budget
        self.tool_call_limit = tool_call_limit
        self.wall_clock_limit_seconds = wall_clock_limit_seconds

    def execute(self, session_id: str, prompt: str, max_steps: int) -> RunDetail:
        if self.execution_lock is None:
            raise RuntimeError("Synchronous execution requires an execution lock")
        if not self.execution_lock.acquire(blocking=False):
            raise AgentBusyError()
        try:
            detail = self.create_run(session_id, prompt, max_steps)
            return self.execute_existing(detail.id, CancellationToken())
        finally:
            self.execution_lock.release()

    def create_run(self, session_id: str, prompt: str, max_steps: int) -> RunDetail:
        with self.session_factory() as db:
            session = db.get(SessionRecord, session_id)
            if session is None:
                raise SessionNotFoundError()
            workspace_path = Path(session.workspace_path).expanduser().resolve()
            if not workspace_path.is_dir():
                raise WorkspaceUnavailableError()
            repository = RunRepository(db)
            repository.completed_history(session_id)
            run = repository.create_run(
                session_id=session_id,
                prompt=prompt,
                model=self._model_name(),
                prompt_version=(
                    _MULTI_AGENT_PROMPT_VERSION
                    if self.multi_agent_enabled
                    else _PROMPT_VERSION
                ),
                max_steps=max_steps,
            )
            detail = repository.get_run_detail(run.id)
            if detail is None:  # pragma: no cover - database contract guard
                raise RuntimeError("Run detail was not persisted")
            return detail

    def execute_existing(
        self,
        run_id: str,
        cancellation: CancellationToken,
        event_sink: Callable[[RunEvent], None] | None = None,
    ) -> RunDetail:
        with self.session_factory() as db:
            repository = RunRepository(db)
            detail = repository.get_run_detail(run_id)
            if detail is None:
                raise RunNotFoundError()
            session = db.get(SessionRecord, detail.session_id)
            if session is None:
                raise SessionNotFoundError()
            workspace_path = Path(session.workspace_path).expanduser().resolve()
            if not workspace_path.is_dir():
                raise WorkspaceUnavailableError()
            prior_messages = repository.completed_history(session.id)
            workspace = WorkspaceService(workspace_path)
            step_count = 0
            try:
                loop_options: dict[str, object] = {}
                if self.multi_agent_enabled:
                    manager = repository.start_agent_execution(
                        run_id, role="manager", task=detail.prompt
                    )
                    if event_sink is not None:
                        event_sink(
                            RunEvent.create(
                                "agent.started",
                                run_id,
                                {
                                    "id": manager.id,
                                    "run_id": manager.run_id,
                                    "parent_execution_id": manager.parent_execution_id,
                                    "role": manager.role,
                                    "task": manager.task,
                                    "status": manager.status,
                                    "step_count": manager.step_count,
                                    "final_result_json": manager.final_result_json,
                                    "started_at": manager.started_at,
                                    "finished_at": manager.finished_at,
                                },
                            )
                        )
                    budget = SharedStepBudget(
                        detail.max_steps,
                        token_limit=self.token_budget,
                        tool_call_limit=self.tool_call_limit,
                        wall_clock_limit_seconds=self.wall_clock_limit_seconds,
                        delegation_limit=self.max_delegations,
                    )
                    coordinator = MultiAgentCoordinator(
                        model=self.model_client,
                        registry=ToolRegistry(workspace),
                        repository=repository,
                        run_id=run_id,
                        parent_execution_id=manager.id,
                        workspace=workspace.root,
                        budget=budget,
                        cancellation=cancellation,
                        event_sink=event_sink,
                        max_delegations=self.max_delegations,
                        child_step_limit=self.child_step_limit,
                    )
                    loop_options = {
                        "allowed_tools": MANAGER_TOOLS,
                        "delegator": coordinator.delegate,
                        "shared_budget": budget,
                        "system_prompt": build_manager_prompt(workspace.root),
                        "execution_id": manager.id,
                        "completion_guard": coordinator.completion_guard,
                    }
                loop = AgentLoop(
                    self.model_client,
                    ToolRegistry(workspace),
                    repository,
                    workspace,
                    event_sink=event_sink,
                    verification_policy=self.verification_policy,
                    **loop_options,
                )
                result = loop.run(
                    run_id=run_id,
                    session_id=session.id,
                    prompt=detail.prompt,
                    prior_messages=prior_messages,
                    max_steps=detail.max_steps,
                    cancellation=cancellation,
                )
                step_count = result.step_count
                finished = repository.get_run_detail(run_id)
            except Exception as error:
                finished = self._recover_created_run(
                    repository, workspace, run_id, step_count, error
                )
            if finished is None:  # pragma: no cover - database contract guard
                raise RuntimeError("Run detail was not persisted")
            return finished

    def _model_name(self) -> str:
        model_name = getattr(self.model_client, "model", None)
        if isinstance(model_name, str) and model_name:
            return model_name
        configured_name = getattr(self.model_client, "_model", None)
        if isinstance(configured_name, str) and configured_name:
            return configured_name
        return type(self.model_client).__name__

    @staticmethod
    def _recover_created_run(
        repository: RunRepository,
        workspace: WorkspaceService,
        run_id: str,
        step_count: int,
        error: Exception,
    ) -> RunDetail | None:
        logger.exception(
            "Unexpected failure after run creation (type=%s)",
            type(error).__name__,
        )
        repository.db.rollback()

        try:
            repository.replace_file_changes(run_id, workspace.changes())
        except Exception as evidence_error:
            logger.exception(
                "Could not persist final run evidence (type=%s)",
                type(evidence_error).__name__,
            )
            repository.db.rollback()

        try:
            repository.finish_run(
                run_id,
                "failed",
                step_count=step_count,
                error_text=_INTERNAL_ERROR,
            )
        except Exception as finish_error:
            logger.exception(
                "Could not persist failed run state (type=%s)",
                type(finish_error).__name__,
            )
            repository.db.rollback()

        return repository.get_run_detail(run_id)
