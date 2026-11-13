"""Network declarations are desired policy; saving never implies data-plane success."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

STATES = "('approved_pending_apply','active','revoked','expired','apply_failed')"


class NetworkRecord(Base):
    __abstract__ = True
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    environment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    owner_instance_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    host_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    state: Mapped[str] = mapped_column(Text, default="approved_pending_apply")
    desired_revision: Mapped[int] = mapped_column(Integer, default=1)
    applied_revision: Mapped[int] = mapped_column(Integer, default=0)
    desired_generation: Mapped[int] = mapped_column(Integer, default=0)
    applied_generation: Mapped[int | None] = mapped_column(Integer)
    apply_error: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspace.users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace.users.id", ondelete="SET NULL")
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


def constraints() -> tuple[ForeignKeyConstraint | UniqueConstraint | CheckConstraint, ...]:
    return (
        ForeignKeyConstraint(
            ["workspace_id", "environment_id"],
            ["workspace.runner_environments.workspace_id", "workspace.runner_environments.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "owner_instance_id"],
            ["workspace.agent_instances.workspace_id", "workspace.agent_instances.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "host_id"],
            ["workspace.runner_hosts.workspace_id", "workspace.runner_hosts.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("workspace_id", "environment_id", "id"),
        CheckConstraint(f"state IN {STATES}", name="state"),
        CheckConstraint(
            "desired_revision >= 1 AND applied_revision >= 0 "
            "AND applied_revision <= desired_revision",
            name="revision",
        ),
    )


class RunnerService(NetworkRecord):
    __tablename__ = "runner_services"
    __table_args__ = (
        *constraints(),
        UniqueConstraint("environment_id", "name"),
        CheckConstraint("port BETWEEN 1 AND 65535", name="port"),
        CheckConstraint("protocol IN ('TCP','UDP')", name="protocol"),
    )
    name: Mapped[str] = mapped_column(Text)
    port: Mapped[int] = mapped_column(Integer)
    protocol: Mapped[str] = mapped_column(Text)
    desired_dns: Mapped[str | None] = mapped_column(Text)


class RunnerIngressRule(NetworkRecord):
    __tablename__ = "runner_ingress_rules"
    __table_args__ = (
        *constraints(),
        ForeignKeyConstraint(
            ["workspace_id", "environment_id", "service_id"],
            [
                "workspace.runner_services.workspace_id",
                "workspace.runner_services.environment_id",
                "workspace.runner_services.id",
            ],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "source_instance_id"],
            ["workspace.agent_instances.workspace_id", "workspace.agent_instances.id"],
            ondelete="CASCADE",
        ),
        CheckConstraint("expires_at IS NOT NULL", name="finite_lease"),
    )
    service_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    source_instance_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    gateway_port: Mapped[int] = mapped_column(
        Integer,
        unique=True,
        server_default=text("nextval('workspace.runner_ingress_port_seq')"),
    )


class RunnerPublishedEndpoint(NetworkRecord):
    __tablename__ = "runner_published_endpoints"
    __table_args__ = (
        *constraints(),
        ForeignKeyConstraint(
            ["workspace_id", "environment_id", "service_id"],
            [
                "workspace.runner_services.workspace_id",
                "workspace.runner_services.environment_id",
                "workspace.runner_services.id",
            ],
            ondelete="CASCADE",
        ),
        CheckConstraint("expires_at IS NOT NULL", name="finite_lease"),
    )
    service_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    source_cidrs: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    enforcement: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    gateway_port: Mapped[int] = mapped_column(
        Integer,
        unique=True,
        server_default=text("nextval('workspace.runner_public_port_seq')"),
    )
