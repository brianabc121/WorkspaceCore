"""Reconcile both historical 0066/0067 runtime schemas without deleting data.

Main keeps the published bridge0066 -> gateway0067 chain. This fence also runs
for databases already stamped 0067 by the earlier acceptance branch.
"""

from migrations.runtime_schema_compat_0068 import reconcile_legacy_runtime

revision = "0068"
down_revision = "0067"
branch_labels = None
depends_on = None


def upgrade() -> None:
    reconcile_legacy_runtime()


def downgrade() -> None:
    # Canonical 0067 has both schemas. The actual bridge drop in 0066 protects data.
    pass
