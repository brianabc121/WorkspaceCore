"""Stable non-recycled TCP gateway ports and monotonically fenced network snapshots.

T-0224 首次合并时编号是 0066，与同时合并的大模型会话桥（0066 llm_bridge，PR #198）撞号，
main 上出现两个 head；改为 0067、接在 0066 之后。两个迁移互不依赖（不同的表）。
"""

import sqlalchemy as sa
from alembic import op

revision = "0067"
down_revision = "0066"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SEQUENCE workspace.runner_network_policy_revision_seq AS BIGINT")
    for table, sequence, low, high in (
        ("runner_ingress_rules", "runner_ingress_port_seq", 30000, 39999),
        ("runner_published_endpoints", "runner_public_port_seq", 20000, 29999),
    ):
        op.execute(
            f"CREATE SEQUENCE workspace.{sequence} START {low} "
            f"MINVALUE {low} MAXVALUE {high} NO CYCLE"
        )
        op.add_column(
            table,
            sa.Column(
                "gateway_port",
                sa.Integer(),
                nullable=False,
                server_default=sa.text(f"nextval('workspace.{sequence}')"),
            ),
            schema="workspace",
        )
        op.create_unique_constraint(
            f"uq_{table}_gateway_port", table, ["gateway_port"], schema="workspace"
        )
        op.create_check_constraint(
            op.f(f"ck_{table}_gateway_port_range"),
            table,
            f"gateway_port BETWEEN {low} AND {high}",
            schema="workspace",
        )


def downgrade() -> None:
    # Keep the declarations and their data. Downgrade is safe only after the gateway is stopped.
    for table, sequence in (
        ("runner_ingress_rules", "runner_ingress_port_seq"),
        ("runner_published_endpoints", "runner_public_port_seq"),
    ):
        op.drop_constraint(op.f(f"ck_{table}_gateway_port_range"), table, schema="workspace")
        op.drop_constraint(f"uq_{table}_gateway_port", table, schema="workspace")
        op.drop_column(table, "gateway_port", schema="workspace")
        op.execute(f"DROP SEQUENCE workspace.{sequence}")
    op.execute("DROP SEQUENCE workspace.runner_network_policy_revision_seq")
