from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.agent.workspace import FileChangeEvidence
from app.db.models import (
    RUN_STATUSES,
    FileChangeRecord,
    MessageRecord,
    RunRecord,
    SessionRecord,
    ToolCallRecord,
    utc_now,
)


_TERMINAL_RUN_STATUSES = frozenset(RUN_STATUSES) - {"running"}
_TOOL_RESULT_CHARACTER_LIMIT = 20_000
_STARTUP_INTERRUPTION_ERROR = "Run interrupted during startup recovery."


@dataclass(frozen=True, slots=True)
class MessageDetail:
    id: str
    run_id: str
    session_id: str
    role: str
    content: str | None
    tool_calls_json: str | None
    tool_call_id: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ToolCallDetail:
    id: str
    run_id: str
    provider_call_id: str
    name: str
    arguments_json: str
    status: str
    result_json: str | None
    duration_ms: int | None
    started_at: datetime
    finished_at: datetime | None


@dataclass(frozen=True, slots=True)
class FileChangeDetail:
    id: str
    run_id: str
    path: str
    operation: str
    before_hash: str | None
    after_hash: str
    unified_diff: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class RunDetail:
    id: str
    session_id: str
    prompt: str
    model: str
    prompt_version: str
    status: str
    max_steps: int
    step_count: int
    final_response: str | None
    error_text: str | None
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None
    messages: tuple[MessageDetail, ...]
    tool_calls: tuple[ToolCallDetail, ...]
    file_changes: tuple[FileChangeDetail, ...]


class RunRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create_run(
        self,
        *,
        session_id: str,
        prompt: str,
        model: str,
        prompt_version: str,
        max_steps: int,
    ) -> RunRecord:
        self._require_session(session_id)
        record = RunRecord(
            session_id=session_id,
            prompt=prompt,
            model=model,
            prompt_version=prompt_version,
            max_steps=max_steps,
        )
        self.db.add(record)
        self._commit_and_refresh(record)
        return record

    def add_message(
        self,
        run_id: str,
        session_id: str,
        role: str,
        content: str | None,
        tool_calls_json: str | None = None,
        tool_call_id: str | None = None,
    ) -> MessageRecord:
        run = self._require_run(run_id)
        if run.session_id != session_id:
            raise ValueError("Run does not belong to the supplied session")
        record = MessageRecord(
            run_id=run_id,
            session_id=session_id,
            role=role,
            content=content,
            tool_calls_json=tool_calls_json,
            tool_call_id=tool_call_id,
        )
        self.db.add(record)
        self._commit_and_refresh(record)
        return record

    def start_tool_call(
        self,
        run_id: str,
        provider_call_id: str,
        name: str,
        arguments_json: str,
    ) -> ToolCallRecord:
        self._require_run(run_id)
        record = ToolCallRecord(
            run_id=run_id,
            provider_call_id=provider_call_id,
            name=name,
            arguments_json=arguments_json,
        )
        self.db.add(record)
        self._commit_and_refresh(record)
        return record

    def finish_tool_call(
        self,
        tool_call_id: str,
        status: str,
        result_json: str | None,
        duration_ms: int,
    ) -> ToolCallRecord:
        record = self._require_tool_call(tool_call_id)
        record.status = status
        record.result_json = (
            None if result_json is None else result_json[:_TOOL_RESULT_CHARACTER_LIMIT]
        )
        record.duration_ms = duration_ms
        record.finished_at = utc_now()
        self._commit_and_refresh(record)
        return record

    def finish_run(
        self,
        run_id: str,
        status: str,
        *,
        step_count: int,
        final_response: str | None = None,
        error_text: str | None = None,
    ) -> RunRecord:
        if status not in _TERMINAL_RUN_STATUSES:
            raise ValueError("Run status must be terminal")
        record = self._require_run(run_id)
        record.status = status
        record.step_count = step_count
        record.final_response = final_response
        record.error_text = error_text
        now = utc_now()
        record.updated_at = now
        record.finished_at = now
        self._commit_and_refresh(record)
        return record

    def replace_file_changes(
        self, run_id: str, changes: tuple[FileChangeEvidence, ...]
    ) -> tuple[FileChangeRecord, ...]:
        self._require_run(run_id)
        self.db.execute(delete(FileChangeRecord).where(FileChangeRecord.run_id == run_id))
        records = tuple(
            FileChangeRecord(
                run_id=run_id,
                path=change.path,
                operation=change.operation,
                before_hash=change.before_hash,
                after_hash=change.after_hash,
                unified_diff=change.unified_diff,
            )
            for change in changes
        )
        self.db.add_all(records)
        self.db.commit()
        for record in records:
            self.db.refresh(record)
        return records

    def get_run_detail(self, run_id: str) -> RunDetail | None:
        run = self.db.get(RunRecord, run_id)
        if run is None:
            return None
        messages = tuple(
            self._message_detail(record)
            for record in self.db.scalars(
                select(MessageRecord)
                .where(MessageRecord.run_id == run_id)
                .order_by(MessageRecord.created_at.asc(), MessageRecord.id.asc())
            )
        )
        tool_calls = tuple(
            self._tool_call_detail(record)
            for record in self.db.scalars(
                select(ToolCallRecord)
                .where(ToolCallRecord.run_id == run_id)
                .order_by(ToolCallRecord.started_at.asc(), ToolCallRecord.id.asc())
            )
        )
        file_changes = tuple(
            self._file_change_detail(record)
            for record in self.db.scalars(
                select(FileChangeRecord)
                .where(FileChangeRecord.run_id == run_id)
                .order_by(FileChangeRecord.created_at.asc(), FileChangeRecord.id.asc())
            )
        )
        return RunDetail(
            id=run.id,
            session_id=run.session_id,
            prompt=run.prompt,
            model=run.model,
            prompt_version=run.prompt_version,
            status=run.status,
            max_steps=run.max_steps,
            step_count=run.step_count,
            final_response=run.final_response,
            error_text=run.error_text,
            created_at=run.created_at,
            updated_at=run.updated_at,
            finished_at=run.finished_at,
            messages=messages,
            tool_calls=tool_calls,
            file_changes=file_changes,
        )

    def completed_history(
        self, session_id: str, character_budget: int = 40_000
    ) -> list[dict[str, object]]:
        statement = (
            select(MessageRecord)
            .join(RunRecord, MessageRecord.run_id == RunRecord.id)
            .where(
                RunRecord.session_id == session_id,
                RunRecord.status == "completed",
                MessageRecord.session_id == session_id,
                MessageRecord.role.in_(("user", "assistant")),
            )
            .order_by(MessageRecord.created_at.desc(), MessageRecord.id.desc())
        )
        selected: list[MessageRecord] = []
        characters = 0
        for message in self.db.scalars(statement):
            content = message.content or ""
            if characters + len(content) > character_budget:
                break
            selected.append(message)
            characters += len(content)
        selected.reverse()
        return [{"role": message.role, "content": message.content} for message in selected]

    def interrupt_running_runs(self) -> int:
        now = utc_now()
        result = self.db.execute(
            update(RunRecord)
            .where(RunRecord.status == "running")
            .values(
                status="interrupted",
                error_text=_STARTUP_INTERRUPTION_ERROR,
                updated_at=now,
                finished_at=now,
            )
        )
        self.db.commit()
        return int(result.rowcount or 0)

    def _require_session(self, session_id: str) -> SessionRecord:
        record = self.db.get(SessionRecord, session_id)
        if record is None:
            raise ValueError("Session not found")
        return record

    def _require_run(self, run_id: str) -> RunRecord:
        record = self.db.get(RunRecord, run_id)
        if record is None:
            raise ValueError("Run not found")
        return record

    def _require_tool_call(self, tool_call_id: str) -> ToolCallRecord:
        record = self.db.get(ToolCallRecord, tool_call_id)
        if record is None:
            raise ValueError("Tool call not found")
        return record

    def _commit_and_refresh(self, record: object) -> None:
        self.db.commit()
        self.db.refresh(record)

    @staticmethod
    def _message_detail(record: MessageRecord) -> MessageDetail:
        return MessageDetail(
            id=record.id,
            run_id=record.run_id,
            session_id=record.session_id,
            role=record.role,
            content=record.content,
            tool_calls_json=record.tool_calls_json,
            tool_call_id=record.tool_call_id,
            created_at=record.created_at,
        )

    @staticmethod
    def _tool_call_detail(record: ToolCallRecord) -> ToolCallDetail:
        return ToolCallDetail(
            id=record.id,
            run_id=record.run_id,
            provider_call_id=record.provider_call_id,
            name=record.name,
            arguments_json=record.arguments_json,
            status=record.status,
            result_json=record.result_json,
            duration_ms=record.duration_ms,
            started_at=record.started_at,
            finished_at=record.finished_at,
        )

    @staticmethod
    def _file_change_detail(record: FileChangeRecord) -> FileChangeDetail:
        return FileChangeDetail(
            id=record.id,
            run_id=record.run_id,
            path=record.path,
            operation=record.operation,
            before_hash=record.before_hash,
            after_hash=record.after_hash,
            unified_diff=record.unified_diff,
            created_at=record.created_at,
        )
