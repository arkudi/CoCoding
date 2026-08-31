from collections.abc import Iterator

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.agent.dependencies import get_agent_service
from app.agent.service import (
    AgentBusyError,
    AgentService,
    SessionNotFoundError,
    WorkspaceUnavailableError,
)
from app.db.run_repository import RunRepository
from app.schemas import RunCreate, RunRead


router = APIRouter(tags=["runs"])


def get_db(request: Request) -> Iterator[Session]:
    with request.app.state.session_factory() as db:
        yield db


@router.post(
    "/sessions/{session_id}/runs",
    response_model=RunRead,
    status_code=status.HTTP_201_CREATED,
)
def create_run(
    session_id: str,
    payload: RunCreate,
    service: AgentService = Depends(get_agent_service),
) -> RunRead:
    try:
        return RunRead.model_validate(service.execute(session_id, payload.prompt, payload.max_steps))
    except SessionNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found") from error
    except WorkspaceUnavailableError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "WORKSPACE_UNAVAILABLE",
                "message": "The session workspace is unavailable.",
            },
        ) from error
    except AgentBusyError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "RUN_ALREADY_ACTIVE",
                "message": "Another agent run is already active.",
            },
        ) from error


@router.get("/runs/{run_id}", response_model=RunRead)
def get_run(run_id: str, db: Session = Depends(get_db)) -> RunRead:
    detail = RunRepository(db).get_run_detail(run_id)
    if detail is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    return RunRead.model_validate(detail)
