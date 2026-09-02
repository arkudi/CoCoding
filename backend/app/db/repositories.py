from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.db.models import (
    AgentExecutionRecord,
    AgentTaskRecord,
    AgentToolCallRecord,
    FileChangeRecord,
    MessageRecord,
    RunRecord,
    SessionRecord,
    ToolCallRecord,
)


class SessionHasActiveRunError(Exception):
    """Raised when deletion is attempted while a session is running."""


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

    def delete(self, session_id: str) -> bool:
        record = self.db.get(SessionRecord, session_id)
        if record is None:
            return False

        run_ids = list(
            self.db.scalars(select(RunRecord.id).where(RunRecord.session_id == session_id))
        )
        if run_ids and self.db.scalar(
            select(RunRecord.id)
            .where(RunRecord.id.in_(run_ids), RunRecord.status == "running")
            .limit(1)
        ):
            raise SessionHasActiveRunError()

        if run_ids:
            tool_call_ids = list(
                self.db.scalars(
                    select(ToolCallRecord.id).where(ToolCallRecord.run_id.in_(run_ids))
                )
            )
            if tool_call_ids:
                self.db.execute(
                    delete(AgentToolCallRecord).where(
                        AgentToolCallRecord.tool_call_id.in_(tool_call_ids)
                    )
                )
            self.db.execute(delete(AgentTaskRecord).where(AgentTaskRecord.run_id.in_(run_ids)))
            self.db.execute(
                update(AgentExecutionRecord)
                .where(AgentExecutionRecord.run_id.in_(run_ids))
                .values(parent_execution_id=None)
            )
            self.db.execute(
                delete(AgentExecutionRecord).where(AgentExecutionRecord.run_id.in_(run_ids))
            )
            self.db.execute(delete(MessageRecord).where(MessageRecord.run_id.in_(run_ids)))
            self.db.execute(delete(FileChangeRecord).where(FileChangeRecord.run_id.in_(run_ids)))
            self.db.execute(delete(ToolCallRecord).where(ToolCallRecord.run_id.in_(run_ids)))
            self.db.execute(delete(RunRecord).where(RunRecord.id.in_(run_ids)))

        self.db.delete(record)
        self.db.commit()
        return True
