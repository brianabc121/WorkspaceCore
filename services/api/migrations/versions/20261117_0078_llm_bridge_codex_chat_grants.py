"""MCP 管理器：原来只给了 ChatGPT 的实例改成整个 Codex（T-0282）

迁移 0077（T-0280）把实例授权里只给了 'chatgpt' 的换成了 'codex:chat'（只能和 Codex 对话）。
人类 2026-11-17 决定：这些实例也要能用 Codex 的开发模式，所以这里把授权里的 'codex:chat'
换成 'codex'，去重，保留原来的顺序。只改数据，不改表结构。

- 新建开发会话还要管理员给这个实例勾开发环境（environment_ids 为空就一个都不能用）；
  读、续发别人的开发会话还要有“接手”权限。这两项都不在这里改。
- 'codex:chat' 只在 0077 和 0077 之后的授权页面里出现过。从 0077 升级到这里之间，
  管理员在页面上特意选的 “Codex（只能对话）” 也会一起换成 'codex'，要限制的话在页面上重新选。
- 已经跑过 0077 的库也靠这一步改过来，所以不改 0077 本身。

downgrade 不做任何事：分不出哪些 codex 原来是 codex:chat。

Revision ID: 0078
Revises: 0077
Create Date: 2026-11-17
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0078"
down_revision: str | None = "0077"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 按第一次出现的位置排序，去重
    op.execute(
        """
        UPDATE workspace.llm_bridge_grants AS t
        SET platforms = (
            SELECT coalesce(jsonb_agg(x.platform ORDER BY x.first), '[]'::jsonb)
            FROM (
                SELECT CASE WHEN e.value = 'codex:chat' THEN 'codex' ELSE e.value END
                           AS platform,
                       min(e.ordinality) AS first
                FROM jsonb_array_elements_text(t.platforms)
                     WITH ORDINALITY AS e(value, ordinality)
                GROUP BY 1
            ) AS x
        )
        WHERE jsonb_typeof(t.platforms) = 'array' AND t.platforms ? 'codex:chat'
        """
    )


def downgrade() -> None:
    pass
