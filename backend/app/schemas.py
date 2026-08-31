from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SessionCreate(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    workspace_path: str = Field(min_length=1, max_length=1024)

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Title must not be blank")
        return normalized


class SessionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    workspace_path: str
    status: str
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class RunCreate(BaseModel):
    prompt: str = Field(min_length=1, max_length=20_000)
    max_steps: int = Field(default=20, ge=1, le=50)

    @field_validator("prompt")
    @classmethod
    def normalize_prompt(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Prompt must not be blank")
        return normalized


class _TimestampedRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    @field_validator("*", mode="after", check_fields=False)
    @classmethod
    def normalize_timestamps(cls, value: object) -> object:
        if isinstance(value, datetime):
            if value.tzinfo is None or value.utcoffset() is None:
                return value.replace(tzinfo=timezone.utc)
            return value.astimezone(timezone.utc)
        return value


class MessageRead(_TimestampedRead):
    id: str
    run_id: str
    session_id: str
    role: str
    content: str | None
    tool_calls_json: str | None
    tool_call_id: str | None
    created_at: datetime


class ToolCallRead(_TimestampedRead):
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


class FileChangeRead(_TimestampedRead):
    id: str
    run_id: str
    relative_path: str = Field(validation_alias="path")
    operation: str
    before_hash: str | None
    after_hash: str
    unified_diff: str
    created_at: datetime


class RunRead(_TimestampedRead):
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
    messages: tuple[MessageRead, ...]
    tool_calls: tuple[ToolCallRead, ...]
    file_changes: tuple[FileChangeRead, ...]
