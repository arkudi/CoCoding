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
