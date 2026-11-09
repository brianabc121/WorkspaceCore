"""远程运维动作（T-0114，docs/v2/AGENT_OPS_PLAN.md 第 6.1 节，D1 选 A）。

走现有的 SSH 通道（T-0179 起：服务器上加密保存的登录密码 + 确认过的主机指纹，只用密码登录，
不用 SSH 客户端密钥），在接入包目录里执行写死的命令，只作用于这个实例的 Compose 项目；
不拼接任何用户输入（logs 的服务名是固定的三个之一）。

- 只有 owner / admin；重启、重新拉起、清理镜像要再输一次自己的**工作台账号**密码（二次认证，
  与审批高危操作相同；与服务器的 SSH 登录密码是两回事，不会挪作 SSH 密码）；
- 同一实例同时只跑一个动作（部分唯一索引），也不和 SSH 部署 / 卸载 / 吊销同时进行；
- 超时 60 秒，重新拉起 180 秒；在 API 进程内的后台协程里运行（与 SSH 部署共用 SshJobRunner），
  进程重启时 running 改为 interrupted；
- 输出先脱敏（令牌、密钥、Authorization、私钥块）再存，最多 64 KB，7 天后删除；
- 审计：谁、什么时候、对哪个实例、做了什么动作、退出码；不记输出原文。
- 只适用于部署到 SSH 服务器上的实例（agent_deployments 有目录、服务器保存了能解开的登录密码、
  主机公钥已确认）；动作接纳时记下凭证版本，登录时密码已被替换就停止；
  一台服务器多个实例后（T-0145）：清理镜像、查看全部容器是服务器级的动作，
  同一台服务器同时只跑一个动作。
"""

import asyncio
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from pydantic import SecretStr
from redis.asyncio import Redis
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import agent_provisioning as prov
from app.core.config import Settings
from app.core.errors import ApiError
from app.domain import agent_server_ssh
from app.domain.agent_server_credentials import ServerCredentialError, password_readable
from app.domain.audit import AuditTrail
from app.domain.credentials import CredentialBox, CredentialError
from app.domain.policy import WorkspaceAccess
from app.domain.step_up import verify_step_up
from app.integrations import ssh
from app.models import (
    AgentDeployment,
    AgentInstance,
    AgentOpsAction,
    AgentServer,
    AgentSshJob,
    AgentSshTarget,
    User,
)
from app.schemas.agents import AgentOpsActionOut, AgentOpsRun, AgentOpsState, ServerOpsRun

if TYPE_CHECKING:
    from app.domain.agent_ssh_install import SshJobRunner

logger = logging.getLogger("app.agent_ops")

OUTPUT_LIMIT = 64 * 1024
RETENTION = timedelta(days=7)
ACTION_TIMEOUT = 60.0
UP_TIMEOUT = 180.0
HISTORY = 20
LOG_LINES = 200
STEP_UP_ACTIONS = frozenset({"restart", "up", "prune"})
ACTION_NAMES = {
    "status": "查看状态",
    "logs": "查看日志",
    "restart": "重启 OpenClaw",
    "up": "重新拉起全部服务",
    "prune": "清理无用镜像",
    "report": "立即上报一次",
    "containers": "查看全部容器",
}

# ---------------------------------------------------------------- 脱敏

