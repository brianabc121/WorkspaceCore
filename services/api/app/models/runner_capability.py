"""Persistent capability requests and grants; approval arguments are disposable."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

CAPABILITIES = ("runner.unlimited_container_count", "runner.cross_agent_communication")


class RunnerCapabilityRequest(Base):
    __tablename__ = "runner_capability_requests"
    __table_args__ = (CheckConstraint(f"capability IN {CAPABILITIES}", name="capability"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.workspaces.id", ondelete="CASCADE")
    )
    instance_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.agent_instances.id", ondelete="CASCADE")
    )
    approval_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.ai_tool_executions.id"), unique=True
    )
    capability: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    policy_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RunnerCapabilityGrant(Base):
    __tablename__ = "runner_capability_grants"
    __table_args__ = (
        CheckConstraint(f"capability IN {CAPABILITIES}", name="capability"),
        CheckConstraint(
            "state IN ('approved_pending_apply','active','revoked','expired','apply_failed')",
            name="state",
        ),
        Index("ix_runner_capability_grants_scope", "workspace_id", "instance_id", "capability"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.workspaces.id", ondelete="CASCADE")
    )
    instance_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.agent_instances.id", ondelete="CASCADE")
    )
    approval_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.ai_tool_executions.id"), unique=True
    )
    requested_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.users.id", ondelete="SET NULL")
    )
    approved_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspace.users.id"))
    capability: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    scope: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    state: Mapped[str] = mapped_column(Text, default="approved_pending_apply")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    policy_version: Mapped[int] = mapped_column(Integer, default=1)
    desired_revision: Mapped[int] = mapped_column(Integer, default=1)
    applied_revision: Mapped[int] = mapped_column(Integer, default=0)
    approved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    revoked_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.users.id", ondelete="SET NULL")
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoke_reason: Mapped[str | None] = mapped_column(Text)
