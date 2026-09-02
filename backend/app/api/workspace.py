"""Read-only session workspace browsing endpoints."""

from collections.abc import Iterator
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.agent.workspace import WorkspaceError, WorkspaceService
from app.db.models import SessionRecord
from app.schemas import WorkspaceFileRead, WorkspaceFilesRead


router = APIRouter(tags=["workspace"])
_BROWSER_MAX_ENTRIES = 20_000


def get_db(request: Request) -> Iterator[Session]:
    with request.app.state.session_factory() as db:
        yield db


def _workspace(db: Session, session_id: str) -> WorkspaceService:
    session = db.get(SessionRecord, session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "SESSION_NOT_FOUND", "message": "Session not found."},
        )
    root = Path(session.workspace_path).expanduser().resolve()
    if not root.is_dir():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "WORKSPACE_UNAVAILABLE",
                "message": "The session workspace is unavailable.",
            },
        )
    return WorkspaceService(root)


def _workspace_error(error: WorkspaceError) -> HTTPException:
    status_code = {
        "PATH_NOT_FOUND": status.HTTP_404_NOT_FOUND,
        "FILE_TOO_LARGE": status.HTTP_413_CONTENT_TOO_LARGE,
        "INVALID_UTF8": status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
    }.get(error.code, status.HTTP_400_BAD_REQUEST)
    return HTTPException(
        status_code=status_code,
        detail={"code": error.code, "message": error.message},
    )


@router.get("/sessions/{session_id}/files", response_model=WorkspaceFilesRead)
def list_workspace_files(
    session_id: str, db: Session = Depends(get_db)
) -> WorkspaceFilesRead:
    try:
        workspace = _workspace(db, session_id)
        workspace.max_entries = _BROWSER_MAX_ENTRIES
        return WorkspaceFilesRead.model_validate(workspace.list_files("."))
    except WorkspaceError as error:
        raise _workspace_error(error) from error


@router.get(
    "/sessions/{session_id}/files/content", response_model=WorkspaceFileRead
)
def read_workspace_file(
    session_id: str, path: str, db: Session = Depends(get_db)
) -> WorkspaceFileRead:
    try:
        data = _workspace(db, session_id).read_file(path)
    except WorkspaceError as error:
        raise _workspace_error(error) from error
    content = str(data["content"])
    return WorkspaceFileRead(
        path=str(data["path"]), content=content, size=len(content.encode("utf-8"))
    )
