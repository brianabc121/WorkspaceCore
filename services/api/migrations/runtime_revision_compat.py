"""Recognize the exact PR0069 schema without confusing it with canonical chat0069.

No application models: canonical structure is frozen from main d4e0a4b DDL.
Only an exact old PR0069 is relabelled to its equivalent canonical0068, within
Alembic's existing upgrade transaction. Current/check never modify the database.
"""

from typing import Any, cast

import sqlalchemy as sa
from alembic import op
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext, RevisionStep
from sqlalchemy.engine import Connection

from migrations.runtime_canonical_schema import CANONICAL_TABLES
from migrations.runtime_schema_compat_0068 import (
    _legacy_state,
    _verify_validated_constraints,
    reconcile_published_0068,
)


def _without_comments(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _without_comments(v) for k, v in value.items() if k != "comment"}
    if isinstance(value, list):
        return [_without_comments(v) for v in value]
    return value


def _canonical_shape(bind: Connection, table: str) -> dict[str, Any]:
    inspector = sa.inspect(bind)
    return cast(
        dict[str, Any],
        _without_comments(
            {
                "columns": [
                    [
                        c["name"],
                        c["type"].compile(dialect=bind.dialect),
                        c["nullable"],
                        c.get("default"),
                    ]
                    for c in inspector.get_columns(table, schema="workspace")
                ],
                "pk": inspector.get_pk_constraint(table, schema="workspace"),
                "unique": sorted(
                    inspector.get_unique_constraints(table, schema="workspace"),
                    key=lambda c: str(c["name"]),
                ),
                "foreign": sorted(
                    inspector.get_foreign_keys(table, schema="workspace"),
                    key=lambda c: str(c["name"]),
                ),
                "indexes": sorted(
                    inspector.get_indexes(table, schema="workspace"), key=lambda c: c["name"]
                ),
                "checks": inspector.get_check_constraints(table, schema="workspace"),
            }
        ),
    )


def _plain_schema(bind: Connection, table: str, origin: str) -> None:
    columns = sa.inspect(bind).get_columns(table, schema="workspace")
    if any(c.get("computed") is not None or c.get("identity") is not None for c in columns):
        raise RuntimeError(f"incompatible {origin} runtime schema; generated column on {table}")
    deferred = bind.scalar(
        sa.text(
            "SELECT conname FROM pg_constraint WHERE conrelid=to_regclass(:name) "
            "AND (condeferrable OR condeferred) LIMIT 1"
        ),
        {"name": "workspace." + table},
    )
    if deferred is not None:
        raise RuntimeError(
            f"incompatible {origin} runtime schema; deferrable constraint on {table}"
        )


def _validate_tables(bind: Connection, tables: tuple[str, ...]) -> None:
    inspector = sa.inspect(bind)
    for table in tables:
        if not inspector.has_table(table, schema="workspace"):
            raise RuntimeError(f"partial canonical runtime schema; missing {table}")
        _plain_schema(bind, table, "canonical")
        _verify_validated_constraints(table)
        if _canonical_shape(bind, table) != CANONICAL_TABLES[table]:
            raise RuntimeError(f"incompatible canonical runtime schema; {table}")
        invalid_index = bind.scalar(
            sa.text(
                "SELECT indexrelid FROM pg_index WHERE indrelid=to_regclass(:name) "
                "AND (NOT indisvalid OR NOT indisready) LIMIT 1"
            ),
            {"name": "workspace." + table},
        )
        if invalid_index is not None:
            raise RuntimeError(f"incompatible canonical runtime schema; invalid index on {table}")


def validate_canonical_runtime_history() -> None:
    _validate_tables(op.get_bind(), tuple(CANONICAL_TABLES))


def normalize_old_pr0069(migration: MigrationContext) -> None:
    # Read-only commands (current/check/autogenerate) have no destination revision.
    if "destination_rev" not in migration.opts:
        return
    heads = migration.get_current_heads()
    if heads not in (("0068",), ("0069",), ("0070",)):
        return
    bind = migration.bind
    if bind is None:
        raise RuntimeError("legacy runtime classification requires an online database")
    with Operations.context(migration):
        if _legacy_state() != (True, True):
            raise RuntimeError(f"partial legacy {heads[0]} runtime schema")
        inspector = sa.inspect(bind)
        for table in ("llm_bridge_bridges", "llm_bridge_grants"):
            _plain_schema(bind, table, "legacy")
        if heads == ("0068",):
            if any(inspector.has_table(table, schema="workspace") for table in CANONICAL_TABLES):
                raise RuntimeError("partial legacy0068 schema; unexpected canonical tables")
            steps = tuple(migration.opts["fn"](heads, migration))
            if steps and all(isinstance(step, RevisionStep) and step.is_upgrade for step in steps):
                # Complete canonical0068 before ANY later canonical stamp can commit,
                # including explicit0069/0070 targets. The same outer transaction
                # rolls back the scope column if subsequent canonical DDL fails.
                reconcile_published_0068()
            return
        columns = inspector.get_columns("llm_bridge_grants", schema="workspace")
        if not any(c["name"] == "bridge_ids" for c in columns):
            raise RuntimeError("partial legacy PR0069 schema; missing bridge scopes")
        if heads == ("0070",):
            _validate_tables(bind, tuple(CANONICAL_TABLES))
            return
        present = tuple(
            table for table in CANONICAL_TABLES if inspector.has_table(table, schema="workspace")
        )
        if present:
            chat_tables = ("llm_bridge_chat_groups", "llm_bridge_chat_group_members")
            if set(present) != set(chat_tables):
                raise RuntimeError("partial canonical0069 schema; operator reconciliation required")
            _validate_tables(bind, chat_tables)
            return
        # Both published meanings now have unambiguous, fully validated shapes.
        # Inspect the actual Alembic plan to distinguish upgrades from downgrades/stamps.
        steps = tuple(migration.opts["fn"](("0069",), migration))
        if not steps or not all(
            isinstance(step, RevisionStep) and step.is_upgrade for step in steps
        ):
            raise RuntimeError("legacy PR0069 must upgrade before any downgrade or relabelling")
        # This conditional historical alias is in the SAME outer transaction as all
        # subsequent canonical DDL. A failing migration restores version0069 and data.
        bind.execute(
            sa.text("UPDATE alembic_version SET version_num='0068' WHERE version_num='0069'")
        )
