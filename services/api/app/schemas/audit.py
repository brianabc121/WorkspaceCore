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


class AuditLogView(AuditLogOut):
    """列表与详情（T-0197）：在原记录之外补充显示名，全部只在当前权限内解析。

    审计没有通用的“结果”字段，也不补前后值或 IP；metadata 是写入时脱敏过的附加信息。
    """

    action_label: str | None = Field(
        default=None, description="内置事件的中文名；未知事件为 null，按 action 原样显示"
    )
    actor_label: str | None = Field(
        default=None,
        description="操作者在当前工作空间的显示名；actor_id 为 null 时是“系统”；"
        "已不是成员（被移出或账号已删）时为 null，按 actor_id 显示",
    )
    resource_current_name: str | None = Field(
        default=None,
        description="对象“现在”的名称，不是记录当时的快照；只在你现在能读这个对象时给出，"
        "对象已删除、不在本工作空间或无权读取时为 null（例如密钥名只有 owner 能看到）",
    )
