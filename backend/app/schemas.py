from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class SessionCreate(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    workspace_path: str = Field(min_length=1, max_length=1024)


class SessionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    workspace_path: str
    status: str
    created_at: datetime
    updated_at: datetime
