"""迁移 0077（T-0280）：去掉单列的 ChatGPT 平台，库里的 chatgpt 改成 codex。"""

import asyncio
import json
from typing import Any
from uuid import uuid4

import asyncpg
import pytest
from alembic import command

from tests.helpers import alembic_config, asyncpg_dsn

pytestmark = pytest.mark.integration


async def _run(url: str, *statements: tuple[Any, ...]) -> None:
    conn = await asyncpg.connect(asyncpg_dsn(url))
    try:
        for sql, *args in statements:
            await conn.execute(sql, *args)
    finally:
        await conn.close()


async def _fetch(url: str, sql: str) -> list[asyncpg.Record]:
    conn = await asyncpg.connect(asyncpg_dsn(url))
    try:
        return list(await conn.fetch(sql))
    finally:
        await conn.close()


def test_chatgpt_becomes_codex_in_every_stored_place(temp_database_url: str) -> None:
    config = alembic_config(temp_database_url)
    command.upgrade(config, "0076")
    user, workspace, bridge, group = uuid4(), uuid4(), uuid4(), uuid4()
    chat_only, mixed, untouched = uuid4(), uuid4(), uuid4()
    asyncio.run(
        _run(
            temp_database_url,
            (
                "INSERT INTO workspace.users(id,email,password_hash,display_name) "
                "VALUES($1,'llm@example.test','synthetic','LLM')",
                user,
            ),
            (
                "INSERT INTO workspace.workspaces(id,name,owner_id) VALUES($1,'A',$2)",
                workspace,
                user,
            ),
            (
                "INSERT INTO workspace.llm_bridge_bridges(id,workspace_id,name,base_url) "
                "VALUES($1,$2,'主账号','http://bridge.test:8787')",
                bridge,
                workspace,
            ),
            *(
                (
                    "INSERT INTO workspace.llm_bridge_sessions"
                    "(workspace_id,bridge_id,session_id,platform,mode,status,origin,owner_kind,"
                    "created_at,updated_at) "
                    "VALUES($1,$2,$3,$4,$5,'active','bridge','user',now(),now())",
                    workspace,
                    bridge,
                    session_id,
                    platform,
                    mode,
                )
                for session_id, platform, mode in (
                    ("bs_chat", "chatgpt", "chat"),
                    ("bs_dev", "codex", "dev"),
                    ("bs_claude", "claude", "chat"),
                )
            ),
            (
                "INSERT INTO workspace.llm_bridge_chat_groups(id,workspace_id,title,request_id) "
                "VALUES($1,$2,'比一比','g-1')",
                group,
                workspace,
            ),
            *(
                (
                    "INSERT INTO workspace.llm_bridge_chat_group_members"
                    "(group_id,position,bridge_id,bridge_name,platform,mode) "
                    "VALUES($1,$2,$3,'主账号',$4,'chat')",
                    group,
                    position,
                    bridge,
                    platform,
                )
                for position, platform in enumerate(("chatgpt", "claude"))
            ),
            *(
                (
                    "INSERT INTO workspace.agent_instances"
                    "(id,workspace_id,name,network_mode,im_user_id,im_nickname) "
                    "VALUES($1,$2,$3,'intranet',$4,$3)",
                    instance,
                    workspace,
                    name,
                    f"agent_{name}",
                )
                for instance, name in ((chat_only, "甲"), (mixed, "乙"), (untouched, "丙"))
            ),
            *(
                (
                    "INSERT INTO workspace.llm_bridge_grants"
                    "(instance_id,workspace_id,enabled,platforms) VALUES($1,$2,true,$3::jsonb)",
                    instance,
                    workspace,
                    json.dumps(platforms),
                )
                for instance, platforms in (
                    (chat_only, ["chatgpt"]),
                    (mixed, ["claude", "chatgpt", "codex", "workbuddy"]),
                    (untouched, []),
                )
            ),
            (
                "INSERT INTO workspace.llm_bridge_deploy_jobs"
                "(workspace_id,kind,target_kind,server_name,name,slot,request_id,platforms) "
                "VALUES($1,'deploy','agent_server','服务器','主账号','abc123def456','d-1',$2::jsonb)",
                workspace,
                json.dumps(["chatgpt", "codex", "claude"]),
            ),
        )
    )

    command.upgrade(config, "0077")
    sessions = asyncio.run(
        _fetch(
            temp_database_url,
            "SELECT session_id, platform, mode FROM workspace.llm_bridge_sessions",
        )
    )
    assert {r["session_id"]: (r["platform"], r["mode"]) for r in sessions} == {
        "bs_chat": ("codex", "chat"),
        "bs_dev": ("codex", "dev"),
        "bs_claude": ("claude", "chat"),
    }
    members = asyncio.run(
        _fetch(
            temp_database_url,
            "SELECT platform FROM workspace.llm_bridge_chat_group_members ORDER BY position",
        )
    )
    assert [r["platform"] for r in members] == ["codex", "claude"]
    grants = asyncio.run(
        _fetch(temp_database_url, "SELECT instance_id, platforms FROM workspace.llm_bridge_grants")
    )
    # 换名、去重、保留原来的顺序；空列表（都允许）不动
    assert {r["instance_id"]: json.loads(r["platforms"]) for r in grants} == {
        chat_only: ["codex"],
        mixed: ["claude", "codex", "workbuddy"],
        untouched: [],
    }
    jobs = asyncio.run(
        _fetch(temp_database_url, "SELECT platforms FROM workspace.llm_bridge_deploy_jobs")
    )
    assert [json.loads(r["platforms"]) for r in jobs] == [["codex", "claude"]]

    # downgrade 不改数据；再升级一次也不变
    command.downgrade(config, "0076")
    command.upgrade(config, "head")
    again = asyncio.run(
        _fetch(temp_database_url, "SELECT platform FROM workspace.llm_bridge_sessions")
    )
    assert sorted(r["platform"] for r in again) == ["claude", "codex", "codex"]
