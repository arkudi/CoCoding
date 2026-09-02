from collections.abc import Iterator
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.db.repositories import SessionHasActiveRunError, SessionRepository
from app.directory_picker import DirectoryPickerUnavailableError
from app.schemas import (
    DirectorySelectionCreate,
    DirectorySelectionRead,
    SessionCreate,
    SessionRead,
)

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.post("/select-workspace", response_model=DirectorySelectionRead)
def select_workspace(
    request: Request, payload: DirectorySelectionCreate | None = None
) -> DirectorySelectionRead:
    try:
        initial_path = payload.initial_path if payload is not None else None
        return DirectorySelectionRead(
            path=request.app.state.directory_picker(initial_path)
        )
    except DirectoryPickerUnavailableError as error:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=str(error)
        ) from error


def get_db(request: Request) -> Iterator[Session]:
    with request.app.state.session_factory() as db:
        yield db


@router.post("", response_model=SessionRead, status_code=status.HTTP_201_CREATED)
def create_session(payload: SessionCreate, db: Session = Depends(get_db)) -> SessionRead:
    workspace = Path(payload.workspace_path).expanduser().resolve()
    if not workspace.is_dir():
        raise HTTPException(status_code=422, detail="Workspace directory does not exist")
    initial_title = payload.title or f"新任务 · {workspace.name or 'workspace'}"
    record = SessionRepository(db).create(title=initial_title, workspace_path=str(workspace))
    return SessionRead.model_validate(record)


@router.get("", response_model=list[SessionRead])
def list_sessions(db: Session = Depends(get_db)) -> list[SessionRead]:
    return [SessionRead.model_validate(item) for item in SessionRepository(db).list()]


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_session(session_id: str, db: Session = Depends(get_db)) -> Response:
    try:
        deleted = SessionRepository(db).delete(session_id)
    except SessionHasActiveRunError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="正在执行的任务不能删除，请先取消任务",
        ) from error
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
