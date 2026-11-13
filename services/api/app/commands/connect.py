"""部署组件的接入状态（部署脚本调用）。

知识库图片存储改在网页“设置 → 图片存储”里配置和自检（T-0223），这里不再有 storage-check。
news-enable：部署好 TrendRadar 后默认打开新闻早报（T-0225）。
"""

import argparse
import asyncio
import sys
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.core.errors import ApiError
from app.core.resources import create_engine, create_redis
from app.domain import im
from app.domain.connectors import ConnectorRuntime
from app.domain.integrations import IntegrationService
from app.domain.policy import WorkspaceAccess, has_role, require_role
from app.integrations.base import ConnectorError
from app.integrations.http import CircuitBreaker
from app.jobs.queue import ArqJobQueue, JobQueue
from app.models import User
from app.modules.news.service import NewsService
from app.repositories import users as user_repo
from app.repositories import workspaces as workspace_repo
from app.schemas.workspaces import Role


class Usage(Exception):
    """用法错误或前提不满足（退出码 2）。"""


class Failed(Exception):
    """接入失败（退出码 1）。"""


@dataclass(frozen=True)
class CliActor:
    """部署脚本代表管理员本人操作：没有会话，也没有客户端 IP（app/domain/auth.py 的 Actor）。"""

    user: User
    client_ip: str | None = None


@dataclass
class Env:
    settings: Settings
    db: AsyncSession
    runtime: ConnectorRuntime
    redis: Redis
    jobs: JobQueue

    def integrations(self) -> IntegrationService:
        return IntegrationService(
            self.db, self.settings, self.runtime, redis=self.redis, jobs=self.jobs
        )


@asynccontextmanager
async def environment() -> AsyncIterator[Env]:
    settings = get_settings()
    engine = create_engine(settings)
    redis = create_redis(settings)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            yield Env(
                settings=settings,
                db=db,
                runtime=ConnectorRuntime(settings, CircuitBreaker()),
                redis=redis,
                jobs=ArqJobQueue(redis),
            )
    finally:
        await redis.aclose()
        await engine.dispose()


# ---------------------------------------------------------------- 公共


async def resolve(
    db: AsyncSession, email: str | None, workspace_id: uuid.UUID | None
) -> tuple[CliActor, WorkspaceAccess]:
    """找到操作者与工作空间，并按网页同样的规则要求 admin 以上角色。"""
    user = await (user_repo.get_by_email(db, email) if email else user_repo.earliest(db))
    if user is None:
        if email:
            raise Usage(f"工作台里没有用户 {email}")
        raise Usage("还没有完成首次安装：先在浏览器里创建管理员")
    if user.status != "active":
        raise Usage(f"用户 {user.email} 已停用")
    if workspace_id is None:
        managed = [
            workspace
            for workspace, role in await workspace_repo.list_for_user(db, user.id)
            if has_role(role, Role.admin)
        ]
        if not managed:
            raise Usage(f"{user.email} 不是任何工作空间的管理员")
        if len(managed) > 1:
            choices = "；".join(f"{w.id}（{w.name}）" for w in managed)
            raise Usage(f"{user.email} 管理多个工作空间，用 --workspace 指定：{choices}")
        workspace_id = managed[0].id
    try:
        access = await require_role(
            db, user_id=user.id, workspace_id=workspace_id, minimum=Role.admin
        )
    except ApiError as error:
        raise Usage(f"不能操作这个工作空间：{error.message}") from None
    return CliActor(user), access


# ---------------------------------------------------------------- 子命令


async def cmd_status(env: Env, args: argparse.Namespace) -> int:
    """key=value 一行一项，供部署脚本解析：setup、github 与 openim。"""
    if await user_repo.count(env.db) == 0:
        print("setup=required")
        print(f"openim={await _openim_state(env)}")
        return 0
    print("setup=done")
    actor, access = await resolve(env.db, args.email, args.workspace)
    print(f"user={actor.user.email}")
    integrations = await env.integrations().list_integrations(access)
    found = next((item for item in integrations if item.provider == "github"), None)
    print(f"github={found.status if found else 'none'}")
    print(f"openim={await _openim_state(env)}")
    return 0


async def _openim_state(env: Env) -> str:
    """OpenIM（T-0081）：none 没有配置地址或 secret；ok 用 imAdmin 令牌查得到通知账号；
    其他情况 unavailable。

    与运维页的检查相同（app/domain/operations.py 的 _openim）。
    """
    client = im.openim_client(env.settings, env.runtime, env.redis)
    if client is None:
        return "none"
    limit = env.settings.openim_timeout_seconds * 2 + 1
    try:
        await asyncio.wait_for(client.existing_users([im.NOTICE_USER_ID]), timeout=limit)
    except (ConnectorError, TimeoutError):
        return "unavailable"
    return "ok"


async def cmd_news_enable(env: Env, args: argparse.Namespace) -> int:
    """部署好 TrendRadar 后默认打开新闻早报（T-0225）。输出一行 news=<结果>，供部署脚本解析。

    与网页一样只有 owner 能改早报设置；只在这个工作空间还没保存过早报设置、也没有别的工作空间
    打开时才打开，保存过的设置（包括关掉的）不动。结果见 NewsService.enable_by_default。
    """
    actor, access = await resolve(env.db, args.email, args.workspace)
    if not has_role(access.role, Role.owner):
        raise Usage(f"{actor.user.email} 不是这个工作空间的 owner，不能打开新闻早报")
    service = NewsService(env.db, env.settings, env.runtime, env.redis)
    print(f"news={await service.enable_by_default(actor, access)}")
    return 0


# ---------------------------------------------------------------- 入口


Command = Callable[[Env, argparse.Namespace], Awaitable[int]]


COMMANDS: dict[str, Command] = {
    "status": cmd_status,
    "news-enable": cmd_news_enable,
}


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="报告组件状态（部署脚本调用）")
    root.add_argument("--email", help="以哪个工作台用户的身份操作（默认首次安装创建的管理员）")
    root.add_argument("--workspace", type=uuid.UUID, help="工作空间 ID（该用户管理多个时必填）")
    sub = root.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="是否完成首次安装、各组件是否已接入")
    sub.add_parser("news-enable", help="部署好 TrendRadar 后默认打开新闻早报（只在还没设置过时）")
    return root


async def run(args: argparse.Namespace) -> int:
    async with environment() as env:
        try:
            return await COMMANDS[args.command](env, args)
        except ConnectorError as error:
            raise Failed(str(error)) from None
        except ApiError as error:
            raise Failed(error.message) from None


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return asyncio.run(run(args))
    except Usage as error:
        print(f"失败  {error}", file=sys.stderr)
        return 2
    except Failed as error:
        print(f"失败  {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
