"""部署好 TrendRadar 后默认打开新闻早报（T-0225）：部署脚本调用 `connect news-enable`。

真实 PostgreSQL + Redis，模拟 TrendRadar（tests/test_news_api.py 的环境）。覆盖：
- 还没保存过设置时按默认设置打开（只抓取热榜，没有推送渠道与大模型），生成配置文件、写审计；
- 再运行不改动；保存过的设置（包括网页上关掉的）不动；
- 别的工作空间打开了、模块在应用中心停用、没部署 TrendRadar 时都不打开；
- 只有 owner 能打开（与网页一样）。
"""

import argparse
import uuid
from typing import Any

import pytest

from app.commands import connect
from app.modules.news.service import NewsService
from tests.helpers import OWNER, add_member, add_user, add_workspace, run_sql
from tests.test_im_accounts_callbacks import ImEnv
from tests.test_news_api import conf, env, fake_dns, save, settings, trendradar  # noqa: F401

pytestmark = pytest.mark.integration


async def news_enable(env: ImEnv, *argv: str) -> None:  # noqa: F811
    """运行 `connect [选项] news-enable`（部署脚本的调用方式）。"""
    namespace: argparse.Namespace = connect.parser().parse_args([*argv, "news-enable"])
    app = env.app
    async with app.state.sessionmaker() as db:
        cli = connect.Env(app.state.settings, db, app.state.connectors, app.state.redis, None)  # type: ignore[arg-type]
        assert await connect.cmd_news_enable(cli, namespace) == 0


async def run(env: ImEnv, capsys: pytest.CaptureFixture[str], *argv: str) -> str:  # noqa: F811
    """运行一次，返回它输出的 news=<结果> 里的结果。"""
    await news_enable(env, *argv)
    out = capsys.readouterr().out.strip()
    assert out.startswith("news="), out
    return out.removeprefix("news=")


async def test_enables_with_defaults_once(
    env: ImEnv,  # noqa: F811
    capsys: pytest.CaptureFixture[str],
) -> None:
    before: dict[str, Any] = await settings(env)
    assert before["enabled"] is False and before["version"] == 0

    assert await run(env, capsys) == "enabled_now"
    after = await settings(env)
    assert after["enabled"] is True and after["version"] == 1
    # 默认设置：默认平台与主题、07:30，没有推送渠道与大模型
    assert after["platforms"] == before["platforms"] and after["platforms"]
    assert [t["name"] for t in after["topics"]] == [t["name"] for t in before["topics"]]
    assert after["push_times"] == ["07:30"] and after["ai_model"] == ""
    assert not any(c["enabled"] for c in after["channels"].values())
    # 配置文件开始抓取热榜（推送渠道要到网页上填）
    config = conf(env)
    assert config["platforms"]["enabled"] is True
    assert config["notification"]["enabled"] is True
    audit = await run_sql(
        env.database_url,
        "SELECT count(*) FROM workspace.audit_logs WHERE action = 'news.settings_updated'",
    )
    assert audit == 1

    # 再运行一次：不改动
    assert await run(env, capsys) == "enabled"
    assert (await settings(env))["version"] == 1


async def test_keeps_saved_settings_off(
    env: ImEnv,  # noqa: F811
    capsys: pytest.CaptureFixture[str],
) -> None:
    # 网页上保存过（关着）：部署脚本不再打开
    await save(env, enabled=False)
    version = (await settings(env))["version"]
    assert await run(env, capsys) == "kept_off"
    current = await settings(env)
    assert current["enabled"] is False and current["version"] == version


async def test_other_workspace_already_enabled(
    env: ImEnv,  # noqa: F811
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert await run(env, capsys) == "enabled_now"
    other = await add_workspace(env.database_url, env.owner_id, "另一个")
    # 管理多个工作空间时要指定 --workspace；一个部署只能有一个工作空间打开早报
    with pytest.raises(connect.Usage):
        await news_enable(env)
    assert await run(env, capsys, "--workspace", str(other)) == "taken"
    theirs = await settings(env, headers={**env.headers(), "X-Workspace-Id": str(other)})
    assert theirs["enabled"] is False and theirs["version"] == 0


async def test_module_disabled_and_not_deployed(
    env: ImEnv,  # noqa: F811
    capsys: pytest.CaptureFixture[str],
) -> None:
    await run_sql(
        env.database_url,
        "INSERT INTO workspace.workspace_modules(workspace_id, module_key, enabled) "
        "VALUES ($1, 'news', false) "
        "ON CONFLICT (workspace_id, module_key) DO UPDATE SET enabled = false",
        uuid.UUID(env.workspace_id),
    )
    assert await run(env, capsys) == "module_disabled"
    rows = await run_sql(env.database_url, "SELECT count(*) FROM workspace.news_settings")
    assert rows == 0

    app = env.app
    async with app.state.sessionmaker() as db:
        undeployed = app.state.settings.model_copy(update={"trendradar_mcp_url": ""})
        service = NewsService(db, undeployed, app.state.connectors)
        actor, access = await connect.resolve(db, None, None)
        assert await service.enable_by_default(actor, access) == "not_deployed"


async def test_only_owner_can_enable(
    env: ImEnv,  # noqa: F811
    capsys: pytest.CaptureFixture[str],
) -> None:
    user_id = await add_user(env.database_url, "admin@example.com", "admin password 1")
    await add_member(env.database_url, uuid.UUID(env.workspace_id), user_id, "admin")
    with pytest.raises(connect.Usage):
        await news_enable(env, "--email", "admin@example.com")
    assert (await settings(env))["enabled"] is False
    assert await run(env, capsys, "--email", OWNER["email"]) == "enabled_now"
