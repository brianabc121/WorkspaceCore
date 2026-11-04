"""远程运维动作（T-0114，docs/v2/AGENT_OPS_PLAN.md 第 6.1 节，D1 选 A）。

走现有的 SSH 通道（工作台密钥库里的专用密钥 + 部署时确认过的主机指纹），在接入包目录里执行
写死的命令，只作用于这个实例的 Compose 项目；不拼接任何用户输入（logs 的服务名是固定的三个之一）。

- 只有 owner / admin；重启、重新拉起、清理镜像要再输一次自己的登录密码（与审批高危操作相同）；
- 同一实例同时只跑一个动作（部分唯一索引），也不和 SSH 部署 / 卸载 / 吊销同时进行；
- 超时 60 秒，重新拉起 180 秒；在 API 进程内的后台协程里运行（与 SSH 部署共用 SshJobRunner），
  进程重启时 running 改为 interrupted；
- 输出先脱敏（令牌、密钥、Authorization、私钥块）再存，最多 64 KB，7 天后删除；
- 审计：谁、什么时候、对哪个实例、做了什么动作、退出码；不记输出原文。
- 只适用于用 SSH 部署过的实例（agent_ssh_targets.install_dir 有值、专用密钥还在）。
"""

import asyncio
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import asyncssh
from redis.asyncio import Redis
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import agent_provisioning as prov
from app.core.config import Settings
from app.core.errors import ApiError
from app.domain.agent_ssh import agent_ssh_context
from app.domain.audit import AuditTrail
from app.domain.credentials import CredentialBox, CredentialError
from app.domain.policy import WorkspaceAccess
from app.domain.step_up import verify_step_up
from app.integrations import ssh
from app.models import AgentInstance, AgentOpsAction, AgentSshJob, AgentSshTarget, User
from app.schemas.agents import AgentOpsActionOut, AgentOpsRun, AgentOpsState

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
    """每个动作对应的命令。directory 是部署时记下的接入包目录（经 shlex.quote）。"""
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


def unavailable_reason(target: AgentSshTarget | None) -> str | None:
    if target is None or not target.install_dir or target.host_key_confirmed_at is None:
        return "not_deployed"
    if target.client_key_ref is None:
        return "key_missing"
    return None


