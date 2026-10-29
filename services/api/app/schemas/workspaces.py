"""工作空间与成员角色（docs/DEVELOPMENT_SPEC.md 第 5.1、9.2 节）。"""

from datetime import datetime
from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, Field, StringConstraints


class Role(StrEnum):
    """权限由高到低：owner > admin > member > viewer。"""

    owner = "owner"
    admin = "admin"
    member = "member"
    viewer = "viewer"


WorkspaceName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)
]


class WorkspaceOut(BaseModel):
    id: UUID
    name: str
    role: Role = Field(description="当前用户在该工作空间的角色")
    created_at: datetime
    updated_at: datetime


class WorkspaceUpdate(BaseModel):
    name: WorkspaceName
