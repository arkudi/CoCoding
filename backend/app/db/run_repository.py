from dataclasses import dataclass
from datetime import datetime
import json

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.agent.workspace import FileChangeEvidence
from app.db.models import (
    RUN_STATUSES,
    AgentExecutionRecord,
    AgentTaskRecord,
    AgentToolCallRecord,
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


def _bounded_tool_output(content: str | None) -> str | None:
    return None if content is None else content[:_TOOL_RESULT_CHARACTER_LIMIT]


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
    agent_execution_id: str | None


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
class AgentExecutionDetail:
    id: str
    run_id: str
    parent_execution_id: str | None
    role: str
    task: str
    status: str
    step_count: int
    final_result_json: str | None
    started_at: datetime
    finished_at: datetime | None


@dataclass(frozen=True, slots=True)
class AgentTaskDetail:
    id: str
    run_id: str
    execution_id: str | None
    role: str
    description: str
    expected_output: str
    depends_on: tuple[str, ...]
    status: str
    result_json: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


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
    agent_executions: tuple[AgentExecutionDetail, ...]
    agent_tasks: tuple[AgentTaskDetail, ...]


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
        self._flush_refresh_and_commit(record)
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
            content=_bounded_tool_output(content) if role == "tool" else content,
            tool_calls_json=tool_calls_json,
            tool_call_id=tool_call_id,
        )
        self.db.add(record)
        self._flush_refresh_and_commit(record)
        return record

    def start_tool_call(
        self,
        run_id: str,
        provider_call_id: str,
        name: str,
        arguments_json: str,
        agent_execution_id: str | None = None,
    ) -> ToolCallRecord:
        self._require_run(run_id)
        if agent_execution_id is not None:
            execution = self._require_agent_execution(agent_execution_id)
            if execution.run_id != run_id:
                raise ValueError("Agent execution does not belong to the run")
        record = ToolCallRecord(
            run_id=run_id,
            provider_call_id=provider_call_id,
            name=name,
            arguments_json=arguments_json,
        )
        self.db.add(record)
        self.db.flush()
        if agent_execution_id is not None:
            self.db.add(
                AgentToolCallRecord(
                    tool_call_id=record.id, agent_execution_id=agent_execution_id
                )
            )
        self.db.refresh(record)
        self.db.commit()
        return record

    def create_agent_task(
        self,
        run_id: str,
        *,
        role: str,
        description: str,
        expected_output: str,
        depends_on: tuple[str, ...] = (),
    ) -> AgentTaskRecord:
        self._require_run(run_id)
        for dependency_id in depends_on:
            dependency = self._require_agent_task(dependency_id)
            if dependency.run_id != run_id:
                raise ValueError("Agent task dependency does not belong to the run")
        record = AgentTaskRecord(
            run_id=run_id,
            role=role,
            description=description,
            expected_output=expected_output,
            depends_on_json=json.dumps(depends_on),
        )
        self.db.add(record)
        self._flush_refresh_and_commit(record)
        return record

    def start_agent_task(self, task_id: str, execution_id: str) -> AgentTaskRecord:
        task = self._require_agent_task(task_id)
        execution = self._require_agent_execution(execution_id)
        if task.run_id != execution.run_id:
            raise ValueError("Agent task and execution belong to different runs")
        dependencies = json.loads(task.depends_on_json)
        if any(self._require_agent_task(item).status != "completed" for item in dependencies):
            raise ValueError("Agent task dependencies are not completed")
        task.execution_id = execution_id
        task.status = "running"
        task.started_at = utc_now()
        self._flush_refresh_and_commit(task)
        return task

    def finish_agent_task(
        self, task_id: str, status: str, *, result_json: str | None = None
    ) -> AgentTaskRecord:
        if status not in {"completed", "failed", "cancelled", "skipped"}:
            raise ValueError("Agent task status must be terminal")
        task = self._require_agent_task(task_id)
        task.status = status
        task.result_json = result_json
        task.finished_at = utc_now()
        self._flush_refresh_and_commit(task)
        return task

    def start_agent_execution(
        self,
        run_id: str,
        *,
        role: str,
        task: str,
        parent_execution_id: str | None = None,
    ) -> AgentExecutionRecord:
        self._require_run(run_id)
        if parent_execution_id is not None:
            parent = self._require_agent_execution(parent_execution_id)
            if parent.run_id != run_id:
                raise ValueError("Parent agent execution does not belong to the run")
        record = AgentExecutionRecord(
            run_id=run_id,
            parent_execution_id=parent_execution_id,
            role=role,
            task=task,
        )
        self.db.add(record)
        self._flush_refresh_and_commit(record)
        return record

    def finish_agent_execution(
        self,
        execution_id: str,
        status: str,
        *,
        step_count: int,
        final_result_json: str | None = None,
    ) -> AgentExecutionRecord:
        if status not in {"completed", "failed", "cancelled"}:
            raise ValueError("Agent execution status must be terminal")
        record = self._require_agent_execution(execution_id)
        record.status = status
        record.step_count = step_count
        record.final_result_json = final_result_json
        record.finished_at = utc_now()
        self._flush_refresh_and_commit(record)
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
        record.result_json = _bounded_tool_output(result_json)
        record.duration_ms = duration_ms
        record.finished_at = utc_now()
        self._flush_refresh_and_commit(record)
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
        self._flush_refresh_and_commit(record)
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
        self.db.flush()
        for record in records:
            self.db.refresh(record)
        self.db.commit()
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
        execution_by_tool_call = dict(
            self.db.execute(
                select(
                    AgentToolCallRecord.tool_call_id,
                    AgentToolCallRecord.agent_execution_id,
                ).join(
                    ToolCallRecord,
                    AgentToolCallRecord.tool_call_id == ToolCallRecord.id,
                ).where(ToolCallRecord.run_id == run_id)
            ).all()
        )
        tool_calls = tuple(
            self._tool_call_detail(record, execution_by_tool_call.get(record.id))
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
        agent_executions = tuple(
            self._agent_execution_detail(record)
            for record in self.db.scalars(
                select(AgentExecutionRecord)
                .where(AgentExecutionRecord.run_id == run_id)
                .order_by(
                    AgentExecutionRecord.started_at.asc(), AgentExecutionRecord.id.asc()
                )
            )
        )
        agent_tasks = tuple(
            self._agent_task_detail(record)
            for record in self.db.scalars(
                select(AgentTaskRecord)
                .where(AgentTaskRecord.run_id == run_id)
                .order_by(AgentTaskRecord.created_at.asc(), AgentTaskRecord.id.asc())
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
            agent_executions=agent_executions,
            agent_tasks=agent_tasks,
        )

    def list_runs(self, session_id: str) -> tuple[RunDetail, ...]:
        self._require_session(session_id)
        run_ids = tuple(
            self.db.scalars(
                select(RunRecord.id)
                .where(RunRecord.session_id == session_id)
                .order_by(RunRecord.created_at.desc(), RunRecord.id.desc())
            )
        )
        details = tuple(
            detail
            for run_id in run_ids
            if (detail := self.get_run_detail(run_id)) is not None
        )
        self.db.rollback()
        return details

    def set_session_status(self, session_id: str, status: str) -> SessionRecord:
        record = self._require_session(session_id)
        record.status = status
        record.updated_at = utc_now()
        self._flush_refresh_and_commit(record)
        return record

    def set_session_title(self, session_id: str, title: str) -> SessionRecord:
        record = self._require_session(session_id)
        record.title = title
        record.updated_at = utc_now()
        self._flush_refresh_and_commit(record)
        return record

    def interrupt_run(self, run_id: str, error_text: str) -> RunRecord:
        record = self._require_run(run_id)
        if record.status != "running":
            self.db.rollback()
            return record
        now = utc_now()
        record.status = "interrupted"
        record.error_text = error_text
        record.updated_at = now
        record.finished_at = now
        self._flush_refresh_and_commit(record)
        return record

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
                MessageRecord.content.is_not(None),
            )
            .order_by(MessageRecord.created_at.desc(), MessageRecord.id.desc())
        )
        selected: list[MessageRecord] = []
        characters = 0
        for message in self.db.scalars(statement):
            content = message.content
            if content is None or not content.strip():
                continue
            if message.tool_calls_json is not None:
                try:
                    calls = json.loads(message.tool_calls_json)
                except (json.JSONDecodeError, TypeError):
                    continue
                if not any(
                    isinstance(call, dict)
                    and isinstance(call.get("function"), dict)
                    and call["function"].get("name") == "finish_task"
                    for call in calls
                ):
                    continue
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

    def _require_agent_execution(self, execution_id: str) -> AgentExecutionRecord:
        record = self.db.get(AgentExecutionRecord, execution_id)
        if record is None:
            raise ValueError("Agent execution not found")
        return record

    def _require_agent_task(self, task_id: str) -> AgentTaskRecord:
        record = self.db.get(AgentTaskRecord, task_id)
        if record is None:
            raise ValueError("Agent task not found")
        return record

    def _flush_refresh_and_commit(self, record: object) -> None:
        self.db.flush()
        self.db.refresh(record)
        self.db.commit()

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
    def _tool_call_detail(
        record: ToolCallRecord, agent_execution_id: str | None = None
    ) -> ToolCallDetail:
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
            agent_execution_id=agent_execution_id,
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

    @staticmethod
    def _agent_execution_detail(record: AgentExecutionRecord) -> AgentExecutionDetail:
        return AgentExecutionDetail(
            id=record.id,
            run_id=record.run_id,
            parent_execution_id=record.parent_execution_id,
            role=record.role,
            task=record.task,
            status=record.status,
            step_count=record.step_count,
            final_result_json=record.final_result_json,
            started_at=record.started_at,
            finished_at=record.finished_at,
        )

    @staticmethod
    def _agent_task_detail(record: AgentTaskRecord) -> AgentTaskDetail:
        dependencies = json.loads(record.depends_on_json)
        return AgentTaskDetail(
            id=record.id,
            run_id=record.run_id,
            execution_id=record.execution_id,
            role=record.role,
            description=record.description,
            expected_output=record.expected_output,
            depends_on=tuple(dependencies),
            status=record.status,
            result_json=record.result_json,
            created_at=record.created_at,
            started_at=record.started_at,
            finished_at=record.finished_at,
        )
