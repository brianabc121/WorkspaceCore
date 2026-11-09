"""通过 SSH 部署 OpenClaw 的后台任务（T-0089 B2；T-0145 一台服务器多个实例；T-0179 全程密码登录）。

设计见 docs/decisions/2026-11-03-openclaw-ssh-provisioning.md 第 5 节，OpenClaw 服务器的登录方式由
docs/decisions/2026-11-09-agent-server-password-auth.md 取代：

- 任务在 API 进程内的后台协程里运行（API 只有一个进程）；
- OpenClaw 服务器（`AgentServerInstaller` / `AgentServerUninstaller`）：初始化、部署、卸载
  都用服务器上加密保存的登录密码，每次登录前才按任务开始时的凭证版本解密；
  不生成、不安装、不读取 SSH 客户端密钥。
  任务记录、日志、审计里没有密码或密文（日志里出现就替换成 ***）；
- 运行服务器（`runner_ssh.RunnerInstaller`，T-0136）复用这里的公共部分（`Installer`），
  仍是“一次密码 + 专用密钥”，不受影响；
- 每一步的状态写进 `agent_ssh_jobs.steps`，命令输出的末尾一段脱敏后写进 `log`；
- 每次写库都用新的短会话（任务可能跑十几分钟，不长时间占着连接）；
  任务所在的实例或目标服务器被删掉时（行不见了）安静地结束。
"""

import asyncio
import copy
import io
import logging
import re
import uuid
import zipfile
from collections.abc import Coroutine
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import asyncssh
from pydantic import SecretStr
from sqlalchemy import func, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import agent_provisioning as prov
from app.core.config import Settings
from app.core.errors import ApiError
from app.domain.agent_server_credentials import (
    ServerCredentialError,
    credential_changed,
    resolve_server_password,
)
from app.domain.agents import AgentService, hash_token
from app.domain.audit import AuditTrail
from app.domain.credentials import CredentialBox, CredentialError
from app.domain.host_metrics import drop_if_empty_auto
from app.integrations import ssh
from app.models import (
    AgentCredential,
    AgentDeployment,
    AgentInstance,
    AgentOpsAction,
    AgentSshJob,
    AgentSshTarget,
    RunnerSshJob,
)

logger = logging.getLogger("app.agent_ssh")

LOG_LIMIT = 64 * 1024
DOCKER_TIMEOUT = 900.0
PULL_TIMEOUT = 1200.0
PLUGIN_TIMEOUT = 900.0
START_TIMEOUT = 300.0
# 冷启动 2 ~ 4 分钟（doctor 联网检查插件更新，OC-0 ADR F5）；最多等 15 分钟
ONLINE_TIMEOUT = 15 * 60.0
ONLINE_POLL = 5.0
# 每晚复盘任务（T-0100）：实例刚连上工作台时 OpenClaw 的网关可能还没就绪（冷启动 2 ~ 4 分钟）。
# 脚本每 10 秒重试，最多 10 分钟：一台服务器上有多个实例时冷启动更慢（T-0148 的 CI 里
# 两个实例加全栈在 4 核机器上，重新部署后 4 分半还没就绪）；只在慢的时候多等，实例已经在线
REFLECTION_TRIES = 60
REFLECTION_WAIT = 10
# 启动时把上次没跑完的任务标为中断，最多等这么久（不拖慢 API 启动）
RECOVER_TIMEOUT = 5.0
# 自动安装 Docker 支持的发行版与架构（uname -m）；ADR 第 5.3 节
SUPPORTED_OS = {("ubuntu", "22.04"), ("ubuntu", "24.04"), ("debian", "12")}
SUPPORTED_ARCH = {"x86_64", "aarch64"}

_PEM = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----", re.S
)
_TOKEN = re.compile(r"mwa_[A-Za-z0-9_\-]+")
# 远端脚本失败时打印在标准错误里的原因（files/*.sh）
_SCRIPT_REASONS = {
    "unsupported_os": "发行版不在自动安装 Docker 的支持范围内（Ubuntu 22.04 / 24.04、Debian 12）",
    "unsupported_arch": "架构不在自动安装 Docker 的支持范围内（amd64、arm64）",
    "bad_key_fingerprint": "Docker 软件源的签名公钥指纹不对，已停止安装",
    "docker_not_running": "Docker 装好了但守护进程没有启动",
    "plugin_sha256_mismatch": "openclaw-channel 发布包的 sha256 与锁定值不一致，已停止安装",
}


class InstallFailed(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class JobGone(Exception):
    """任务记录不在了，或已经不是 running（实例 / 目标被删除，或被标记为中断）。"""


class OneTimePassword:
    """登录密码只在密码阶段使用：用完 clear()，之后任务里不再有对它的引用（运行服务器的部署用）。"""

    def __init__(self, value: SecretStr | None) -> None:
        self._value = value

    def get(self) -> SecretStr | None:
        return self._value

    def clear(self) -> None:
        self._value = None


def _now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------- 进度与日志


class JobRecorder:
    """把步骤状态与脱敏后的输出写回任务记录。"""

    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        job_id: uuid.UUID,
        secrets: list[str] | None = None,
        steps: tuple[str, ...] = prov.STEPS,
        model: Any = AgentSshJob,
    ) -> None:
        self.sessionmaker = sessionmaker
        self.job_id = job_id
        # 任务表：OpenClaw 实例是 agent_ssh_jobs，运行服务器（T-0136）是 runner_ssh_jobs，列相同
        self.model = model
        self.steps: list[dict[str, Any]] = [
            {"key": key, "status": "pending", "detail": None} for key in steps
        ]
        self.log = ""
        self._secrets: list[str] = []
        for value in secrets or []:
            self.add_secret(value)

    def add_secret(self, value: str | None) -> None:
        # 太短的值（例如空串、一两个字符）替换掉会把日志弄乱，也不会是真正的凭证
        if value and len(value) >= 4 and value not in self._secrets:
            self._secrets.append(value)
            self._secrets.sort(key=len, reverse=True)

    def redact(self, text: str) -> str:
        for value in self._secrets:
            text = text.replace(value, "***")
        text = _PEM.sub("***", text)
        return _TOKEN.sub("mwa_***", text)

    def write(self, text: str) -> None:
        if not text:
            return
        if not text.endswith("\n"):
            text += "\n"
        self.log = (self.log + self.redact(text))[-LOG_LIMIT:]

    def forget(self, value: str | None) -> None:
        if value in self._secrets:
            self._secrets.remove(value)

    def output(self, label: str, result: ssh.CommandResult) -> None:
        body = (result.stdout + result.stderr).strip()
        head = f"$ {label}（退出码 {result.exit_status}）"
        self.write(f"{head}\n{body[-8000:]}" if body else head)

    @property
    def current(self) -> str | None:
        for step in self.steps:
            if step["status"] == "running":
                return str(step["key"])
        return None

    async def step(self, key: str, status: str, detail: str | None = None) -> None:
        for step in self.steps:
            if step["key"] == key:
                step["status"] = status
                step["detail"] = self.redact(detail) if detail else None
        await self.flush()

    async def flush(self, **values: Any) -> None:
        async with self.sessionmaker() as db:
            result = await db.execute(
                update(self.model)
                .where(self.model.id == self.job_id, self.model.status == "running")
                .values(
                    steps=copy.deepcopy(self.steps),
                    log=self.log,
                    updated_at=func.now(),
                    **values,
                )
            )
            await db.commit()
        if getattr(result, "rowcount", 0) == 0:
            raise JobGone


