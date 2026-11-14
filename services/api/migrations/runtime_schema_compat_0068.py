"""Frozen reconciliation for the historical runtime 0066/0067/0068 revision collisions.

Keep independent of evolving application models. Validate the complete old shape
before replaying either historical DDL; existing rows and allocators stay intact.
"""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType
from typing import Any

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine.reflection import Inspector


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
            or (kind is sa.Integer and isinstance(c["type"], (sa.SmallInteger, sa.BigInteger)))
            or c["nullable"] != nullable
            or actual_default != default
            or (isinstance(c["type"], sa.DateTime) and not c["type"].timezone)
        ):
            raise RuntimeError(f"partial legacy schema; incompatible column {table}.{name}")


def _verify_validated_constraints(table: str) -> None:
    invalid = op.get_bind().scalar(
        sa.text(
            "SELECT conname FROM pg_constraint WHERE conrelid=to_regclass(:table) "
            "AND contype IN ('c','f') AND NOT convalidated LIMIT 1"
        ),
        {"table": "workspace." + table},
    )
    if invalid is not None:
        raise RuntimeError(f"partial legacy schema; unvalidated constraint on {table}")


def _legacy_state() -> tuple[bool, bool]:
    """0066 and 0067 have historical meanings: verify both shapes before writing."""
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
            _verify_validated_constraints(table)
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
            _verify_validated_constraints(table)
            actual = {c["name"] for c in inspector.get_columns(table, schema="workspace")}
            if table == "llm_bridge_grants" and "bridge_ids" in actual:
                # Published main0068: exactly nullable JSONB without a default.
                _verify_columns(inspector, table, {"bridge_ids": (postgresql.JSONB, True, None)})
                actual.remove("bridge_ids")
            if actual != columns:
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
        raise RuntimeError("unknown legacy 0066/0067 state; neither historical schema is present")
    return all(gateway), all(bridge)


def _historical(filename: str) -> ModuleType:
    spec = spec_from_file_location(
        "runtime_historical_" + filename.removesuffix(".py"),
        Path(__file__).with_name("versions") / filename,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("historical runtime migration unavailable")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _require_online() -> None:
    if context.is_offline_mode():
        raise RuntimeError("legacy 0066/0067 reconciliation requires an online database")


def reconcile_legacy_runtime() -> None:
    _require_online()
    gateway, bridge = _legacy_state()
    if not gateway:
        _historical("20261113_0067_runner_gateway_ports.py")._create_gateway()
    if not bridge:
        _historical("20261113_0066_llm_bridge.py").upgrade()
    if _legacy_state() != (True, True):
        raise RuntimeError("legacy runtime reconciliation did not produce both schemas")


def refuse_bridge_data_loss() -> None:
    _require_online()
    _, bridge = _legacy_state()
    if not bridge:
        raise RuntimeError("legacy gateway revision must be reconciled before bridge downgrade")
    # Hold the locks through Alembic's transaction, including both subsequent DROPs.
    # A writer must not commit new data after the empty result and lose it on DROP.
    op.execute(
        "LOCK TABLE workspace.llm_bridge_bridges, workspace.llm_bridge_grants "
        "IN ACCESS EXCLUSIVE MODE"
    )
    if op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS(SELECT 1 FROM workspace.llm_bridge_grants) "
            "OR EXISTS(SELECT 1 FROM workspace.llm_bridge_bridges)"
        )
    ):
        raise RuntimeError("bridge data exists; refusing destructive downgrade")


def reconcile_published_0068() -> None:
    """Both published 0068 meanings contain complete bridge and gateway schemas.

    The acceptance branch did not yet include main's bridge access scopes.
    Validate before adding that column; never replay gateway or bridge DDL here.
    """
    _require_online()
    if _legacy_state() != (True, True):
        raise RuntimeError("partial legacy 0068 schema; operator reconciliation required")
    inspector = sa.inspect(op.get_bind())
    if not any(
        c["name"] == "bridge_ids"
        for c in inspector.get_columns("llm_bridge_grants", schema="workspace")
    ):
        _historical("20261113_0068_llm_bridge_grant_bridges.py").upgrade()


def refuse_bridge_scope_loss() -> None:
    _require_online()
    _legacy_state()
    # Prevent a writer committing a restrictive scope between the check and DROP.
    # Alembic holds this lock until the column drop and revision update commit.
    op.execute("LOCK TABLE workspace.llm_bridge_grants IN ACCESS EXCLUSIVE MODE")
    if op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS(SELECT 1 FROM workspace.llm_bridge_grants WHERE bridge_ids IS NOT NULL)"
        )
    ):
        raise RuntimeError("bridge access scopes exist; refusing downgrade that broadens grants")
