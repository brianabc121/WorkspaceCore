"""大模型会话桥模块的表（docs/v2/LLM_BRIDGE_PLAN.md 第 11 节）。

- llm_bridge_bridges：工作空间登记的会话桥（一座桥 = 一个独立部署的容器 = 每个平台一个账号）。
  管理令牌用凭证密钥加密后存引用，不回传浏览器。
- llm_bridge_grants：按实例的权限（例外九）：没有这一行或 enabled 为 false 的实例
  看不到也调用不了工具。
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class LlmBridge(Base):
    __tablename__ = "llm_bridge_bridges"
    __table_args__ = (UniqueConstraint("workspace_id", "name"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.workspaces.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(Text)
    base_url: Mapped[str] = mapped_column(Text)
    token_ref: Mapped[str] = mapped_column(Text, server_default=text("''"))
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class LlmBridgeGrant(Base):
    __tablename__ = "llm_bridge_grants"

    instance_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.agent_instances.id", ondelete="CASCADE"), primary_key=True
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.workspaces.id", ondelete="CASCADE")
    )
    # 总开关：打开后实例能看到工具、能查看自己的会话
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    # 新建会话、发送、取消、归档
    can_write: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    # 看到账号下全部会话的标题等（不含内容）
    can_account: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    # 读取、续发不属于自己的会话
    can_takeover: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    # 允许的平台；空列表表示都允许
    platforms: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