# ---------------------------------------------------------------- 目标服务器的信息


@dataclass(frozen=True)
class Target:
    job_id: uuid.UUID
    kind: str
    target_id: uuid.UUID
    instance_id: uuid.UUID
    workspace_id: uuid.UUID
    started_by: uuid.UUID | None
    address: str
    port: int
    username: str
    host_key: str
    client_key_ref: str | None
    host_key_confirmed: bool
    # 一台服务器多个实例（T-0145）：OpenClaw 服务器的 ID；准备服务器的任务里 instance_id 也填它
    # （只用作审计的资源 ID）。运行服务器的部署不用这个字段
    server_id: uuid.UUID | None = None
    # OpenClaw 服务器（T-0179）：任务开始时登录密码的版本；每次登录前核对，变了就停止。
    # 运行服务器不用这个字段
    credential_revision: int | None = None


@dataclass(frozen=True)
class HostInfo:
    os_id: str
    os_version: str
    arch: str
    uid: int
    gid: int
    user: str
    home: str
    docker: bool
    docker_access: bool
    compose: bool
    sudo: bool
    crontab: bool

    @property
    def root(self) -> bool:
        return self.uid == 0

    @property
    def docker_ready(self) -> bool:
        return self.docker_access and self.compose

    @property
    def supported(self) -> bool:
        return (self.os_id, self.os_version) in SUPPORTED_OS and self.arch in SUPPORTED_ARCH


def parse_inspect(stdout: str) -> HostInfo:
    values: dict[str, str] = {}
    for line in stdout.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            values[key.strip()] = value.strip()
    try:
        home = values["home"]
        uid, gid = int(values["uid"]), int(values["gid"])
    except (KeyError, ValueError) as error:
        raise InstallFailed("inspect_failed", "读取服务器信息失败") from error
    if not home.startswith("/") or any(c in home for c in "\n\r\0"):
        raise InstallFailed("inspect_failed", "登录账号的主目录无效")
    yes = {key: values.get(key) == "yes" for key in ("docker", "docker_access", "compose")}
    return HostInfo(
        os_id=values.get("os_id", ""),
        os_version=values.get("os_version", ""),
        arch=values.get("arch", ""),
        uid=uid,
        gid=gid,
        user=values.get("user", ""),
        # 末尾不带 /（主目录是 / 时为空串）：路径都写成 f"{home}/..."
        home=home.rstrip("/"),
        docker=yes["docker"],
        docker_access=yes["docker_access"],
        compose=yes["compose"],
        sudo=values.get("sudo") == "yes",
        crontab=values.get("crontab") == "yes",
    )


def unzip_bundle(data: bytes) -> dict[str, tuple[bytes, int]]:
    """接入包 zip → {相对路径: (内容, 权限)}。路径由工作台自己生成，这里仍然拒绝 .. 与绝对路径。"""
    files: dict[str, tuple[bytes, int]] = {}
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for info in zf.infolist():
            name = info.filename
            if info.is_dir():
                continue
            if name.startswith("/") or ".." in name.split("/"):
                raise InstallFailed("bundle_invalid", "接入包里的路径无效")
            mode = (info.external_attr >> 16) & 0o777 or 0o644
            files[name] = (zf.read(info), mode)
    return files


def bundle_secrets(files: dict[str, tuple[bytes, int]]) -> list[str]:
    """接入包里的凭证（日志里一律替换成 ***）。"""
    found: list[str] = []
    env = files.get("myworkspace.env")
    if env is not None:
        for line in env[0].decode("utf-8", "replace").splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip() in {"MYWS_INSTANCE_TOKEN", "OPENCLAW_GATEWAY_TOKEN"}:
                found.append(value.strip())
    client_key = files.get("tls/client.key")
    if client_key is not None:
        found.append(client_key[0].decode("utf-8", "replace").strip())
    return found


def bundle_token(files: dict[str, tuple[bytes, int]]) -> str:
    env = files.get("myworkspace.env")
    if env is not None:
        for line in env[0].decode("utf-8", "replace").splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip() == "MYWS_INSTANCE_TOKEN":
                return value.strip()
    raise InstallFailed("bundle_invalid", "接入包里没有实例凭证")


def _script_failure(result: ssh.CommandResult, default_code: str, default: str) -> InstallFailed:
    text = result.stderr + result.stdout
    for code, message in _SCRIPT_REASONS.items():
        if code in text:
            return InstallFailed(code, message)
    return InstallFailed(default_code, default)


# ---------------------------------------------------------------- 公共部分


