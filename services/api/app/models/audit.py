"""审计日志（docs/DEVELOPMENT_SPEC.md 第 5.1 节第 23 项）。

只追加：应用层只提供写入与查询，数据库触发器拒绝 UPDATE（迁移 0003）。
metadata 在写入前脱敏；客户端 IP 只保存带密钥的哈希。
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"
    # 按工作空间倒序分页：WHERE workspace_id = ? ORDER BY created_at DESC, id DESC
    __table_args__ = (Index(None, "workspace_id", "created_at", "id"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    # 账户级事件（登录、修改密码等）为用户所在的每个工作空间各写一条；
    # 与任何工作空间都无关的事件（例如不存在的账户登录失败）为 NULL，不通过 API 暴露
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.workspaces.id", ondelete="CASCADE")
    )
    # 不加外键：用户被删除后审计记录仍然保留
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    action: Mapped[str] = mapped_column(Text)
    resource_type: Mapped[str | None] = mapped_column(Text)
    resource_id: Mapped[str | None] = mapped_column(Text)
    metadata_redacted_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb")
    )
    ip_hash: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
