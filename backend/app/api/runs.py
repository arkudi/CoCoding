from collections.abc import Iterator

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect, status
from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm import Session

from app.agent.dependencies import get_model_client, get_run_manager
from app.agent.events import RunEvent, RunEventHub
from app.agent.run_manager import RunManager
from app.agent.service import (
    AgentBusyError,
    RunNotFoundError,
    SessionNotFoundError,
    WorkspaceUnavailableError,
)
from app.agent.types import ModelClient
from app.db.run_repository import RunRepository
from app.schemas import RunCancelRead, RunCreate, RunEventRead, RunRead


router = APIRouter(tags=["runs"])


def get_db(request: Request) -> Iterator[Session]:
    with request.app.state.session_factory() as db:
        yield db


@router.post(
    "/sessions/{session_id}/runs",
    response_model=RunRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_run(
    session_id: str,
    payload: RunCreate,
    manager: RunManager = Depends(get_run_manager),
    model_client: ModelClient = Depends(get_model_client),
) -> RunRead:
    try:
        return RunRead.model_validate(
            manager.start(session_id, payload.prompt, model_client)
        )
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


@router.get("/sessions/{session_id}/runs", response_model=list[RunRead])
def list_runs(session_id: str, db: Session = Depends(get_db)) -> list[RunRead]:
    try:
        return [
            RunRead.model_validate(detail)
            for detail in RunRepository(db).list_runs(session_id)
        ]
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found") from error


@router.post("/runs/{run_id}/cancel", response_model=RunCancelRead)
def cancel_run(
    run_id: str, manager: RunManager = Depends(get_run_manager)
) -> RunCancelRead:
    try:
        return RunCancelRead.model_validate(manager.cancel(run_id), from_attributes=True)
    except RunNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found") from error


@router.get("/runs/{run_id}", response_model=RunRead)
def get_run(run_id: str, db: Session = Depends(get_db)) -> RunRead:
    detail = RunRepository(db).get_run_detail(run_id)
    if detail is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    return RunRead.model_validate(detail)


@router.websocket("/runs/{run_id}/events")
async def run_events(websocket: WebSocket, run_id: str) -> None:
    hub: RunEventHub = websocket.app.state.event_hub
    subscription = hub.subscribe(run_id)
    try:
        with websocket.app.state.session_factory() as db:
            detail = RunRepository(db).get_run_detail(run_id)
        if detail is None:
            await websocket.close(code=4404)
            return
        await websocket.accept()
        snapshot = RunEventRead(
            type="run.snapshot",
            run_id=run_id,
            occurred_at=RunEvent.create("run.snapshot", run_id, {}).occurred_at,
            data=RunRead.model_validate(detail).model_dump(mode="json"),
        )
        await websocket.send_json(snapshot.model_dump(mode="json"))
        if detail.status != "running":
            return
        while True:
            event = await subscription.receive()
            payload = RunEventRead(
                type=event.type,
                run_id=event.run_id,
                occurred_at=event.occurred_at,
                data=jsonable_encoder(event.data),
            )
            await websocket.send_json(payload.model_dump(mode="json"))
            if event.type == "run.finished":
                return
    except WebSocketDisconnect:
        return
    finally:
        hub.unsubscribe(subscription)
