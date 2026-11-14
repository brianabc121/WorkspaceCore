"""Reconcile historical runtime0068 with published main0068 without deleting data.

Main keeps bridge0066 -> gateway0067 -> grant-scopes0068. The acceptance branch
already stamped 0068 before grant scopes existed; validate and add only that column.
"""

from migrations.runtime_schema_compat_0068 import reconcile_published_0068

revision = "0069"
down_revision = "0068"
branch_labels = None
depends_on = None


def upgrade() -> None:
    reconcile_published_0068()


def downgrade() -> None:
    # Canonical 0068 has both schemas and grant scopes. 0068/0066 protect data loss.
    pass