class Installer:
    """经 SSH 执行任务的公共部分：执行命令并记日志、只读检查、确认目标服务器没有被更换。

    OpenClaw 服务器（`AgentServerInstaller`，T-0179 起全程用保存的登录密码）与运行服务器
    （`runner_ssh.RunnerInstaller`，仍是“一次密码 + 专用密钥”，T-0136）共用。
    专用密钥相关的方法（`key_comment` / `_existing_key` / `_save_key` / `_authorize`）
    只给运行服务器用：
    运行服务器的子类提供 `_existing_key` / `_save_key`，OpenClaw 服务器的子类把它们全部禁用。
    """

    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        settings: Settings,
        recorder: JobRecorder,
        target: Target,
        *,
        client_ip: str | None,
    ) -> None:
        self.sessionmaker = sessionmaker
        self.settings = settings
        self.rec = recorder
        self.target = target
        self.client_ip = client_ip
        self.home = ""

    def _unchanged(self, target: AgentSshTarget) -> bool:
        """任务开始后目标服务器没有被更换（地址、端口、账号、主机公钥都一样且仍然确认）。"""
        t = self.target
        return (
            target.address == t.address
            and target.port == t.port
            and target.username == t.username
            and target.host_key == t.host_key
            and target.host_key_confirmed_at is not None
        )

    def key_comment(self) -> str:
        """专用公钥在 authorized_keys 里的注释（运行服务器的子类覆盖）。"""
        return f"myworkspace-{str(self.target.instance_id)[:8]}"

    def _box(self) -> CredentialBox:
        try:
            return CredentialBox.from_settings(self.settings)
        except CredentialError as error:
            raise InstallFailed("credentials_unavailable", "密钥库未配置加密密钥") from error

    async def _run(
        self,
        conn: asyncssh.SSHClientConnection,
        label: str,
        command: str,
        *,
        stdin: str | None = None,
        seconds: float = ssh.COMMAND_TIMEOUT,
        log: bool = True,
    ) -> ssh.CommandResult:
        result = await ssh.run(conn, command, stdin=stdin, seconds=seconds)
        if log:
            self.rec.output(label, result)
        return result

    async def _connect(
        self, *, password: SecretStr | None = None, key: asyncssh.SSHKey | None = None
    ) -> asyncssh.SSHClientConnection:
        t = self.target
        return await ssh.connect(
            t.address,
            t.port,
            t.username,
            t.host_key,
            password=password.get_secret_value() if password is not None else None,
            client_key=key,
        )

    async def _inspect(self, conn: asyncssh.SSHClientConnection) -> HostInfo:
        await self.rec.step("inspect", "running")
        result = await self._run(conn, "检查服务器", "sh -s", stdin=prov.script("inspect.sh"))
        if not result.ok:
            raise InstallFailed("inspect_failed", "读取服务器信息失败")
        info = parse_inspect(result.stdout)
        detail = (
            f"{info.os_id} {info.os_version} {info.arch}，账号 {info.user}（uid {info.uid}）；"
            + ("Docker 可以直接使用" if info.docker_ready else "需要安装或配置 Docker")
        )
        await self.rec.step("inspect", "done", detail)
        return info

    def _existing_key(self) -> asyncssh.SSHKey | None:
        """已保存的专用私钥（运行服务器的子类提供；用途绑定各自的记录）。"""
        raise NotImplementedError

    async def _save_key(self, key: asyncssh.SSHKey, public: str) -> None:
        """加密保存新生成的专用私钥（运行服务器的子类提供）。"""
        raise NotImplementedError

    async def _authorize(
        self, conn: asyncssh.SSHClientConnection, key: asyncssh.SSHKey | None
    ) -> asyncssh.SSHKey:
        """装专用公钥（运行服务器的部署）：没有就生成一把，写进 authorized_keys 后加密保存私钥。"""
        await self.rec.step("authorize_key", "running")
        fresh = key is None
        if key is None:
            key = asyncssh.generate_private_key("ssh-ed25519", comment=self.key_comment())
        public = key.export_public_key().decode().strip()
        result = await self._run(
            conn, "安装专用公钥", prov.authorize_key_command(public), seconds=30
        )
        if not result.ok:
            raise InstallFailed("authorize_failed", "写入 ~/.ssh/authorized_keys 失败")
        if fresh:
            await self._save_key(key, public)
        await self.rec.step(
            "authorize_key",
            "done",
            ("已生成并安装专用公钥" if fresh else "专用公钥已在服务器上")
            + f"（{key.get_fingerprint('sha256')}）",
        )
        return key


# ---------------------------------------------------------------- OpenClaw 服务器（T-0179）


_CREDENTIAL_CODES = {
    "credentials_required": "credential_required",
    "credential_unreadable": "credential_unreadable",
    "credential_changed": "credential_changed",
}


def _credential_failed(error: ServerCredentialError) -> InstallFailed:
    """保存的登录密码不能用（没保存 / 解不开 / 任务开始后被替换）：任务失败，不回退到别的凭证。"""
    return InstallFailed(
        _CREDENTIAL_CODES.get(error.reason, "credential_unreadable"), error.message
    )


