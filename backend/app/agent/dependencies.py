"""FastAPI dependencies for configuring a concrete agent service."""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status

from app.agent.provider import DeepSeekClient
from app.agent.service import AgentService
from app.agent.run_manager import RunManager
from app.agent.types import ModelClient


def get_model_client(request: Request) -> ModelClient:
    injected = request.app.state.model_client
    if injected is not None:
        return injected
    if not (request.app.state.settings.deepseek_api_key or "").strip():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "MODEL_CONFIGURATION_UNAVAILABLE",
                "message": "The model provider is not configured.",
            },
        )
    return request.app.state.production_model_client_factory()


def get_agent_service(
    request: Request, model_client: object = Depends(get_model_client)
) -> AgentService:
    return AgentService(
        request.app.state.session_factory,
        model_client,  # type: ignore[arg-type]
        multi_agent_enabled=request.app.state.settings.agent_multi_agent_enabled,
        max_delegations=request.app.state.settings.agent_max_delegations,
        child_step_limit=request.app.state.settings.agent_child_step_limit,
    )


def get_run_manager(request: Request) -> RunManager:
    return request.app.state.run_manager
