"""工作空间成员管理（docs/DEVELOPMENT_SPEC.md 第 9.2 节；T-0064）。

- 只能授予 admin / member / viewer；owner 不能通过这里授予、修改或移除（不支持转让）。
- admin 管理 member / viewer；授予、收回 admin 或移除 admin 只能由 owner 操作。
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field

from app.schemas.auth import NewPassword
from app.schemas.users import DisplayName
from app.schemas.workspaces import Role

AssignableRole = Literal["admin", "member", "viewer"]


class MemberOut(BaseModel):
    user_id: UUID
    email: str
    display_name: str
    role: Role
    joined_at: datetime = Field(description="加入这个工作空间的时间")
    you: bool = Field(description="是否为当前用户")


class MemberCreate(BaseModel):
    """已有账号按邮箱加入；没有账号时同时填写 display_name 与 password，创建账号并加入。"""

    email: EmailStr
    role: AssignableRole
    display_name: DisplayName | None = Field(default=None, description="只在创建新账号时使用")
    password: NewPassword | None = Field(
        default=None, description="新账号的初始密码（至少 12 位）；只在创建新账号时使用"
    )


class MemberUpdate(BaseModel):
    role: AssignableRole
