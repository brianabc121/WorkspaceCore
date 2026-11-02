"""部署脚本自动接入 Vikunja、GitHub、n8n（T-0076）。在 api 容器里运行：

    docker compose exec -T api python -m app.commands.connect status
    printf '%s\\n' "$PASSWORD" | docker compose exec -T api python -m app.commands.connect \\
        vikunja --username admin --public-url http://localhost:3456
    printf '%s\\n' "$TOKEN" | docker compose exec -T api python -m app.commands.connect github-repos
    printf '%s\\n' "$TOKEN" | docker compose exec -T api python -m app.commands.connect \\
        github --repos owner/a,owner/b
    docker compose exec -T api python -m app.commands.connect n8n-owner
    printf '%s\\n' "$PASSWORD" | docker compose exec -T api python -m app.commands.connect \\
        n8n --owner-email you@example.com --public-url http://localhost:5678

- 密码与 Token 只从标准输入读，不出现在命令行参数、进程列表与日志里。
- 以工作空间管理员本人的身份调用和网页相同的服务：凭证加密保存、写审计、要求 admin 以上角色。
  默认是首次安装创建的管理员和他唯一管理的工作空间；不唯一时用 --email / --workspace 指定。
- 已经接入的组件不重复创建，只报告状态（更换凭证到工作台“集成设置”里改）。
- 接口的核验依据见 app/integrations/provisioning.py 与 docs/decisions/2026-11-02-deploy-connect.md。

退出码：0 完成或已接入；1 接入失败（外部服务拒绝、测试没通过）；2 用法错误或前提不满足。
"""

import argparse
import asyncio
import secrets
import sys
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from pydantic import ValidationError
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.core.errors import ApiError
from app.core.resources import create_engine, create_redis
from app.domain.automations import AutomationService
from app.domain.connectors import ConnectorRuntime
from app.domain.integrations import IntegrationService
from app.domain.policy import WorkspaceAccess, has_role, require_role
from app.integrations.base import ConnectorError
from app.integrations.github import GitHubClient
from app.integrations.http import CircuitBreaker
from app.integrations.n8n import TRIGGER_HEADER
from app.integrations.provisioning import N8nProvisioner, VikunjaProvisioner
from app.jobs.queue import ArqJobQueue, JobQueue
from app.models import User
from app.repositories import users as user_repo
from app.repositories import workspaces as workspace_repo
from app.schemas.automations import AutomationConnectionUpsert
from app.schemas.integrations import (
    GitHubIntegrationCreate,
    IntegrationCreate,
    IntegrationOut,
    IntegrationTestResult,
    IntegrationUpdate,
)
from app.schemas.workspaces import Role

TOKEN_TITLE = "My Workspace"  # noqa: S105 - Token 的显示名称，不是密钥
VIKUNJA_TOKEN_DAYS = 365
N8N_KEY_LABEL = "My Workspace（只读）"

_CHECKS = {
    "reachable": "能连上服务",
    "version": "版本",
    "token": "凭证有效",
    "tasks_read": "能读取任务",
    "webhooks": "Webhook 权限（实时同步，可选）",
}
_PERMISSIONS = {
    "metadata": "Metadata",
    "issues": "Issues",
    "pull_requests": "Pull requests",
    "actions": "Actions",
}
_SYNC = {
    "ok": "已同步",
    "stale": "数据较旧",
    "unavailable": "暂时不可用",
    "unauthorized": "凭证失效",
    "not_configured": "还没有同步",
}


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

    def automations(self) -> AutomationService:
        return AutomationService(self.db, self.settings, self.runtime)


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


def read_secret(label: str) -> str:
    value = sys.stdin.readline().rstrip("\r\n")
    if not value:
        raise Usage(f"没有从标准输入读到{label}")
    return value


def _find(items: list[IntegrationOut], provider: str) -> IntegrationOut | None:
    return next((item for item in items if item.provider == provider), None)


def _validation(error: ValidationError) -> Usage:
    # 不带输入值：可能是 Token
    parts = [
        f"{'.'.join(str(p) for p in item['loc'])}：{item['msg']}"
        for item in error.errors(include_input=False, include_url=False)
    ]
    return Usage("参数不正确：" + "；".join(parts))


