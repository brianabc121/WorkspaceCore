"""MCP 管理器：去掉单列的 “ChatGPT” 平台，并入 Codex（T-0280）

原来单列的 “ChatGPT 聊天” 其实就是 Codex app-server 的纯聊天通道（codex.chat），人类 2026-11-17
决定不再单列，只留 Codex。只改数据，不改表结构：

- llm_bridge_sessions（会话索引）、llm_bridge_chat_group_members（群聊成员）：
  platform 'chatgpt' → 'codex'（模式不变，还是 chat；会话编号、内容都在 MCP 管理器上，不受影响）；
- llm_bridge_grants（实例的授权）、llm_bridge_installs（安装记录）、
  llm_bridge_deploy_jobs（部署任务）：platforms 列表里的 'chatgpt' 换成 'codex'，
  去重，保留原来的顺序。

授权：只勾了 “ChatGPT” 的实例迁移后是 “Codex”。开发模式另外要管理员授权开发环境
（environment_ids 为空就一个都不能用），所以没给环境的实例照样只能聊天。

downgrade 不做任何事：分不出哪些 codex 原来是 chatgpt，旧代码也能把 codex.chat 当成聊天用。

Revision ID: 0077
Revises: 0076
Create Date: 2026-11-17
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0077"
down_revision: str | None = "0076"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "workspace"
TEXT_COLUMNS = ("llm_bridge_sessions", "llm_bridge_chat_group_members")
LIST_COLUMNS = ("llm_bridge_grants", "llm_bridge_installs", "llm_bridge_deploy_jobs")


def upgrade() -> None:
    for table in TEXT_COLUMNS:
        op.execute(
            f"UPDATE {SCHEMA}.{table} SET platform = 'codex' WHERE platform = 'chatgpt'"  # noqa: S608
        )
    for table in LIST_COLUMNS:
        # 按第一次出现的位置排序，去重
        op.execute(
            f"""
            UPDATE {SCHEMA}.{table} AS t
            SET platforms = (
                SELECT coalesce(jsonb_agg(x.platform ORDER BY x.first), '[]'::jsonb)
                FROM (
                    SELECT CASE WHEN e.value = 'chatgpt' THEN 'codex' ELSE e.value END AS platform,
                           min(e.ordinality) AS first
                    FROM jsonb_array_elements_text(t.platforms)
                         WITH ORDINALITY AS e(value, ordinality)
                    GROUP BY 1
                ) AS x
            )
            WHERE jsonb_typeof(t.platforms) = 'array' AND t.platforms ? 'chatgpt'
            """  # noqa: S608
        )


def downgrade() -> None:
    pass
