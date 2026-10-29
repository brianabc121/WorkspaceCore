"""首次安装、登录、会话与密码（docs/DEVELOPMENT_SPEC.md 第 9.1 节）。

密码与安装凭据使用 SecretStr，避免出现在日志、repr 和错误信息里。
"""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, SecretStr

from app.schemas.users import DisplayName
from app.schemas.workspaces import WorkspaceName

# 新密码长度；上限防止超长输入拖慢 Argon2 计算
NewPassword = Annotated[SecretStr, Field(min_length=12, max_length=128)]
# 登录时不检查下限（历史密码规则可能不同），只限制上限
AnyPassword = Annotated[SecretStr, Field(min_length=1, max_length=128)]


class SetupStatus(BaseModel):
    setup_required: bool = Field(description="还没有任何用户时为 true，此时应显示首次安装页")


class SetupRequest(BaseModel):
    setup_token: Annotated[SecretStr, Field(min_length=1, max_length=256)] = Field(
        description="部署时生成的一次性安装凭据（.env 中的 SETUP_TOKEN）"
    )
    email: EmailStr
    password: NewPassword
    display_name: DisplayName
    workspace_name: WorkspaceName


class LoginRequest(BaseModel):
    email: EmailStr
    password: AnyPassword


class PasswordChangeRequest(BaseModel):
    current_password: AnyPassword
    new_password: NewPassword


class SessionOut(BaseModel):
    id: UUID
    created_at: datetime
    last_seen_at: datetime | None
    expires_at: datetime
    current: bool = Field(description="是否为发出本次请求的会话")