_PEM = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----", re.S
)
_REDACTIONS: list[tuple[re.Pattern[str], str]] = [
    (_PEM, "***"),
    # 未闭合的私钥块（输出被截断时）：从 BEGIN 起全部替换
    (re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*", re.S), "***"),
    (
        re.compile(r"(?i)(authorization\s*[:=]\s*)(?:(?:bearer|basic|token)\s+)?[^\s,;\"']+"),
        r"\1***",
    ),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+"), "Bearer ***"),
    # 工作台签发的实例凭证、常见的模型服务密钥、JWT
    (re.compile(r"mwa_[A-Za-z0-9_\-]+"), "mwa_***"),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{12,}"), "sk-***"),
    (re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]+"), "***"),
    # KEY=value / "key": "value" 形式的令牌、密钥、密码
    (
        re.compile(
            r"(?i)(\b[\w.-]*(?:token|secret|password|passwd|api[_-]?key|access[_-]?key|private[_-]?key)"
            r"[\w.-]*[\"']?\s*[:=]\s*[\"']?)[^\s\"',;}]+"
        ),
        r"\1***",
    ),
]


def redact(text: str) -> str:
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


class Output:
    """收集命令输出：整体脱敏后只保留末尾 64 KB（按 UTF-8 字节）。"""

    def __init__(self) -> None:
        self._parts: list[str] = []

    def write(self, text: str) -> None:
        if text:
            self._parts.append(text if text.endswith("\n") else text + "\n")

    def result(self) -> tuple[str, bool]:
        data = redact("".join(self._parts)).encode()
        if len(data) <= OUTPUT_LIMIT:
            return data.decode(), False
        return data[-OUTPUT_LIMIT:].decode("utf-8", "ignore"), True


# ---------------------------------------------------------------- 命令（写死）


def commands(action: str, service: str | None, directory: str) -> list[str]:
    """每个动作对应的命令。directory 是部署时记下的这个实例的接入包目录（经 shlex.quote）；
    服务器级的动作（prune、containers）不用目录。"""
    compose = prov.compose_command
    cd = f"cd {prov.q(directory)}"
    report = ("node", "/opt/myworkspace/bin/")
    if action == "status":
        return [
            compose(directory, "ps", "--all"),
            f'{cd} && ids=$(docker compose ps -q) && if [ -n "$ids" ];'
            " then docker stats --no-stream $ids; else echo '没有运行中的容器'; fi",
            f"df -h {prov.q(directory)} /",
            "uptime",
        ]
    if action == "logs":
        if service not in ("openclaw", "tunnel", "image-proxy"):
            raise ValueError("unknown service")
        return [compose(directory, "logs", "--no-color", "--tail", str(LOG_LINES), service)]
    if action == "restart":
        return [compose(directory, "restart", "openclaw")]
    if action == "up":
        return [compose(directory, "up", "-d")]
    if action == "prune":
        return ["docker image prune -f"]
    if action == "containers":
        # 这台服务器上所有工作台的 Compose 项目（每个实例一个）与它们的资源占用
        label = "label=com.docker.compose.project"
        return [
            "docker compose ls --all",
            f"docker ps --all --filter {label} --format"
            " 'table {{.Label \"com.docker.compose.project\"}}\\t{{.Names}}\\t{{.Status}}'",
            f'ids=$(docker ps -q --filter {label}) && if [ -n "$ids" ];'
            " then docker stats --no-stream $ids; else echo '没有运行中的容器'; fi",
            "df -h /",
            "uptime",
        ]
    if action == "report":
        return [
            compose(directory, "exec", "-T", "openclaw", report[0], report[1] + "usage-report.mjs"),
            compose(directory, "exec", "-T", "openclaw", report[0], report[1] + "host-report.mjs"),
        ]
    raise ValueError("unknown action")


def timeout_for(action: str) -> float:
    return UP_TIMEOUT if action == "up" else ACTION_TIMEOUT


# ---------------------------------------------------------------- 输出模型


def to_out(row: AgentOpsAction, *, with_output: bool = False) -> AgentOpsActionOut:
    return AgentOpsActionOut.model_validate(
        {
            "id": row.id,
            "action": row.action,
            "instance_id": row.instance_id,
            "service": row.service,
            "status": row.status,
            "exit_code": row.exit_code,
            "output": row.output if with_output else None,
            "truncated": row.truncated,
            "error_code": row.error_code,
            "error_message": row.error_message,
            "started_by": row.started_by,
            "started_at": row.started_at,
            "finished_at": row.finished_at,
            "expires_at": row.expires_at,
        }
    )


def _login_reason(target: AgentSshTarget, box: CredentialBox | None) -> str | None:
    """能不能用保存的密码登录这台服务器（只看本地记录，不解密、不连接）。"""
    if target.host_key_confirmed_at is None:
        return "host_key_unconfirmed"
    if target.login_password_ref is None:
        return "credential_missing"
    if not password_readable(box, target.login_password_ref):
        return "credential_unreadable"
    return None


def unavailable_reason(
    deployment: AgentDeployment | None,
    target: AgentSshTarget | None,
    box: CredentialBox | None,
) -> str | None:
    """实例级动作能不能执行（T-0179）：部署到了一台 SSH 服务器（有目录），并且能用保存的密码登录。

    旧方案的专用密钥（client_key_ref）不再作为依据（key_missing 已弃用）。
    """
    if deployment is None or not deployment.install_dir or target is None:
        return "not_deployed"
    return _login_reason(target, box)


def server_unavailable_reason(
    target: AgentSshTarget | None, box: CredentialBox | None
) -> str | None:
    """服务器级动作能不能执行（T-0179）：经 SSH 添加，并且能用保存的密码登录。"""
    if target is None:
        return "not_deployed"
    return _login_reason(target, box)


def _interrupt(row: AgentOpsAction, message: str) -> None:
    row.status = "interrupted"
    row.error_code = "interrupted"
    row.error_message = message
    row.finished_at = datetime.now(UTC)


_UNAVAILABLE_MESSAGES = {
    "not_deployed": "只适用于部署到 SSH 服务器上的实例：先在“智能体服务器”里添加服务器并部署",
    "host_key_unconfirmed": "这台服务器的主机公钥还没有确认：先在服务器页核对指纹",
    "credential_missing": "这台服务器还没有保存登录密码：先在服务器页补充登录密码",
    "credential_unreadable": "保存的登录密码无法解密（加密密钥不对或密文损坏）：请重新填写登录密码",
}


def _unavailable(reason: str | None) -> ApiError:
    return ApiError(
        409,
        "AGENT_OPS_UNAVAILABLE",
        _UNAVAILABLE_MESSAGES.get(reason or "", "现在不能对这台服务器执行运维动作"),
        details={"reason": reason},
    )


class AgentOpsService:
    def __init__(
        self,
        db: AsyncSession,
        settings: Settings,
        redis: Redis | None = None,
        runner: "SshJobRunner | None" = None,
    ) -> None:
        self.db = db
        self.settings = settings
        self.redis = redis
        self.runner = runner
        self.audit = AuditTrail(db, settings)

    def _box(self) -> CredentialBox | None:
        try:
            return CredentialBox.from_settings(self.settings)
        except CredentialError:
            return None

    async def _instance(self, access: WorkspaceAccess, instance_id: uuid.UUID) -> AgentInstance:
        instance = await self.db.scalar(
            select(AgentInstance).where(
                AgentInstance.id == instance_id,
                AgentInstance.workspace_id == access.workspace.id,
            )
        )
        if instance is None:
            raise ApiError(404, "NOT_FOUND", "实例不存在")
        return instance

    async def _server(self, access: WorkspaceAccess, server_id: uuid.UUID) -> AgentServer:
        server = await self.db.scalar(
            select(AgentServer).where(
                AgentServer.id == server_id, AgentServer.workspace_id == access.workspace.id
            )
        )
        if server is None:
            raise ApiError(404, "NOT_FOUND", "服务器不存在")
        return server

    async def _target(self, server_id: uuid.UUID, *, lock: bool = False) -> AgentSshTarget | None:
        query = select(AgentSshTarget).where(AgentSshTarget.server_id == server_id)
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        return await self.db.scalar(query)

    async def _deployment(
        self, instance: AgentInstance, *, lock: bool = False
    ) -> tuple[AgentDeployment | None, AgentSshTarget | None]:
        deployment = await self.db.scalar(
            select(AgentDeployment).where(
                AgentDeployment.instance_id == instance.id,
                AgentDeployment.workspace_id == instance.workspace_id,
            )
        )
        if deployment is None:
            return None, None
        return deployment, await self._target(deployment.server_id, lock=lock)

    def _alive(self, key: uuid.UUID) -> bool:
        return self.runner is not None and self.runner.running(key)

    async def _running(self, server_id: uuid.UUID) -> AgentOpsAction | None:
        """这台服务器上进行中的动作；记录是 running、但本进程里没有它的协程时标为中断。"""
        row = await self.db.scalar(
            select(AgentOpsAction)
            .where(AgentOpsAction.server_id == server_id, AgentOpsAction.status == "running")
            .execution_options(populate_existing=True)
        )
        if row is None or self._alive(row.id):
            return row
        _interrupt(row, "动作已不在运行；可以重新执行")
        await self.db.flush()
        return None

    async def _job_running(self, target: AgentSshTarget) -> bool:
        job = await self.db.scalar(
            select(AgentSshJob.id).where(
                AgentSshJob.target_id == target.id, AgentSshJob.status == "running"
            )
        )
        # 本进程里没有协程的部署任务由 AgentServerService 标为中断；这里只看是否还在跑
        return job is not None and self._alive(job)

    async def _history(self, *conditions: Any) -> list[AgentOpsAction]:
        return list(
            (
                await self.db.scalars(
                    select(AgentOpsAction)
                    .where(*conditions, AgentOpsAction.expires_at > datetime.now(UTC))
                    .order_by(AgentOpsAction.started_at.desc())
                    .limit(HISTORY)
                    .execution_options(populate_existing=True)
                )
            ).all()
        )

    @staticmethod
    def _state(
        reason: str | None, running: AgentOpsAction | None, rows: list[AgentOpsAction]
    ) -> AgentOpsState:
        return AgentOpsState.model_validate(
            {
                "available": reason is None,
                "reason": reason,
                "running": to_out(running) if running else None,
                "items": [to_out(row) for row in rows],
            }
        )

    # ---------------------------------------------------------------- 实例级

    async def state(self, access: WorkspaceAccess, instance_id: uuid.UUID) -> AgentOpsState:
        instance = await self._instance(access, instance_id)
        deployment, target = await self._deployment(instance)
        running = await self._running(deployment.server_id) if deployment else None
        rows = await self._history(AgentOpsAction.instance_id == instance.id)
        await self.db.commit()
        return self._state(unavailable_reason(deployment, target, self._box()), running, rows)

    async def get(
        self, access: WorkspaceAccess, instance_id: uuid.UUID, action_id: uuid.UUID
    ) -> AgentOpsActionOut:
        instance = await self._instance(access, instance_id)
        return await self._get(action_id, AgentOpsAction.instance_id == instance.id)

    async def _get(self, action_id: uuid.UUID, scope: Any) -> AgentOpsActionOut:
        row = await self.db.scalar(
            select(AgentOpsAction)
            .where(AgentOpsAction.id == action_id, scope)
            .execution_options(populate_existing=True)
        )
        if row is None or row.expires_at <= datetime.now(UTC):
            raise ApiError(404, "NOT_FOUND", "记录不存在或已过期")
        if row.status == "running" and not self._alive(row.id):
            _interrupt(row, "动作已不在运行；可以重新执行")
            await self.db.commit()
        return to_out(row, with_output=True)

    async def run(
        self,
        access: WorkspaceAccess,
        user: User,
        instance_id: uuid.UUID,
        body: AgentOpsRun,
        *,
        client_ip: str,
    ) -> AgentOpsActionOut:
        if self.runner is None:
            raise ApiError(503, "EXTERNAL_UNAVAILABLE", "运维动作暂时不可用，请稍后重试")
        if body.service is not None and body.action != "logs":
            raise ApiError(422, "VALIDATION_FAILED", "只有查看日志可以选服务")
        instance = await self._instance(access, instance_id)
        deployment, target = await self._deployment(instance, lock=True)
        reason = unavailable_reason(deployment, target, self._box())
        if reason is not None or target is None or deployment is None:
            raise _unavailable(reason)
        service = (body.service or "openclaw") if body.action == "logs" else None
        return await self._start(
            access,
            user,
            target,
            action=body.action,
            service=service,
            password=body.password,
            instance=instance,
            client_ip=client_ip,
        )

    # ---------------------------------------------------------------- 服务器级

    async def server_state(self, access: WorkspaceAccess, server_id: uuid.UUID) -> AgentOpsState:
        server = await self._server(access, server_id)
        target = await self._target(server.id)
        running = await self._running(server.id)
        rows = await self._history(
            AgentOpsAction.server_id == server.id, AgentOpsAction.instance_id.is_(None)
        )
        await self.db.commit()
        return self._state(server_unavailable_reason(target, self._box()), running, rows)

    async def server_get(
        self, access: WorkspaceAccess, server_id: uuid.UUID, action_id: uuid.UUID
    ) -> AgentOpsActionOut:
        server = await self._server(access, server_id)
        return await self._get(action_id, AgentOpsAction.server_id == server.id)

    async def server_run(
        self,
        access: WorkspaceAccess,
        user: User,
        server_id: uuid.UUID,
        body: ServerOpsRun,
        *,
        client_ip: str,
    ) -> AgentOpsActionOut:
        if self.runner is None:
            raise ApiError(503, "EXTERNAL_UNAVAILABLE", "运维动作暂时不可用，请稍后重试")
        server = await self._server(access, server_id)
        target = await self._target(server.id, lock=True)
        reason = server_unavailable_reason(target, self._box())
        if reason is not None or target is None:
            raise _unavailable(reason)
        return await self._start(
            access,
            user,
            target,
            action=body.action,
            service=None,
            password=body.password,
            instance=None,
            client_ip=client_ip,
        )

    # ---------------------------------------------------------------- 开始执行

    async def _start(
        self,
        access: WorkspaceAccess,
        user: User,
        target: AgentSshTarget,
        *,
        action: str,
        service: str | None,
        password: SecretStr | None,
        instance: AgentInstance | None,
        client_ip: str,
    ) -> AgentOpsActionOut:
        assert self.runner is not None  # noqa: S101 - 调用方已检查
        if self.settings.credential_encryption_key is None:
            raise ApiError(503, "EXTERNAL_UNAVAILABLE", "密钥库未配置加密密钥")
        if action in STEP_UP_ACTIONS:
            # 二次认证用的是工作台账号的登录密码（不是服务器的 SSH 密码）
            await verify_step_up(
                self.redis, user, password, message=f"{ACTION_NAMES[action]}需要重新输入密码"
            )
        if await self._job_running(target):
            raise ApiError(409, "AGENT_SSH_JOB_RUNNING", "这台服务器正在部署或卸载，结束后再操作")
        busy = ApiError(409, "AGENT_OPS_RUNNING", "这台服务器正在执行另一个运维动作，结束后再操作")
        if await self._running(target.server_id) is not None:
            raise busy
        now = datetime.now(UTC)
        revision = target.credential_revision
        row = AgentOpsAction(
            id=uuid.uuid4(),
            workspace_id=target.workspace_id,
            instance_id=instance.id if instance is not None else None,
            server_id=target.server_id,
            action=action,
            service=service,
            status="running",
            output="",
            truncated=False,
            started_by=access.user_id,
            started_at=now,
            expires_at=now + RETENTION,
        )
        self.db.add(row)
        await self.audit.record(
            "agents.ops.started",
            workspace_ids=[target.workspace_id],
            actor_id=access.user_id,
            resource_type="agent_instance" if instance is not None else "agent_server",
            resource_id=instance.id if instance is not None else target.server_id,
            metadata={
                "action": action,
                "service": service,
                "action_id": str(row.id),
                "server_id": str(target.server_id),
                "auth": "saved_password",
                "auth_revision": revision,
            },
            client_ip=client_ip,
        )
        # 提交前先登记：提交之后、启动协程之前，别的请求不会把它当成失效的动作
        self.runner.reserve(row.id)
        try:
            await self.db.commit()
        except IntegrityError as error:
            self.runner.release(row.id)
            await self.db.rollback()
            raise busy from error
        except BaseException:
            self.runner.release(row.id)
            raise
        self.runner.launch(
            row.id,
            run_action(
                self.runner.sessionmaker,
                self.settings,
                row.id,
                client_ip=client_ip,
                expected_revision=revision,
            ),
        )
        return to_out(row, with_output=True)


# ---------------------------------------------------------------- 后台执行


@dataclass(frozen=True)
class _Plan:
    action: str
    service: str | None
    workspace_id: uuid.UUID
    instance_id: uuid.UUID | None
    server_id: uuid.UUID
    started_by: uuid.UUID | None
    # 这台服务器的 SSH 记录（开始执行时读出；登录用其中保存的密码，不含旧专用私钥的用途）
    target: AgentSshTarget
    directory: str | None


async def _plan(
    db: AsyncSession, action_id: uuid.UUID, box: CredentialBox | None
) -> tuple[_Plan | None, str | None]:
    row = await db.get(AgentOpsAction, action_id)
    if row is None:
        return None, None
    target = await db.scalar(
        select(AgentSshTarget).where(
            AgentSshTarget.server_id == row.server_id,
            AgentSshTarget.workspace_id == row.workspace_id,
        )
    )
    directory: str | None = None
    if row.instance_id is not None:
        deployment = await db.scalar(
            select(AgentDeployment).where(
                AgentDeployment.instance_id == row.instance_id,
                AgentDeployment.server_id == row.server_id,
            )
        )
        reason = unavailable_reason(deployment, target, box)
        if reason is not None:
            return None, (
                "这个实例已不在这台服务器上（被卸载）"
                if reason == "not_deployed"
                else _UNAVAILABLE_MESSAGES.get(reason, "现在不能登录这台服务器")
            )
        assert deployment is not None  # noqa: S101 - unavailable_reason 已检查
        directory = deployment.install_dir
    else:
        reason = server_unavailable_reason(target, box)
        if reason is not None:
            return None, (
                "这台服务器已被移除"
                if reason == "not_deployed"
                else _UNAVAILABLE_MESSAGES.get(reason, "现在不能登录这台服务器")
            )
    assert target is not None  # noqa: S101 - 上面已检查
    return (
        _Plan(
            action=row.action,
            service=row.service,
            workspace_id=row.workspace_id,
            instance_id=row.instance_id,
            server_id=row.server_id,
            started_by=row.started_by,
            target=target,
            directory=directory,
        ),
        None,
    )


async def _execute(
    settings: Settings, plan: _Plan, out: Output, *, expected_revision: int | None
) -> int:
    """用保存的登录密码登录并依次执行；返回第一条失败命令的退出码（全部成功为 0）。

    查看状态与立即上报会跑完全部命令；其它动作遇到失败就停。expected_revision 是接纳动作时的
    凭证版本：密码在那之后被替换就不登录（ServerCredentialError）。
    """
    box = CredentialBox.from_settings(settings)
    revision = (
        expected_revision if expected_revision is not None else plan.target.credential_revision
    )
    conn = await agent_server_ssh.connect_agent_server(plan.target, box, expected_revision=revision)
    exit_code = 0
    try:
        for command in commands(plan.action, plan.service, plan.directory or ""):
            out.write(f"$ {command}")
            result = await ssh.run(conn, command, seconds=timeout_for(plan.action))
            out.write(result.stdout)
            out.write(result.stderr)
            if not result.ok:
                out.write(f"（退出码 {result.exit_status}）")
                if exit_code == 0:
                    exit_code = result.exit_status
                if plan.action not in ("status", "report"):
                    break
    finally:
        conn.close()
    return exit_code


async def run_action(
    sessionmaker: async_sessionmaker[AsyncSession],
    settings: Settings,
    action_id: uuid.UUID,
    *,
    client_ip: str | None,
    expected_revision: int | None = None,
) -> None:
    try:
        box: CredentialBox | None = CredentialBox.from_settings(settings)
    except CredentialError:
        box = None
    async with sessionmaker() as db:
        plan, problem = await _plan(db, action_id, box)
    out = Output()
    exit_code: int | None = None
    status, error_code, error_message = "failed", None, None
    if plan is None and problem is None:
        return  # 记录已被删除（实例被删掉）
    try:
        if plan is None:
            error_code, error_message = "unavailable", problem
        else:
            seconds = timeout_for(plan.action)
            try:
                async with asyncio.timeout(seconds):
                    exit_code = await _execute(
                        settings, plan, out, expected_revision=expected_revision
                    )
                status = "succeeded" if exit_code == 0 else "failed"
            except TimeoutError:
                error_code, error_message = "timeout", f"超过 {int(seconds)} 秒没有完成"
            except ssh.SshError as error:
                if error.reason == "command_timeout":
                    error_code, error_message = "timeout", f"超过 {int(seconds)} 秒没有完成"
                else:
                    error_code, error_message = error.reason, str(error)
            except ServerCredentialError as error:
                error_code, error_message = (
                    {
                        "credentials_required": "credential_required",
                        "credential_changed": "credential_changed",
                    }.get(error.reason, "credential_unreadable"),
                    error.message,
                )
            except CredentialError:
                error_code, error_message = "credential_unreadable", "密钥库未配置加密密钥"
    except asyncio.CancelledError:
        await _finish(
            sessionmaker,
            settings,
            action_id,
            out,
            "interrupted",
            exit_code,
            "interrupted",
            "工作台停止，动作中断",
            client_ip=client_ip,
        )
        raise
    except Exception:
        logger.exception("agent ops action crashed", extra={"action_id": str(action_id)})
        error_code, error_message = "internal", "执行时出错，详见工作台日志"
    await _finish(
        sessionmaker,
        settings,
        action_id,
        out,
        status,
        exit_code,
        error_code,
        error_message,
        client_ip=client_ip,
    )


async def _finish(
    sessionmaker: async_sessionmaker[AsyncSession],
    settings: Settings,
    action_id: uuid.UUID,
    out: Output,
    status: str,
    exit_code: int | None,
    error_code: str | None,
    error_message: str | None,
    *,
    client_ip: str | None,
) -> None:
    text, truncated = out.result()
    try:
        async with sessionmaker() as db:
            row = await db.get(AgentOpsAction, action_id, with_for_update=True)
            if row is None or row.status != "running":
                return
            row.status = status
            row.exit_code = exit_code
            row.output = text
            row.truncated = truncated
            row.error_code = error_code
            row.error_message = error_message
            row.finished_at = datetime.now(UTC)
            await AuditTrail(db, settings).record(
                "agents.ops.finished",
                workspace_ids=[row.workspace_id],
                actor_id=row.started_by,
                resource_type="agent_instance" if row.instance_id else "agent_server",
                resource_id=row.instance_id or row.server_id,
                metadata={
                    "action": row.action,
                    "service": row.service,
                    "action_id": str(row.id),
                    "status": status,
                    "exit_code": exit_code,
                    "error_code": error_code,
                },
                client_ip=client_ip,
            )
            await db.commit()
    except SQLAlchemyError:
        logger.exception("agent ops action result not saved", extra={"action_id": str(action_id)})


async def purge(db: AsyncSession, now: datetime | None = None) -> int:
    """删除 7 天前的动作记录与输出（Worker 每天）；审计里的记录不受影响。"""
    result = await db.execute(
        delete(AgentOpsAction).where(
            AgentOpsAction.expires_at < (now or datetime.now(UTC)),
            AgentOpsAction.status != "running",
        )
    )
    return int(getattr(result, "rowcount", 0) or 0)