def report(result: IntegrationTestResult) -> None:
    for check in result.checks:
        if check.code == "not_supported":
            continue  # GitHub 不用 Webhook（核验 ADR 第 6 节），不是失败项
        code = "" if check.ok or not check.code else f"（{check.code}）"
        print(f"  {'通过' if check.ok else '失败'}  {_CHECKS.get(check.name, check.name)}{code}")
    for repo in result.repositories or []:
        failed = [
            f"{_PERMISSIONS.get(c.name, c.name)}（{c.code or 'failed'}）"
            for c in repo.checks
            if not c.ok
        ]
        readable = "：Metadata、Issues、Pull requests、Actions 可读"
        detail = f"：{'、'.join(failed)}" if failed else readable
        print(f"  {'通过' if repo.ok else '失败'}  {repo.full_name}{detail}")
    for warning in result.warnings:
        print(f"  注意  {warning}")


async def _sync_line(service: IntegrationService, access: WorkspaceAccess, provider: str) -> None:
    current = _find(await service.list_integrations(access), provider)
    if current is not None and current.sync is not None:
        print(f"  同步：{_SYNC.get(current.sync.status, current.sync.status)}")


# ---------------------------------------------------------------- 子命令


async def cmd_status(env: Env, args: argparse.Namespace) -> int:
    """key=value 一行一项，供部署脚本解析：setup、vikunja、github、n8n（none 表示还没接入）。"""
    if await user_repo.count(env.db) == 0:
        print("setup=required")
        return 0
    print("setup=done")
    actor, access = await resolve(env.db, args.email, args.workspace)
    print(f"user={actor.user.email}")
    integrations = await env.integrations().list_integrations(access)
    for provider in ("vikunja", "github"):
        found = _find(integrations, provider)
        print(f"{provider}={found.status if found else 'none'}")
    connection = await env.automations().get_connection(access)
    print(f"n8n={connection.status if connection.configured and connection.status else 'none'}")
    return 0


async def cmd_vikunja(env: Env, args: argparse.Namespace) -> int:
    actor, access = await resolve(env.db, args.email, args.workspace)
    service = env.integrations()
    existing = _find(await service.list_integrations(access), "vikunja")
    if existing is not None:
        print(f"已接入 Vikunja（状态：{existing.status}），跳过")
        print("更换 Token 请到工作台“集成设置”里修改")
        return 0
    password = read_secret("Vikunja 密码")
    provisioner = VikunjaProvisioner(env.runtime.http("provision:vikunja"), args.base_url)
    session = await provisioner.login(args.username, password)
    expires = datetime.now(UTC).replace(microsecond=0) + timedelta(days=VIKUNJA_TOKEN_DAYS)
    created = await provisioner.create_token(session, title=TOKEN_TITLE, expires_at=expires)
    print(f"已在 Vikunja 创建 API Token「{TOKEN_TITLE}」（最小权限，{expires:%Y-%m-%d} 到期）")
    try:
        body = IntegrationCreate.model_validate(
            {
                "provider": "vikunja",
                "name": "Vikunja",
                "base_url": args.base_url,
                "public_url": args.public_url,
                "api_token": created.token,
                "token_expires_at": expires,
            }
        )
        integration = await service.create(actor, access, body)
    except (ValidationError, ApiError) as error:
        # 工作台没保存成功：吊销刚建的 Token，不留下没人用的凭证
        if created.token_id is not None:
            await provisioner.revoke_token(session, created.token_id)
        if isinstance(error, ValidationError):
            raise _validation(error) from None
        raise
    result = await service.test(actor, access, integration.id)
    report(result)
    if not result.ok:
        raise Failed("测试连接没有通过（见上面的检查项）；修好后到工作台“集成设置”里重新测试")
    await _sync_line(service, access, "vikunja")
    return 0


