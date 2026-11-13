"""大模型会话桥模块（T-0228，docs/v2/LLM_BRIDGE_PLAN.md 第 11 节）

两张新表：llm_bridge_bridges（工作空间登记的会话桥，管理令牌加密保存）、
llm_bridge_grants（按实例的“大模型会话权限”）。只新增，不改已有的表。

downgrade 删掉这两张表。

Revision ID: 0067
Revises: 0066
Create Date: 2026-11-13
"""

from collections.abc import Sequence
from datetime import datetime
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0067"
down_revision: str | None = "0066"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _flag(name: str, default: str = "false") -> sa.Column[bool]:
    return sa.Column(name, sa.Boolean(), server_default=sa.text(default), nullable=False)


def _stamp(name: str) -> sa.Column[datetime]:
    return sa.Column(
        name, sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    )


def _legacy_state() -> tuple[bool, bool]:
    """0066 was historically used by two branches: verify both shapes before writing."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    gateway = [
        bind.scalar(sa.text("SELECT to_regclass(:name)"), {"name": "workspace." + name}) is not None
        for name in (
            "runner_network_policy_revision_seq",
            "runner_ingress_port_seq",
            "runner_public_port_seq",
        )
    ]
    for table in ("runner_ingress_rules", "runner_published_endpoints"):
        gateway.append(
            any(
                c["name"] == "gateway_port"
                for c in inspector.get_columns(table, schema="workspace")
            )
        )
        gateway.append(
            any(
                c["name"] == f"uq_{table}_gateway_port"
                for c in inspector.get_unique_constraints(table, schema="workspace")
            )
        )
        gateway.append(
            any(
                c["name"] == f"ck_{table}_gateway_port_range"
                for c in inspector.get_check_constraints(table, schema="workspace")
            )
        )
    if any(gateway) and not all(gateway):
        raise RuntimeError("partial legacy gateway schema; operator reconciliation required")
    expected = {
        "llm_bridge_bridges": {
            "id",
            "workspace_id",
            "name",
            "base_url",
            "token_ref",
            "enabled",
            "created_at",
            "updated_at",
        },
        "llm_bridge_grants": {
            "instance_id",
            "workspace_id",
            "enabled",
            "can_write",
            "can_account",
            "can_takeover",
            "platforms",
            "updated_at",
        },
    }
    bridge = [inspector.has_table(table, schema="workspace") for table in expected]
    if any(bridge) and not all(bridge):
        raise RuntimeError("partial legacy bridge schema; operator reconciliation required")
    if all(bridge):
        for table, columns in expected.items():
            if {c["name"] for c in inspector.get_columns(table, schema="workspace")} != columns:
                raise RuntimeError("partial legacy bridge schema; unexpected columns")
    return all(gateway), all(bridge)


def upgrade() -> None:
    gateway, bridge = _legacy_state()
    if not gateway:
        # Replay only the immutable historical DDL when the old bridge branch marked
        # itself 0066 without applying the gateway migration. Never reset live sequences.
        spec = spec_from_file_location(
            "historical_gateway_0066",
            Path(__file__).with_name("20261113_0066_runner_gateway_ports.py"),
        )
        if spec is None or spec.loader is None:
            raise RuntimeError("historical gateway migration unavailable")
        module = module_from_spec(spec)
        spec.loader.exec_module(module)
        module.upgrade()
    if bridge:
        return
    op.create_table(
        "llm_bridge_bridges",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=False),
        sa.Column("token_ref", sa.Text(), server_default=sa.text("''"), nullable=False),
        _flag("enabled", "true"),
        _stamp("created_at"),
        _stamp("updated_at"),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.workspaces.id"],
            name=op.f("fk_llm_bridge_bridges_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_llm_bridge_bridges")),
        sa.UniqueConstraint(
            "workspace_id", "name", name=op.f("uq_llm_bridge_bridges_workspace_id_name")
        ),
        schema="workspace",
    )
    op.create_table(
        "llm_bridge_grants",
        sa.Column("instance_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        _flag("enabled"),
        _flag("can_write"),
        _flag("can_account"),
        _flag("can_takeover"),
        sa.Column(
            "platforms",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        _stamp("updated_at"),
        sa.ForeignKeyConstraint(
            ["instance_id"],
            ["workspace.agent_instances.id"],
            name=op.f("fk_llm_bridge_grants_instance_id_agent_instances"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.workspaces.id"],
            name=op.f("fk_llm_bridge_grants_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("instance_id", name=op.f("pk_llm_bridge_grants")),
        schema="workspace",
    )


def downgrade() -> None:
    op.drop_table("llm_bridge_grants", schema="workspace")
    op.drop_table("llm_bridge_bridges", schema="workspace")
