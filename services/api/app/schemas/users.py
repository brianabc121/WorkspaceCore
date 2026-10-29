"""当前用户（docs/DEVELOPMENT_SPEC.md 第 5.1 节 users）。"""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, EmailStr, StringConstraints

from app.schemas.workspaces import WorkspaceOut

DisplayName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]


class UserOut(BaseModel):
    id: UUID
    email: EmailStr
    display_name: str
    created_at: datetime


class MeResponse(BaseModel):
    user: UserOut
    workspaces: list[WorkspaceOut]


class MeUpdate(BaseModel):
    display_name: DisplayName
