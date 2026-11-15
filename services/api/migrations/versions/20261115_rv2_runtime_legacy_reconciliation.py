"""Reconcile historical runtime0068 with published main0068 without deleting data.

Main keeps bridge0066 -> gateway0067 -> scopes0068 -> chat0069 -> installs0070.
The stable runtime revision avoids consuming main's next sequential number.
Old runtime0068 lacks bridge scopes; add that column after validating history.
Old PR0069 is classified transactionally in env.py before canonical DDL runs.
"""

from migrations.runtime_revision_compat import validate_canonical_runtime_history
from migrations.runtime_schema_compat_0068 import reconcile_published_0068

revision = "rv2_20261115"
down_revision = "0070"
branch_labels = None
depends_on = None


def upgrade() -> None:
    reconcile_published_0068()
    validate_canonical_runtime_history()


def downgrade() -> None:
    # The canonical history remains intact. 0068/0066 still protect grant/bridge data.
    pass
