"""Retire workflow/account storage (T-0149); downgrade restores empty structures.

Back up before deployment: configuration, run history and stored credentials are
permanently removed. Historical migrations stay unchanged for upgrade compatibility.
Revisions 0045/0046 are reserved by concurrent multi-instance work, so this uses 0047.

Revision ID: 0047
Revises: 0044
"""

from collections.abc import Sequence
from importlib import import_module

from alembic import op

revision: str = "0047"
down_revision: str | None = "0044"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "DELETE FROM workspace.idempotency_requests "
        "WHERE context_json->>'operation' = 'automation.trigger'"
    )
    for table in ("automation_runs", "automations", "automation_connections", "app_logins"):
        op.drop_table(table, schema="workspace")


def downgrade() -> None:
    import_module("migrations.versions.20261031_0010_automations").upgrade()
    import_module("migrations.versions.20261102_0019_app_logins").upgrade()
    op.drop_constraint(op.f("ck_app_logins_app"), "app_logins", schema="workspace", type_="check")
    op.create_check_constraint(
        op.f("ck_app_logins_app"), "app_logins", "app = 'n8n'", schema="workspace"
    )
