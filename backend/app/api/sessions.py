from collections.abc import Iterator
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.db.repositories import SessionRepository
from app.schemas import SessionCreate, SessionRead

router = APIRouter(prefix="/sessions", tags=["sessions"])


def get_db(request: Request) -> Iterator[Session]:
    with request.app.state.session_factory() as db:
        yield db


@router.post("", response_model=SessionRead, status_code=status.HTTP_201_CREATED)
def create_session(payload: SessionCreate, db: Session = Depends(get_db)) -> SessionRead:
    workspace = Path(payload.workspace_path).expanduser().resolve()
    if not workspace.is_dir():
        raise HTTPException(status_code=422, detail="Workspace directory does not exist")
    record = SessionRepository(db).create(
        title=payload.title, workspace_path=str(workspace)
    )
    return SessionRead.model_validate(record)


@router.get("", response_model=list[SessionRead])
def list_sessions(db: Session = Depends(get_db)) -> list[SessionRead]:
    return [SessionRead.model_validate(item) for item in SessionRepository(db).list()]
