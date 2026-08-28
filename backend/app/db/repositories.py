from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import SessionRecord


class SessionRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(self, *, title: str, workspace_path: str) -> SessionRecord:
        record = SessionRecord(title=title, workspace_path=workspace_path)
        self.db.add(record)
        self.db.commit()
        self.db.refresh(record)
        return record

    def list(self) -> list[SessionRecord]:
        statement = select(SessionRecord).order_by(SessionRecord.updated_at.desc())
        return list(self.db.scalars(statement))