class AgentServerInstaller(Installer):
    """OpenClaw 服务器的初始化与部署：全程用保存的登录密码登录（T-0179）。

    - 每次登录前重新读取服务器的 SSH 记录：地址、账号、主机公钥被更换或取消确认就停止；
      按任务开始时的凭证版本（`credential_revision`）解密密码，密码中途被替换就停止；
    - 登录只用 `agent_server_ssh.connect_agent_server`（只用密码、固定主机公钥，不用 ssh-agent、
      默认私钥或 ~/.ssh/config）；不生成、不安装、不读取 SSH 客户端密钥（旧方案留下的专用私钥
      连任务记录都不进，见 `_load`）；
    - sudo 用同一个登录密码，只经标准输入；需要另外的 sudo 密码或交互式验证（MFA）的服务器明确失败。
    """

    @property
    def revision(self) -> int:
        revision = self.target.credential_revision
        if revision is None:  # 任务入口总会填；没有就当作密码已变
            raise _credential_failed(credential_changed())
        return revision

    async def _current(self) -> AgentSshTarget:
        """这台服务器现在的 SSH 记录（每次登录前读）。被删了安静结束；被更换了停止。"""
        async with self.sessionmaker() as db:
            target = await db.get(AgentSshTarget, self.target.target_id, populate_existing=True)
        if target is None:
            raise JobGone
        if not self._unchanged(target):
            raise InstallFailed(
                "target_changed", "服务器的地址、账号或主机公钥在任务期间被更换，已停止"
            )
        return target

    async def _password(self, target: AgentSshTarget | None = None) -> SecretStr:
        """临用时解密保存的登录密码（按任务开始时的凭证版本）；之后写日志时替换成 ***。"""
        current = target if target is not None else await self._current()
        try:
            password = resolve_server_password(
                self._box(), current, expected_revision=self.revision
            )
        except ServerCredentialError as error:
            raise _credential_failed(error) from error
        self.rec.add_secret(password.get_secret_value())
        return password

    async def _login(self) -> asyncssh.SSHClientConnection:
        """用保存的登录密码登录（只用密码，按确认过的主机公钥）。"""
        # 延迟导入：agent_server_ssh 引用了本模块的 HostInfo / parse_inspect
        from app.domain import agent_server_ssh

        target = await self._current()
        await self._password(target)
        try:
            return await agent_server_ssh.connect_agent_server(
                target, self._box(), expected_revision=self.revision
            )
        except ServerCredentialError as error:
            raise _credential_failed(error) from error

    async def _connect(
        self, *, password: SecretStr | None = None, key: asyncssh.SSHKey | None = None
    ) -> asyncssh.SSHClientConnection:
        # OpenClaw 服务器不接受外部给的密码或密钥：只用保存的密码
        if password is not None or key is not None:
            raise InstallFailed("internal_error", "OpenClaw 服务器只用保存的登录密码登录")
        return await self._login()

    def _existing_key(self) -> asyncssh.SSHKey | None:
        raise InstallFailed("internal_error", "OpenClaw 服务器不使用 SSH 客户端密钥")

    async def _save_key(self, key: asyncssh.SSHKey, public: str) -> None:
        raise InstallFailed("internal_error", "OpenClaw 服务器不使用 SSH 客户端密钥")

    async def _authorize(
        self, conn: asyncssh.SSHClientConnection, key: asyncssh.SSHKey | None
    ) -> asyncssh.SSHKey:
        raise InstallFailed("internal_error", "OpenClaw 服务器不安装 SSH 公钥")

    def _check_confirmed(self) -> None:
        if not self.target.host_key_confirmed:
            # 接口里已经检查过；任务开始时再确认一次，未确认的主机公钥绝不用来登录
            raise InstallFailed("host_key_unconfirmed", "服务器的主机公钥还没有确认")

    async def _connect_step(self) -> asyncssh.SSHClientConnection:
        await self.rec.step("connect", "running")
        conn = await self._login()
        await self.rec.step(
            "connect", "done", "用保存的登录密码登录（主机公钥已固定，不使用 SSH 密钥）"
        )
        return conn

    # ---------------------------------------------------------------- 初始化

    async def prepare(self) -> None:
        """初始化服务器：只准备基础环境与平台受控的部署根目录，不建实例、不发接入包、不装公钥。"""
        self._check_confirmed()
        conn: asyncssh.SSHClientConnection | None = None
        try:
            conn = await self._connect_step()
            info = await self._inspect(conn)
            self.home = info.home
            installed = await self._docker(conn, info)
            if installed:
                # 新会话里 docker 组才生效：断开后重新用密码登录
                await self.rec.step("reconnect", "running")
                conn.close()
                await conn.wait_closed()
                conn = None
                conn = await self._login()
                check = await self._run(
                    conn,
                    "检查 Docker",
                    "docker info --format '{{.ServerVersion}}' && docker compose version --short",
                    seconds=60,
                )
                if not check.ok:
                    raise InstallFailed(
                        "docker_unavailable", "重新登录后仍然不能使用 Docker（docker 组没有生效）"
                    )
                await self.rec.step("reconnect", "done", "重新用密码登录，Docker 可用")
            else:
                await self.rec.step("reconnect", "skipped", "Docker 本来就可以使用，不用重新登录")
            root = prov.deploy_root(info.home)
            await self.rec.step("workspace", "running")
            result = await self._run(conn, "准备部署目录", f"mkdir -p {prov.q(root)}", seconds=30)
            if not result.ok:
                raise InstallFailed("workspace_failed", f"无法创建部署目录 {root}")
            await self.rec.step("workspace", "done", f"{root}（每个智能体在下面一个子目录）")
            await self._verify(conn, root)
            await self._mark_prepared()
        finally:
            if conn is not None:
                conn.close()

    async def _docker(self, conn: asyncssh.SSHClientConnection, info: HostInfo) -> bool:
        """缺 Docker / Compose（或账号用不了）才按锁定脚本安装或配置；已经能用就沿用，不升级。

        返回是否执行了安装脚本（之后要重新登录，docker 组才生效）。
        """
        if info.docker_ready:
            await self.rec.step(
                "docker", "skipped", "已经可以使用 Docker 与 Compose（沿用现有版本，不升级）"
            )
            return False
        await self.rec.step("docker", "running")
        if (not info.docker or not info.compose) and not info.supported:
            raise InstallFailed(
                "unsupported_os",
                "自动安装 Docker 只支持 Ubuntu 22.04 / 24.04、Debian 12（amd64、arm64）；"
                "请先手动安装 Docker 与 Compose 插件，再重新初始化",
            )
        secret: str | None = None
        if not info.root:
            if not info.sudo:
                raise InstallFailed(
                    "sudo_unavailable", "服务器上没有 sudo，无法以 root 安装或配置 Docker"
                )
            secret = (await self._password()).get_secret_value() + "\n"
            check = await self._run(
                conn, "检查 sudo", "sudo -S -p '' -k true", stdin=secret, seconds=30
            )
            if not check.ok:
                raise InstallFailed(
                    "sudo_denied",
                    "这个账号不能用登录密码执行 sudo：需要另外的 sudo 密码"
                    "或交互式验证（MFA）的服务器不支持自动初始化；请让这个账号能用登录密码 sudo，"
                    "或先手动安装 Docker 并把账号加入 docker 组",
                )
        staging = prov.staging_dir(info.home, str(self.target.instance_id))
        path = f"{staging}/install-docker.sh"
        await self._run(
            conn, "准备安装脚本", f"mkdir -p {prov.q(staging)} && chmod 700 {prov.q(staging)}"
        )
        await ssh.put_files(
            conn, staging, {"install-docker.sh": (prov.script("install-docker.sh").encode(), 0o700)}
        )
        user = "" if info.root else info.user
        command = f"bash {prov.q(path)} {prov.q(user)}"
        try:
            result = await self._run(
                conn,
                "安装 Docker",
                command if info.root else f"sudo -S -p '' -k {command}",
                stdin=secret,
                seconds=DOCKER_TIMEOUT,
            )
        finally:
            secret = None
            await self._run(conn, "删除安装脚本", f"rm -rf {prov.q(staging)}", log=False)
        if not result.ok:
            raise _script_failure(result, "docker_install_failed", "安装 Docker 失败，详见日志")
        version = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
        await self.rec.step("docker", "done", f"Docker {version}".strip())
        return True

    async def _verify(self, conn: asyncssh.SSHClientConnection, root: str) -> None:
        """最终检查：Docker 可用、部署目录可写、能连到工作台的外网接入地址。"""
        await self.rec.step("verify", "running")
        ingress = prov.ingress_target(self.settings.agent_public_url)
        result = await self._run(
            conn,
            "检查环境",
            prov.verify_command(root, ingress),
            stdin=prov.script("verify.sh"),
            seconds=60,
        )
        values = _key_values(result.stdout)
        if not result.ok or not values:
            raise InstallFailed("verify_failed", "最终检查没有完成，详见日志")
        if values.get("docker") != "yes" or values.get("compose") != "yes":
            raise InstallFailed("docker_unavailable", "Docker 或 Compose 插件不能使用")
        if values.get("workspace") != "yes":
            raise InstallFailed("workspace_failed", f"部署目录 {root} 不可写")
        notes = ["Docker 可用", "部署目录可写"]
        reach = values.get("ingress")
        if reach == "no" and ingress is not None:
            raise InstallFailed(
                "ingress_unreachable",
                f"服务器连不上工作台的接入地址 {ingress[0]}:{ingress[1]}："
                "检查服务器的出站网络、防火墙与 DNS",
            )
        if reach == "yes" and ingress is not None:
            notes.append(f"能连到工作台的接入地址 {ingress[0]}:{ingress[1]}")
        elif ingress is None:
            notes.append("工作台还没有配置外网接入入口（AGENT_PUBLIC_URL），部署前要先配置")
        else:
            notes.append("服务器上没有 bash / timeout 或 nc，没能检查能否连到工作台的接入地址")
        await self.rec.step("verify", "done", "；".join(notes))

    async def _mark_prepared(self) -> None:
        """初始化成功：记下时间（之后可以在这台服务器上部署实例）。"""
        async with self.sessionmaker() as db:
            target = await db.get(AgentSshTarget, self.target.target_id, with_for_update=True)
            if target is None:
                raise JobGone
            if not self._unchanged(target):
                raise InstallFailed("target_changed", "服务器的地址在任务期间被更换，已停止")
            if target.credential_revision != self.revision:
                raise _credential_failed(credential_changed())
            target.prepared_at = _now()
            target.version += 1
            await db.commit()

    # ---------------------------------------------------------------- 部署

    async def deploy(self) -> None:
        """部署一个实例：环境预检（服务器要已初始化），只替换这个实例自己的目录与 Compose 项目。"""
        self._check_confirmed()
        conn: asyncssh.SSHClientConnection | None = None
        try:
            conn = await self._connect_step()
            await self.rec.step("inspect", "running")
            result = await self._run(conn, "检查服务器", "sh -s", stdin=prov.script("inspect.sh"))
            if not result.ok:
                raise InstallFailed("inspect_failed", "读取服务器信息失败")
            info = parse_inspect(result.stdout)
            if not info.docker_ready:
                raise InstallFailed(
                    "docker_unavailable",
                    "这个账号现在不能直接使用 Docker 与 Compose：先在“智能体服务器”页重新初始化",
                )
            await self.rec.step(
                "inspect",
                "done",
                f"{info.os_id} {info.os_version} {info.arch}，账号 {info.user}（uid {info.uid}）；"
                "Docker 可以直接使用",
            )
            self.home = info.home
            await self._deploy(conn, info)
        finally:
            if conn is not None:
                conn.close()

    async def _issue_bundle(self) -> dict[str, tuple[bytes, int]]:
        async with self.sessionmaker() as db:
            instance = await db.get(AgentInstance, self.target.instance_id)
            if instance is None:
                raise JobGone
            try:
                _, data = await AgentService(db, self.settings).build_bundle(
                    instance,
                    previous="keep_24h",
                    actor_id=self.target.started_by,
                    client_ip=self.client_ip,
                )
            except ApiError as error:
                raise InstallFailed(error.code.lower(), error.message) from error
        return unzip_bundle(data)

    async def _wait_online(self, token: str, conn: asyncssh.SSHClientConnection) -> None:
        """新签发的实例凭证被用过一次，就说明新部署的 OpenClaw 连上了工作台。"""
        await self.rec.step("online", "running", "等待实例连上工作台（冷启动 2 ~ 4 分钟）")
        digest = hash_token(token)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + ONLINE_TIMEOUT
        while True:
            async with self.sessionmaker() as db:
                used = await db.scalar(
                    select(AgentCredential.last_used_at).where(
                        AgentCredential.token_hash == digest,
                        AgentCredential.instance_id == self.target.instance_id,
                    )
                )
            if used is not None:
                break
            if loop.time() >= deadline:
                directory = prov.install_dir(self.home, str(self.target.instance_id))
                for label, args in (
                    ("容器状态", ("ps",)),
                    ("OpenClaw 日志", ("logs", "--tail", "60", "openclaw")),
                ):
                    try:
                        await self._run(conn, label, prov.compose_command(directory, *args))
                    except ssh.SshError:
                        break
                raise InstallFailed(
                    "not_online",
                    "已经启动，但 15 分钟内没有连上工作台；在服务器上运行"
                    " docker compose logs openclaw 查看原因",
                )
            await self.rec.flush()
            await asyncio.sleep(ONLINE_POLL)
        await self.rec.step("online", "done", "实例已连上工作台")

    async def _deploy(self, conn: asyncssh.SSHClientConnection, info: HostInfo) -> None:
        directory = prov.install_dir(info.home, str(self.target.instance_id))
        await self.rec.step("upload", "running")
        files = await self._issue_bundle()
        for value in bundle_secrets(files):
            self.rec.add_secret(value)
        token = bundle_token(files)
        prepare = await self._run(
            conn, "准备目录", f"mkdir -p {prov.q(directory)} && chmod 700 {prov.q(directory)}"
        )
        if not prepare.ok:
            raise InstallFailed("upload_failed", f"无法创建目录 {directory}")
        reclaim = prov.reclaim_command(directory, uid=info.uid, gid=info.gid)
        if reclaim is not None:
            result = await self._run(conn, "收回目录属主", reclaim, seconds=PULL_TIMEOUT)
            if not result.ok:
                raise InstallFailed("upload_failed", "无法收回上次安装的文件属主")
        await ssh.put_files(conn, directory, files)
        await self.rec.step("upload", "done", f"接入包已上传到 {directory}")

        await self.rec.step("pull", "running")
        pull = prov.compose_command(directory, "pull", "--quiet")
        result = await self._run(conn, "拉取镜像", pull, seconds=PULL_TIMEOUT)
        if not result.ok:
            raise InstallFailed("pull_failed", "拉取镜像失败，详见日志")
        await self.rec.step("pull", "done")

        await self.rec.step("plugin", "running")
        handover = prov.handover_command(directory, uid=info.uid)
        if handover is not None:
            result = await self._run(conn, "目录交给 uid 1000", handover, seconds=START_TIMEOUT)
            if not result.ok:
                raise InstallFailed("plugin_failed", "无法把 state/、bin/、tls/ 交给 uid 1000")
        # 安装插件要联网：先起这个实例的网络命名空间（net 服务，只运行 sleep）
        result = await self._run(
            conn, "启动实例网络", prov.compose_command(directory, "up", "-d", "net"), seconds=120
        )
        if not result.ok:
            raise InstallFailed("plugin_failed", "无法启动这个实例的网络（net 服务），详见日志")
        result = await self._run(
            conn,
            "安装 openclaw-channel",
            prov.compose_command(
                directory, "run", "--rm", "--no-deps", "-T", "--entrypoint", "sh", "openclaw", "-s"
            ),
            stdin=prov.plugin_script(),
            seconds=PLUGIN_TIMEOUT,
        )
        if not result.ok:
            raise _script_failure(result, "plugin_failed", "安装 openclaw-channel 失败，详见日志")
        # 补丁（T-0120）：已经装好的插件也在这一步补上；
        # 重新部署换了凭证，up -d 会重建容器，补丁随之生效
        patched = "plugin_patch_applied" in result.stdout
        if "plugin_already_installed" in result.stdout and not patched:
            await self.rec.step("plugin", "skipped", f"已经安装 {prov.PLUGIN_PACKAGE}（已打补丁）")
        elif "plugin_already_installed" in result.stdout:
            await self.rec.step(
                "plugin", "done", f"已经安装 {prov.PLUGIN_PACKAGE}，这次打上了工作台的补丁"
            )
        else:
            await self.rec.step("plugin", "done", f"{prov.PLUGIN_PACKAGE}（已打补丁）")

        await self.rec.step("start", "running")
        result = await self._run(
            conn, "启动", prov.compose_command(directory, "up", "-d"), seconds=START_TIMEOUT
        )
        if not result.ok:
            raise InstallFailed("start_failed", "启动失败，详见日志")
        await self.rec.step("start", "done")

        if info.crontab:
            await self.rec.step("schedule", "running")
            result = await self._run(
                conn,
                "定时任务",
                prov.schedule_command(directory, str(self.target.instance_id)),
                seconds=30,
            )
            if not result.ok:
                raise InstallFailed("schedule_failed", "写入 crontab 失败")
            await self.rec.step(
                "schedule",
                "done",
                "每分钟上报用量与服务器状态、领取唤醒与终止令，每 10 分钟取密钥、每周一重启",
            )
        else:
            await self.rec.step(
                "schedule",
                "skipped",
                "服务器上没有 crontab：按接入包 CHECKLIST.md 手动配置定时任务",
            )

        await self._wait_online(token, conn)
        await self._reflection(conn, directory)
        async with self.sessionmaker() as db:
            target = await db.get(AgentSshTarget, self.target.target_id, with_for_update=True)
            if target is None:
                raise JobGone
            if not self._unchanged(target):
                raise InstallFailed("target_changed", "服务器的地址在部署期间被更换，已停止")
            deployment = await db.scalar(
                select(AgentDeployment)
                .where(AgentDeployment.instance_id == self.target.instance_id)
                .with_for_update()
            )
            if deployment is None or deployment.server_id != target.server_id:
                raise InstallFailed("deployment_changed", "部署记录在部署期间被改动，已停止")
            deployment.install_dir = directory
            deployment.installed_at = _now()
            # 真实证据（T-0180）：这次部署成功、容器装着（不代表此刻仍在运行）
            deployment.runtime_installed = True
            deployment.version += 1
            # 经 SSH 部署的实例归到这台服务器（以部署为准，不再按指纹自动改）；
            # 部署过程中按指纹自动建的那台空了就删掉
            previous = await db.scalar(
                select(AgentInstance.server_id).where(AgentInstance.id == self.target.instance_id)
            )
            await db.execute(
                update(AgentInstance)
                .where(AgentInstance.id == self.target.instance_id)
                .values(server_id=target.server_id, server_manual=True)
            )
            if previous != target.server_id:
                await drop_if_empty_auto(db, previous)
            await db.commit()

    async def _reflection(self, conn: asyncssh.SSHClientConnection, directory: str) -> None:
        """每晚复盘的定时任务（知识库方案 3.9，T-0100）：同名任务已有就更新，不重复建。

        建不成不算安装失败（实例已在线）：步骤记为跳过，原因在日志里，按 CHECKLIST 手动运行。
        """
        await self.rec.step("reflection", "running", "等 OpenClaw 的网关就绪后建定时任务")
        result = await self._run(
            conn,
            "每晚复盘任务",
            prov.reflection_command(directory, REFLECTION_TRIES, REFLECTION_WAIT),
            seconds=REFLECTION_TRIES * (REFLECTION_WAIT + 30) + 60,
        )
        if result.ok:
            updated = '"created": false' in result.stdout
            await self.rec.step(
                "reflection", "done", "已有，已更新提示词与时间" if updated else "已建好"
            )
        else:
            await self.rec.step(
                "reflection",
                "skipped",
                "没建成（详见日志）：在服务器的接入包目录里运行 sh setup-reflection.sh",
            )


