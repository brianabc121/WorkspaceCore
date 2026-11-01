"""MCP 工具调用记录与审批（V2 OC-5，契约 docs/decisions/2026-10-31-v2-oc1-contract.md 第 6、9 节）。

每次工具调用一行：读工具直接是 succeeded / failed，权限不够是 denied；写工具与高危工具先是
pending_approval（没有执行），之后 rejected / expired，或 executing → succeeded / failed / unknown。
- arguments 是规范化后的完整参数，执行时就用它（AI 之后改不了），不对外返回；只在待审批期间保存，
  处理完（执行结束、拒绝、过期）就清空；工作流里标为敏感的参数值用凭证密钥加密后再存；
  读工具与 denied 记录不保存；
- arguments_display 是脱敏后给人看的版本（敏感参数换成 ***），长期保留；
- arguments_hash 用于去重：配置了凭证密钥时是 HMAC（敏感参数的取值不能从哈希反推），否则是 SHA-256；
- target_version：修改、完成、删除任务与删除日程时，创建审批那一刻目标的版本；执行时版本已变就失败，
  不覆盖别人的修改；
- 同一实例、同一工具、同样参数同时只有一条待审批（部分唯一索引，并发请求也只会建一条）；
- version 是乐观并发版本号，审批接口的 If-Match 就是它。
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

RISK_LEVELS = ("read", "write", "critical")
EXECUTION_STATUSES = (
    "pending_approval",
    "rejected",
    "expired",
    "executing",
    "succeeded",
    "failed",
    "unknown",
    "denied",
)


class AiToolExecution(Base):
    __tablename__ = "ai_tool_executions"
    __table_args__ = (
        CheckConstraint(f"risk_level IN {RISK_LEVELS}", name="risk_level"),
        CheckConstraint(f"status IN {EXECUTION_STATUSES}", name="status"),
        Index("ix_ai_tool_executions_workspace_created", "workspace_id", "created_at"),
        Index("ix_ai_tool_executions_acting_user", "acting_user_id", "created_at"),
        Index(
            "ix_ai_tool_executions_pending_expiry",
            "expires_at",
            postgresql_where=text("status = 'pending_approval'"),
        ),
        Index(
            "uq_ai_tool_executions_pending_args",
            "instance_id",
            "tool_name",
            "arguments_hash",
            unique=True,
            postgresql_where=text("status = 'pending_approval'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.workspaces.id", ondelete="CASCADE")
    )
    instance_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.agent_instances.id", ondelete="SET NULL")
    )
    # 实例被删除后仍能看出是谁发起的
    instance_name: Mapped[str] = mapped_column(Text)
    acting_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.users.id", ondelete="SET NULL")
    )
    tool_name: Mapped[str] = mapped_column(Text)
    risk_level: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text)
    arguments: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    arguments_display: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb")
    )
    arguments_hash: Mapped[str] = mapped_column(Text)
    target_version: Mapped[str | None] = mapped_column(Text)
    source: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.users.id", ondelete="SET NULL")
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_reason: Mapped[str | None] = mapped_column(Text)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result_summary: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(Text)
    request_id: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
