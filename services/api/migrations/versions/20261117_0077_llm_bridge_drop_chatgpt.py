"""MCP 管理器：去掉单列的 “ChatGPT” 平台，并入 Codex（T-0280）

原来单列的 “ChatGPT 聊天” 其实就是 Codex app-server 的纯聊天通道（codex.chat），人类 2026-11-17
决定不再单列，只留 Codex。只改数据，不改表结构：

- llm_bridge_sessions（会话索引）、llm_bridge_chat_group_members（群聊成员）：
  platform 'chatgpt' → 'codex'（模式不变，还是 chat；会话编号、内容都在 MCP 管理器上，不受影响）；
- llm_bridge_installs（安装记录）、llm_bridge_deploy_jobs（部署任务）：管理器控制的平台列表里的
  'chatgpt' 换成 'codex'，去重，保留原来的顺序；
- llm_bridge_grants（实例的授权）：原来的 ChatGPT 只能聊天，所以 'chatgpt' 换成 'codex:chat'
  （只能和 Codex 对话，不能用开发模式，也碰不到别人的开发会话），不放宽成整个 Codex；
  已经给了 'codex' 的，'chatgpt' 直接去掉（整个 Codex 已经包括对话）。去重，保留顺序。

downgrade 不做任何事：分不出哪些 codex 原来是 chatgpt；旧代码遇到 codex:chat 当成不认识的平台，
只会更严，不会放宽。

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
# 表 → 'chatgpt' 换成什么（SQL 表达式；t 是这一行）
LIST_COLUMNS = {
    "llm_bridge_installs": "'codex'",
    "llm_bridge_deploy_jobs": "'codex'",
    "llm_bridge_grants": "CASE WHEN t.platforms ? 'codex' THEN 'codex' ELSE 'codex:chat' END",
}


def upgrade() -> None:
    for table in TEXT_COLUMNS:
        op.execute(
            f"UPDATE {SCHEMA}.{table} SET platform = 'codex' WHERE platform = 'chatgpt'"  # noqa: S608
        )
    for table, replacement in LIST_COLUMNS.items():
        # 按第一次出现的位置排序，去重
        op.execute(
            f"""
            UPDATE {SCHEMA}.{table} AS t
            SET platforms = (
                SELECT coalesce(jsonb_agg(x.platform ORDER BY x.first), '[]'::jsonb)
                FROM (
                    SELECT CASE WHEN e.value = 'chatgpt' THEN {replacement} ELSE e.value END
                               AS platform,
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