async def cmd_github_repos(env: Env, args: argparse.Namespace) -> int:
    """列出 Token 能访问的仓库（一行一个），部署脚本让管理员挑选。"""
    token = read_secret("GitHub Token")
    if not token.startswith("github_pat_"):
        raise Usage("只接受细粒度 Token（github_pat_ 开头），不要用经典 Token")
    client = GitHubClient(env.runtime.http("provision:github"), token, env.settings.github_api_url)
    listing = await client.list_user_repos()
    names = sorted(
        {item["full_name"] for item in listing.items if isinstance(item.get("full_name"), str)}
    )
    for name in names:
        print(name)
    if not listing.complete:
        print("注意  仓库太多，只列出了前面一部分", file=sys.stderr)
    return 0


async def cmd_github(env: Env, args: argparse.Namespace) -> int:
    """新建 GitHub 集成并检查权限；--replace 时用新 Token 与仓库替换已有的集成，再重新检查。

    仓库级的权限缺失不改变集成状态（核验 ADR 第 7 节），所以是否替换由管理员决定，不按状态判断。
    """
    actor, access = await resolve(env.db, args.email, args.workspace)
    service = env.integrations()
    existing = _find(await service.list_integrations(access), "github")
    if existing is not None and not args.replace:
        print(f"已接入 GitHub（状态：{existing.status}），跳过")
        print("更换 Token 或仓库：加 --replace 重新运行，或到工作台“集成设置”里修改")
        return 0
    token = read_secret("GitHub Token")
    repositories = [name.strip() for name in args.repos.split(",") if name.strip()]
    try:
        if existing is None:
            body = GitHubIntegrationCreate.model_validate(
                {
                    "provider": "github",
                    "name": "GitHub",
                    "api_token": token,
                    "repositories": repositories,
                }
            )
            integration_id = (await service.create_github(actor, access, body)).id
            print(f"已在工作台保存 GitHub 集成（{len(repositories)} 个仓库），检查权限：")
        else:
            update = IntegrationUpdate.model_validate(
                {"version": existing.version, "api_token": token, "repositories": repositories}
            )
            integration_id = (await service.update(actor, access, existing.id, update)).id
            print(
                f"已用新 Token 替换工作台里的 GitHub 集成（之前的状态：{existing.status}，"
                f"{len(repositories)} 个仓库），检查权限："
            )
    except ValidationError as error:
        raise _validation(error) from None
    result = await service.test(actor, access, integration_id)
    report(result)
    if not result.ok:
        raise Failed(_github_failure(result))
    await _sync_line(service, access, "github")
    return 0


def _github_failure(result: IntegrationTestResult) -> str:
    failed = {check.name for check in result.checks if not check.ok and check.name != "webhooks"}
    if "reachable" in failed:
        return (
            "连不上 GitHub（api.github.com）：检查服务器能否访问外网 HTTPS，"
            "再重新运行 deploy.sh connect"
        )
    if "token" in failed:
        return (
            "GitHub 拒绝了这个 Token：确认复制完整、没有过期或被撤销，"
            "重新生成后再运行 deploy.sh connect"
        )
    return (
        "仓库权限不够（见上面每个仓库缺少的项）：重新生成 Token 时 Repository access 选上这些仓库，"
        "Repository permissions 给 Issues、Pull requests、Actions 只读，再运行 deploy.sh connect"
    )


async def cmd_n8n_owner(env: Env, args: argparse.Namespace) -> int:
    """n8n 是否已经有 owner：部署脚本据此决定生成新密码，还是询问已有账号的密码。"""
    provisioner = N8nProvisioner(env.runtime.http("provision:n8n"), args.base_url)
    print("owner=exists" if await provisioner.owner_exists() else "owner=none")
    return 0


