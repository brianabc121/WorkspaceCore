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
from typing import Any

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine.reflection import Inspector

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


def _verify_columns(
    inspector: Inspector,
    table: str,
    expected: dict[str, tuple[type[Any], bool, str | None]],
) -> None:
    columns = {c["name"]: c for c in inspector.get_columns(table, schema="workspace")}
    for name, (kind, nullable, default) in expected.items():
        c = columns.get(name)
        actual_default = c.get("default") if c is not None else None
        if isinstance(actual_default, str):
            # PostgreSQL quotes this schema name when deparsing regclass defaults.
            actual_default = actual_default.replace('"workspace".', "workspace.")
        if (
            c is None
            or not isinstance(c["type"], kind)
            or c["nullable"] != nullable
            or actual_default != default
            or (isinstance(c["type"], sa.DateTime) and not c["type"].timezone)
        ):
            raise RuntimeError(f"partial legacy schema; incompatible column {table}.{name}")


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
    if all(gateway):
        for table, sequence, low, high in (
            ("runner_ingress_rules", "runner_ingress_port_seq", 30000, 39999),
            ("runner_published_endpoints", "runner_public_port_seq", 20000, 29999),
        ):
            _verify_columns(
                inspector,
                table,
                {"gateway_port": (sa.Integer, False, f"nextval('workspace.{sequence}'::regclass)")},
            )
            unique = next(
                c
                for c in inspector.get_unique_constraints(table, schema="workspace")
                if c["name"] == f"uq_{table}_gateway_port"
            )
            check = next(
                c
                for c in inspector.get_check_constraints(table, schema="workspace")
                if c["name"] == f"ck_{table}_gateway_port_range"
            )
            expression = "".join(check["sqltext"].lower().split()).replace("(", "").replace(")", "")
            if unique["column_names"] != ["gateway_port"] or expression not in (
                f"gateway_port>={low}andgateway_port<={high}",
                f"gateway_portbetween{low}and{high}",
            ):
                raise RuntimeError("partial legacy gateway schema; incompatible constraint")
        for name, low, high in (
            ("runner_ingress_port_seq", 30000, 39999),
            ("runner_public_port_seq", 20000, 29999),
            ("runner_network_policy_revision_seq", 1, 9223372036854775807),
        ):
            row = bind.execute(
                sa.text(
                    "SELECT seqtypid::regtype::text, seqincrement, seqmin, seqmax, seqcycle "
                    "FROM pg_sequence WHERE seqrelid=to_regclass(:name)"
                ),
                {"name": "workspace." + name},
            ).first()
            if row is None or tuple(row) != ("bigint", 1, low, high, False):
                raise RuntimeError("partial legacy gateway schema; incompatible sequence")
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
        common = {
            "workspace_id": (sa.Uuid, False, None),
            "enabled": (sa.Boolean, False, "false"),
            "updated_at": (sa.DateTime, False, "now()"),
        }
        _verify_columns(
            inspector,
            "llm_bridge_bridges",
            {
                **common,
                "id": (sa.Uuid, False, "gen_random_uuid()"),
                "name": (sa.Text, False, None),
                "base_url": (sa.Text, False, None),
                "token_ref": (sa.Text, False, "''::text"),
                "enabled": (sa.Boolean, False, "true"),
                "created_at": (sa.DateTime, False, "now()"),
            },
        )
        _verify_columns(
            inspector,
            "llm_bridge_grants",
            {
                **common,
                "instance_id": (sa.Uuid, False, None),
                "can_write": (sa.Boolean, False, "false"),
                "can_account": (sa.Boolean, False, "false"),
                "can_takeover": (sa.Boolean, False, "false"),
                "platforms": (postgresql.JSONB, False, "'[]'::jsonb"),
            },
        )
        for table, pk in (("llm_bridge_bridges", "id"), ("llm_bridge_grants", "instance_id")):
            if inspector.get_pk_constraint(table, schema="workspace")["constrained_columns"] != [
                pk
            ]:
                raise RuntimeError("partial legacy bridge schema; incompatible primary key")
            foreign = {
                (
                    tuple(c["constrained_columns"]),
                    c["referred_schema"],
                    c["referred_table"],
                    tuple(c["referred_columns"]),
                    c["options"].get("ondelete"),
                )
                for c in inspector.get_foreign_keys(table, schema="workspace")
            }
            required = {(("workspace_id",), "workspace", "workspaces", ("id",), "CASCADE")}
            if table == "llm_bridge_grants":
                required.add((("instance_id",), "workspace", "agent_instances", ("id",), "CASCADE"))
            if not required <= foreign:
                raise RuntimeError("partial legacy bridge schema; incompatible foreign key")
        if not any(
            set(c["column_names"]) == {"workspace_id", "name"}
            for c in inspector.get_unique_constraints("llm_bridge_bridges", schema="workspace")
        ):
            raise RuntimeError("partial legacy bridge schema; missing uniqueness")
    if not any(gateway) and not any(bridge):
        raise RuntimeError("unknown legacy 0066 state; neither historical schema is present")
    return all(gateway), all(bridge)


def upgrade() -> None:
    if context.is_offline_mode():
        raise RuntimeError("legacy 0066 reconciliation requires an online database")
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
    bind = op.get_bind()
    if bind.scalar(
        sa.text(
            "SELECT EXISTS(SELECT 1 FROM workspace.llm_bridge_grants) "
            "OR EXISTS(SELECT 1 FROM workspace.llm_bridge_bridges)"
        )
    ):
        raise RuntimeError("bridge data exists; refusing destructive downgrade")
    op.drop_table("llm_bridge_grants", schema="workspace")
    op.drop_table("llm_bridge_bridges", schema="workspace")
