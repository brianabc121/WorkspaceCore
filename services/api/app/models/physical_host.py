"""Trusted physical capacity belongs to exactly one server registration."""

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class PhysicalHostRegistration(Base):
    __tablename__ = "physical_host_registrations"
    __table_args__ = (
        CheckConstraint("physical_id ~ '^[a-f0-9]{64}$'", name="physical_id_format"),
        CheckConstraint(
            "(agent_server_id IS NOT NULL) <> (runner_host_id IS NOT NULL)",
            name="one_pool",
        ),
        UniqueConstraint("agent_server_id"),
        UniqueConstraint("runner_host_id"),
        ForeignKeyConstraint(
            ["workspace_id", "agent_server_id"],
            ["workspace.agent_servers.workspace_id", "workspace.agent_servers.id"],
            name="fk_physical_host_agent_scope",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "runner_host_id"],
            ["workspace.runner_hosts.workspace_id", "workspace.runner_hosts.id"],
            name="fk_physical_host_runner_scope",
            ondelete="CASCADE",
        ),
    )

    physical_id: Mapped[str] = mapped_column(Text, primary_key=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace.workspaces.id", ondelete="CASCADE")
    )
    agent_server_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    runner_host_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
