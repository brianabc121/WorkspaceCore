"""审计日志（docs/DEVELOPMENT_SPEC.md 第 5.1 节 audit_logs）。"""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class AuditLogOut(BaseModel):
    id: UUID
    action: str = Field(description="例如 auth.login_succeeded、workspace.updated")
    actor_id: UUID | None = Field(description="执行者；系统事件为 null")
    resource_type: str | None
    resource_id: str | None
    metadata: dict[str, Any] = Field(description="已脱敏的附加信息，不含密码、Token 等")
    created_at: datetime