def _interrupt(row: AgentOpsAction, message: str) -> None:
    row.status = "interrupted"
    row.error_code = "interrupted"
    row.error_message = message
    row.finished_at = datetime.now(UTC)


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

    async def _target(
        self, instance: AgentInstance, *, lock: bool = False
    ) -> AgentSshTarget | None:
        query = select(AgentSshTarget).where(
            AgentSshTarget.instance_id == instance.id,
            AgentSshTarget.workspace_id == instance.workspace_id,
        )
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        return await self.db.scalar(query)

    def _alive(self, key: uuid.UUID) -> bool:
        return self.runner is not None and self.runner.running(key)

    async def _running(self, instance: AgentInstance) -> AgentOpsAction | None:
        """进行中的动作；记录是 running、但本进程里没有它的协程（意外退出）时标为中断。"""
        row = await self.db.scalar(
            select(AgentOpsAction)
            .where(AgentOpsAction.instance_id == instance.id, AgentOpsAction.status == "running")
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
        # 本进程里没有协程的安装任务由 AgentSshService 标为中断；这里只看是否还在跑
        return job is not None and self._alive(job)

    async def state(self, access: WorkspaceAccess, instance_id: uuid.UUID) -> AgentOpsState:
        instance = await self._instance(access, instance_id)
        target = await self._target(instance)
        running = await self._running(instance)
        rows = (
            await self.db.scalars(
                select(AgentOpsAction)
                .where(
                    AgentOpsAction.instance_id == instance.id,
                    AgentOpsAction.expires_at > datetime.now(UTC),
                )
                .order_by(AgentOpsAction.started_at.desc())
                .limit(HISTORY)
                .execution_options(populate_existing=True)
            )
        ).all()
        await self.db.commit()
        reason = unavailable_reason(target)
        return AgentOpsState.model_validate(
            {
                "available": reason is None,
                "reason": reason,
                "running": to_out(running) if running else None,
                "items": [to_out(row) for row in rows],
            }
        )

    async def get(
        self, access: WorkspaceAccess, instance_id: uuid.UUID, action_id: uuid.UUID
    ) -> AgentOpsActionOut:
        instance = await self._instance(access, instance_id)
        row = await self.db.scalar(
            select(AgentOpsAction)
            .where(AgentOpsAction.id == action_id, AgentOpsAction.instance_id == instance.id)
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
        target = await self._target(instance, lock=True)
        reason = unavailable_reason(target)
        if reason is not None or target is None:
            raise ApiError(
                409,
                "AGENT_OPS_UNAVAILABLE",
                "只适用于用 SSH 部署过的实例：先在实例页做一次 SSH 部署"
                if reason == "not_deployed"
                else "专用密钥已吊销：重新做一次 SSH 部署后才能执行运维动作",
                details={"reason": reason},
            )
        if self.settings.credential_encryption_key is None:
            raise ApiError(503, "EXTERNAL_UNAVAILABLE", "密钥库未配置加密密钥")
        if body.action in STEP_UP_ACTIONS:
            await verify_step_up(
                self.redis,
                user,
                body.password,
                message=f"{ACTION_NAMES[body.action]}需要重新输入密码",
            )
        if await self._job_running(target):
            raise ApiError(409, "AGENT_SSH_JOB_RUNNING", "这台服务器正在部署或卸载，结束后再操作")
        if await self._running(instance) is not None:
            raise ApiError(409, "AGENT_OPS_RUNNING", "这个实例正在执行另一个运维动作，结束后再操作")
        now = datetime.now(UTC)
        service = (body.service or "openclaw") if body.action == "logs" else None
        row = AgentOpsAction(
            id=uuid.uuid4(),
            workspace_id=instance.workspace_id,
            instance_id=instance.id,
            action=body.action,
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
            workspace_ids=[instance.workspace_id],
            actor_id=access.user_id,
            resource_type="agent_instance",
            resource_id=instance.id,
            metadata={"action": body.action, "service": service, "action_id": str(row.id)},
            client_ip=client_ip,
        )
        # 提交前先登记：提交之后、启动协程之前，别的请求不会把它当成失效的动作
        self.runner.reserve(row.id)
        try:
            await self.db.commit()
        except IntegrityError as error:
            self.runner.release(row.id)
            await self.db.rollback()
            raise ApiError(
                409, "AGENT_OPS_RUNNING", "这个实例正在执行另一个运维动作，结束后再操作"
            ) from error
        except BaseException:
            self.runner.release(row.id)
            raise
        self.runner.launch(
            row.id, run_action(self.runner.sessionmaker, self.settings, row.id, client_ip=client_ip)
        )
        return to_out(row, with_output=True)


# ---------------------------------------------------------------- 后台执行


@dataclass(frozen=True)
class _Plan:
    action: str
    service: str | None
    workspace_id: uuid.UUID
    instance_id: uuid.UUID
    started_by: uuid.UUID | None
    target_id: uuid.UUID
    address: str
    port: int
    username: str
    host_key: str
    client_key_ref: str
    directory: str


async def _plan(db: AsyncSession, action_id: uuid.UUID) -> tuple[_Plan | None, str | None]:
    row = await db.get(AgentOpsAction, action_id)
    if row is None:
        return None, None
    target = await db.scalar(
        select(AgentSshTarget).where(
            AgentSshTarget.instance_id == row.instance_id,
            AgentSshTarget.workspace_id == row.workspace_id,
        )
    )
    if target is None or unavailable_reason(target) is not None:
        return None, "这台服务器已不再是 SSH 部署状态（目标服务器被删除、卸载或专用密钥被吊销）"
    assert target.install_dir is not None  # noqa: S101 - unavailable_reason 已检查
    assert target.client_key_ref is not None  # noqa: S101
    return (
        _Plan(
            action=row.action,
            service=row.service,
            workspace_id=row.workspace_id,
            instance_id=row.instance_id,
            started_by=row.started_by,
            target_id=target.id,
            address=target.address,
            port=target.port,
            username=target.username,
            host_key=target.host_key,
            client_key_ref=target.client_key_ref,
            directory=target.install_dir,
        ),
        None,
    )


async def _execute(settings: Settings, plan: _Plan, out: Output) -> int:
    """登录并依次执行；返回第一条失败命令的退出码（全部成功为 0）。

    查看状态与立即上报会跑完全部命令；其它动作遇到失败就停。
    """
    box = CredentialBox.from_settings(settings)
    secret = box.resolve(plan.client_key_ref, context=agent_ssh_context(plan.target_id))
    key = asyncssh.import_private_key(secret.get_secret_value())
    conn = await ssh.connect(plan.address, plan.port, plan.username, plan.host_key, client_key=key)
    exit_code = 0
    try:
        for command in commands(plan.action, plan.service, plan.directory):
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
) -> None:
    async with sessionmaker() as db:
        plan, problem = await _plan(db, action_id)
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
                    exit_code = await _execute(settings, plan, out)
                status = "succeeded" if exit_code == 0 else "failed"
            except TimeoutError:
                error_code, error_message = "timeout", f"超过 {int(seconds)} 秒没有完成"
            except ssh.SshError as error:
                if error.reason == "command_timeout":
                    error_code, error_message = "timeout", f"超过 {int(seconds)} 秒没有完成"
                else:
                    error_code, error_message = error.reason, str(error)
            except (CredentialError, asyncssh.KeyImportError):
                error_code, error_message = (
                    "client_key_invalid",
                    "专用私钥无法解密（加密密钥可能换过）",
                )
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
                resource_type="agent_instance",
                resource_id=row.instance_id,
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