class AgentServerUninstaller(AgentServerInstaller):
    """卸载（B3；T-0179 起用保存的登录密码）：停止容器、删掉 crontab 里带标记的行，需要时删除目录。

    不碰 Docker 本身、authorized_keys 与实例凭证（实例页可以吊销）。
    """

    async def uninstall(self, *, remove_files: bool) -> None:
        self._check_confirmed()
        conn: asyncssh.SSHClientConnection | None = None
        removed = False
        try:
            conn = await self._connect_step()
            info = await self._inspect(conn)
            directory = prov.install_dir(info.home, str(self.target.instance_id))

            await self.rec.step("stop", "running")
            present = await self._run(
                conn, "检查目录", f"[ -f {prov.q(directory)}/compose.yaml ]", log=False
            )
            if present.ok:
                result = await self._run(
                    conn,
                    "停止",
                    prov.compose_command(directory, "down", "--remove-orphans"),
                    seconds=START_TIMEOUT,
                )
                if not result.ok:
                    raise InstallFailed("stop_failed", "停止容器失败，详见日志")
                await self.rec.step("stop", "done", "已停止并删除容器")
            else:
                await self.rec.step("stop", "skipped", f"服务器上没有 {directory}")

            if info.crontab:
                await self.rec.step("unschedule", "running")
                result = await self._run(
                    conn,
                    "删除定时任务",
                    prov.unschedule_command(str(self.target.instance_id)),
                    seconds=30,
                )
                if not result.ok:
                    raise InstallFailed("schedule_failed", "修改 crontab 失败")
                await self.rec.step("unschedule", "done", "已删除这个实例的定时任务")
            else:
                await self.rec.step("unschedule", "skipped", "服务器上没有 crontab")

            exists = await self._run(conn, "检查目录", f"[ -d {prov.q(directory)} ]", log=False)
            if not remove_files or not exists.ok:
                await self.rec.step(
                    "remove_files",
                    "skipped",
                    f"保留 {directory}（state/ 里是 OpenClaw 的会话与记忆）"
                    if exists.ok
                    else f"服务器上没有 {directory}",
                )
            else:
                await self.rec.step("remove_files", "running")
                reclaim = prov.reclaim_command(directory, uid=info.uid, gid=info.gid)
                if reclaim is not None:
                    result = await self._run(conn, "收回目录属主", reclaim, seconds=PULL_TIMEOUT)
                    if not result.ok:
                        raise InstallFailed("remove_failed", "无法收回目录属主，详见日志")
                result = await self._run(
                    conn, "删除目录", prov.remove_dir_command(directory), seconds=60
                )
                if not result.ok:
                    raise InstallFailed("remove_failed", f"删除 {directory} 失败，详见日志")
                removed = True
                await self.rec.step("remove_files", "done", f"已删除 {directory}")
        finally:
            if conn is not None:
                conn.close()
        async with self.sessionmaker() as db:
            target = await db.get(AgentSshTarget, self.target.target_id, with_for_update=True)
            if target is None:
                raise JobGone
            if not self._unchanged(target):
                raise InstallFailed("target_changed", "服务器的地址在卸载期间被更换，已停止")
            deployment = await db.scalar(
                select(AgentDeployment)
                .where(AgentDeployment.instance_id == self.target.instance_id)
                .with_for_update()
            )
            if deployment is not None and removed:
                # 目录删掉了：这台服务器上不再有这个实例（之后按指纹自动识别）
                await db.delete(deployment)
                await db.execute(
                    update(AgentInstance)
                    .where(
                        AgentInstance.id == self.target.instance_id,
                        AgentInstance.server_id == target.server_id,
                    )
                    .values(server_id=None, server_manual=False)
                )
            elif deployment is not None:
                # 容器卸载了、目录保留（T-0180 的 retained）：真实证据是“容器不在了”
                deployment.installed_at = None
                deployment.runtime_installed = False
                deployment.version += 1
            await db.commit()