async def cmd_n8n(env: Env, args: argparse.Namespace) -> int:
    actor, access = await resolve(env.db, args.email, args.workspace)
    service = env.automations()
    current = await service.get_connection(access)
    if current.configured:
        print(f"已接入 n8n（状态：{current.status}），跳过；更换 API Key 请到工作台“自动化”页修改")
        return 0
    password = read_secret("n8n 密码")
    provisioner = N8nProvisioner(env.runtime.http("provision:n8n"), args.base_url)
    if await provisioner.owner_exists():
        print("n8n 已经有 owner 账号，用给出的邮箱和密码登录")
    else:
        try:
            await provisioner.setup_owner(email=args.owner_email, password=password)
        except ConnectorError as error:
            if error.kind == "VALIDATION_FAILED":
                raise Failed(
                    "n8n 拒绝创建 owner：密码要 8 到 64 位、至少一个数字和一个大写字母，邮箱要有效"
                ) from None
            raise
        print(f"已创建 n8n owner 账号：{args.owner_email}")
    cookie = await provisioner.login(email=args.owner_email, password=password)
    api_key = await provisioner.create_api_key(cookie, label=N8N_KEY_LABEL)
    print("已在 n8n 创建只读 API Key（workflow:list/read、execution:list/read）")
    secret = secrets.token_urlsafe(32)
    await provisioner.create_header_credential(
        cookie, name=TRIGGER_HEADER, header=TRIGGER_HEADER, value=secret
    )
    print(f"已在 n8n 创建 Header Auth 凭据「{TRIGGER_HEADER}」（工作流的 Webhook 节点选它）")
    try:
        body = AutomationConnectionUpsert.model_validate(
            {
                "base_url": args.base_url,
                "public_url": args.public_url,
                "api_key": api_key,
                "trigger_secret": secret,
            }
        )
        await service.put_connection(actor, access, body)
    except (ValidationError, ApiError) as error:
        print(
            f"注意  工作台没有保存这个连接：n8n 里刚建的只读 API Key「{N8N_KEY_LABEL}」与凭据"
            f"「{TRIGGER_HEADER}」没有用上，可以在 n8n 的设置里删除",
            file=sys.stderr,
        )
        if isinstance(error, ValidationError):
            raise _validation(error) from None
        raise
    result = await service.test_connection(actor, access)
    report(result)
    if not result.ok:
        raise Failed("测试连接没有通过（见上面的检查项）；修好后到工作台“自动化”页重新测试")
    return 0


# ---------------------------------------------------------------- 入口

Command = Callable[[Env, argparse.Namespace], Awaitable[int]]
COMMANDS: dict[str, Command] = {
    "status": cmd_status,
    "vikunja": cmd_vikunja,
    "github-repos": cmd_github_repos,
    "github": cmd_github,
    "n8n-owner": cmd_n8n_owner,
    "n8n": cmd_n8n,
}


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="自动接入 Vikunja、GitHub、n8n（部署脚本调用）")
    root.add_argument("--email", help="以哪个工作台用户的身份操作（默认首次安装创建的管理员）")
    root.add_argument("--workspace", type=uuid.UUID, help="工作空间 ID（该用户管理多个时必填）")
    sub = root.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="是否完成首次安装、各组件是否已接入")
    vikunja = sub.add_parser(
        "vikunja", help="登录 Vikunja 创建最小权限 Token 并接入（标准输入：密码）"
    )
    vikunja.add_argument("--username", required=True)
    vikunja.add_argument("--base-url", default="http://vikunja:3456")
    vikunja.add_argument("--public-url", default=None, help="浏览器打开 Vikunja 的地址")
    sub.add_parser("github-repos", help="列出 Token 能访问的仓库（标准输入：Token）")
    github = sub.add_parser("github", help="接入 GitHub 并检查权限（标准输入：Token）")
    github.add_argument("--repos", required=True, help="owner/repo，逗号分隔，最多 20 个")
    github.add_argument(
        "--replace", action="store_true", help="已有 GitHub 集成时，用新 Token 与仓库替换并重新检查"
    )
    owner = sub.add_parser("n8n-owner", help="n8n 是否已经有 owner 账号")
    owner.add_argument("--base-url", default="http://n8n:5678")
    n8n = sub.add_parser("n8n", help="建 owner、只读 API Key 与触发凭据并接入（标准输入：密码）")
    n8n.add_argument("--owner-email", required=True)
    n8n.add_argument("--base-url", default="http://n8n:5678")
    n8n.add_argument("--public-url", default=None, help="浏览器打开 n8n 编辑器的地址")
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