def _key_values(stdout: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in stdout.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            values[key.strip()] = value.strip()
    return values


# ---------------------------------------------------------------- 任务入口


async def _load(
    sessionmaker: async_sessionmaker[AsyncSession],
    job_id: uuid.UUID,
    expected_revision: int | None = None,
) -> Target:
    async with sessionmaker() as db:
        row = (
            await db.execute(
                select(AgentSshJob, AgentSshTarget)
                .join(AgentSshTarget, AgentSshTarget.id == AgentSshJob.target_id)
                .where(AgentSshJob.id == job_id, AgentSshJob.status == "running")
            )
        ).first()
    if row is None:
        raise JobGone
    job, target = row
    return Target(
        job_id=job.id,
        kind=job.kind,
        target_id=target.id,
        # 准备服务器的任务没有实例：审计的资源 ID 用服务器
        instance_id=job.instance_id or target.server_id,
        workspace_id=target.workspace_id,
        started_by=job.started_by,
        address=target.address,
        port=target.port,
        username=target.username,
        host_key=target.host_key,
        # T-0179：旧方案留下的专用私钥从不进 OpenClaw 服务器的任务（只用保存的密码登录）
        client_key_ref=None,
        host_key_confirmed=target.host_key_confirmed_at is not None,
        server_id=target.server_id,
        # 接纳任务时记下的版本优先（任务开始前密码被替换也会发现）
        credential_revision=(
            expected_revision if expected_revision is not None else target.credential_revision
        ),
    )


async def _finish(
    sessionmaker: async_sessionmaker[AsyncSession],
    settings: Settings,
    recorder: JobRecorder,
    target: Target,
    *,
    status: str,
    code: str | None = None,
    message: str | None = None,
    client_ip: str | None = None,
    audit: tuple[str, str] = ("agents.ssh", "agent_instance"),
) -> None:
    """写回结果；数据库出错时只记日志（任务记录保持 running，由下一次操作或重启标为中断）。

    audit：审计事件的前缀与资源类型（运行服务器的部署是 runner.ssh / runner_host）。
    """
    try:
        await _write_result(
            sessionmaker,
            settings,
            recorder,
            target,
            status=status,
            code=code,
            message=message,
            client_ip=client_ip,
            audit=audit,
        )
    except (OSError, SQLAlchemyError) as error:
        logger.warning(
            "agent ssh job result not saved",
            extra={"job_id": str(recorder.job_id), "error": type(error).__name__},
        )


async def _write_result(
    sessionmaker: async_sessionmaker[AsyncSession],
    settings: Settings,
    recorder: JobRecorder,
    target: Target,
    *,
    status: str,
    code: str | None = None,
    message: str | None = None,
    client_ip: str | None = None,
    audit: tuple[str, str] = ("agents.ssh", "agent_instance"),
) -> None:
    current = recorder.current
    for step in recorder.steps:
        if step["key"] == current:
            step["status"] = "done" if status == "succeeded" else "failed"
    if message:
        recorder.write(f"！{message}")
    try:
        await recorder.flush(
            status=status,
            error_code=code,
            error_message=recorder.redact(message) if message else None,
            finished_at=func.now(),
        )
    except JobGone:
        return
    async with sessionmaker() as db:
        await AuditTrail(db, settings).record(
            f"{audit[0]}.{target.kind}_{status}",
            workspace_ids=[target.workspace_id],
            actor_id=target.started_by,
            resource_type=audit[1],
            resource_id=target.instance_id,
            metadata={"job_id": str(target.job_id), "error_code": code},
            client_ip=client_ip,
        )
        await db.commit()


def job_steps(kind: str) -> tuple[str, ...]:
    """OpenClaw 服务器各类任务的步骤（与任务记录里预先写好的一致）。"""
    return {"prepare": prov.PREPARE_STEPS, "install": prov.STEPS}.get(kind, prov.UNINSTALL_STEPS)


async def run_job(
    sessionmaker: async_sessionmaker[AsyncSession],
    settings: Settings,
    job_id: uuid.UUID,
    *,
    client_ip: str | None = None,
    remove_files: bool = False,
    expected_revision: int | None = None,
) -> None:
    """后台协程：初始化服务器、部署或卸载实例（按任务记录的 kind），全程用保存的登录密码。"""
    try:
        target = await _load(sessionmaker, job_id, expected_revision)
    except JobGone:
        return
    recorder = JobRecorder(sessionmaker, job_id, steps=job_steps(target.kind))
    label = {"prepare": "初始化", "install": "部署"}.get(target.kind, "卸载")
    # 初始化服务器的审计记在服务器上，部署 / 卸载记在实例上
    audit = ("agents.ssh", "agent_server" if target.kind == "prepare" else "agent_instance")
    try:
        if target.kind == "prepare":
            await AgentServerInstaller(
                sessionmaker, settings, recorder, target, client_ip=client_ip
            ).prepare()
        elif target.kind == "install":
            await AgentServerInstaller(
                sessionmaker, settings, recorder, target, client_ip=client_ip
            ).deploy()
        else:
            await AgentServerUninstaller(
                sessionmaker, settings, recorder, target, client_ip=client_ip
            ).uninstall(remove_files=remove_files)
    except JobGone:
        return
    except asyncio.CancelledError:
        await asyncio.shield(
            _finish(
                sessionmaker,
                settings,
                recorder,
                target,
                status="interrupted",
                code="interrupted",
                message=f"工作台停止，{label}中断；可以重新开始",
                client_ip=client_ip,
                audit=audit,
            )
        )
        raise
    except InstallFailed as error:
        await _finish(
            sessionmaker,
            settings,
            recorder,
            target,
            status="failed",
            code=error.code,
            message=error.message,
            client_ip=client_ip,
            audit=audit,
        )
    except ssh.SshError as error:
        await _finish(
            sessionmaker,
            settings,
            recorder,
            target,
            status="failed",
            code=error.reason,
            message=error.message,
            client_ip=client_ip,
            audit=audit,
        )
    except Exception:
        # 不带异常文本写进任务（可能含远端输出）；完整堆栈只在服务日志里，堆栈不含局部变量
        logger.exception("agent ssh job failed", extra={"job_id": str(job_id)})
        await _finish(
            sessionmaker,
            settings,
            recorder,
            target,
            status="failed",
            code="internal_error",
            message="工作台内部错误，详见服务日志",
            client_ip=client_ip,
            audit=audit,
        )
    else:
        await _finish(
            sessionmaker,
            settings,
            recorder,
            target,
            status="succeeded",
            client_ip=client_ip,
            audit=audit,
        )


class SshJobRunner:
    """进程内的安装 / 卸载任务。启动时把上次没跑完的任务标为中断，停止时取消还在跑的任务。"""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession], settings: Settings) -> None:
        self._sessionmaker = sessionmaker
        self.settings = settings
        self._tasks: dict[uuid.UUID, asyncio.Task[None]] = {}
        # 已经提交、还没启动协程的任务（提交与启动之间别的请求不能把它当成失效的任务）
        self._reserved: set[uuid.UUID] = set()

    @property
    def sessionmaker(self) -> async_sessionmaker[AsyncSession]:
        return self._sessionmaker

    def reserve(self, job_id: uuid.UUID) -> None:
        self._reserved.add(job_id)

    def release(self, job_id: uuid.UUID) -> None:
        self._reserved.discard(job_id)

    def start(
        self,
        job_id: uuid.UUID,
        *,
        client_ip: str | None,
        remove_files: bool = False,
        expected_revision: int | None = None,
    ) -> None:
        """启动 OpenClaw 服务器的任务。

        不接收密码：任务登录时才按 expected_revision 解密保存的密码。
        """
        task = asyncio.get_running_loop().create_task(
            run_job(
                self._sessionmaker,
                self.settings,
                job_id,
                client_ip=client_ip,
                remove_files=remove_files,
                expected_revision=expected_revision,
            ),
            name=f"agent-ssh-job:{job_id}",
        )
        self._tasks[job_id] = task
        self._reserved.discard(job_id)
        task.add_done_callback(lambda _: self._tasks.pop(job_id, None))

    def launch(self, key: uuid.UUID, coro: Coroutine[Any, Any, None]) -> None:
        """运行其它借用 SSH 通道的后台协程（T-0114 的运维动作）；key 是那条记录的 ID。"""
        task = asyncio.get_running_loop().create_task(coro, name=f"agent-ssh-task:{key}")
        self._tasks[key] = task
        self._reserved.discard(key)
        task.add_done_callback(lambda _: self._tasks.pop(key, None))

    def running(self, job_id: uuid.UUID) -> bool:
        """本进程里有这个任务的协程（或刚提交、马上启动）。"""
        return job_id in self._tasks or job_id in self._reserved

    async def wait(self, job_id: uuid.UUID) -> None:
        task = self._tasks.get(job_id)
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)

    async def recover(self, seconds: float = RECOVER_TIMEOUT) -> int | None:
        """API 只有一个进程：启动时还是 running 的任务，协程已经不在了，标为中断。

        数据库暂时连不上（或还没迁移）时不阻止 API 启动（/readyz 会报告），只记警告：
        漏掉的任务在下一次操作时由 AgentSshService 标为中断（本进程里没有它的协程）。
        """
        try:
            async with asyncio.timeout(seconds), self._sessionmaker() as db:
                result = await db.execute(
                    update(AgentSshJob)
                    .where(AgentSshJob.status == "running")
                    .values(
                        status="interrupted",
                        error_code="interrupted",
                        error_message="工作台重启，任务中断；可以重新开始",
                        finished_at=func.now(),
                        updated_at=func.now(),
                    )
                )
                # 运维动作（T-0114）同样在本进程里运行
                await db.execute(
                    update(AgentOpsAction)
                    .where(AgentOpsAction.status == "running")
                    .values(
                        status="interrupted",
                        error_code="interrupted",
                        error_message="工作台重启，动作中断；可以重新执行",
                        finished_at=func.now(),
                    )
                )
                # 运行服务器的部署（T-0136）也在本进程里运行
                await db.execute(
                    update(RunnerSshJob)
                    .where(RunnerSshJob.status == "running")
                    .values(
                        status="interrupted",
                        error_code="interrupted",
                        error_message="工作台重启，任务中断；可以重新开始",
                        finished_at=func.now(),
                        updated_at=func.now(),
                    )
                )
                await db.commit()
        except (TimeoutError, OSError, SQLAlchemyError) as error:
            logger.warning("agent ssh job recovery skipped", extra={"error": type(error).__name__})
            return None
        return int(getattr(result, "rowcount", 0) or 0)

    async def stop(self) -> None:
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
