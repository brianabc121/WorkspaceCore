"""通过 SSH 自动部署 OpenClaw 的安装任务（T-0089 B2）。

设计见 docs/decisions/2026-11-03-openclaw-ssh-provisioning.md 第 5 节：

- 任务在 API 进程内的后台协程里运行（API 只有一个进程）。登录密码只作为这个协程的参数存在
  （`SecretStr`），不写数据库、不进 Redis、不写日志；
  密码阶段（登录、装公钥、装 Docker）一结束就丢掉并断开；
- 之后都用专用密钥登录；专用私钥加密保存（用途 `agent_ssh:<目标 id>`）；
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
from app.domain.agent_ssh import agent_ssh_context
from app.domain.agents import AgentService, hash_token
from app.domain.audit import AuditTrail
from app.domain.credentials import CredentialBox, CredentialError
from app.integrations import ssh
from app.models import AgentCredential, AgentInstance, AgentSshJob, AgentSshTarget

logger = logging.getLogger("app.agent_ssh")

LOG_LIMIT = 64 * 1024
DOCKER_TIMEOUT = 900.0
PULL_TIMEOUT = 1200.0
PLUGIN_TIMEOUT = 900.0
START_TIMEOUT = 300.0
# 冷启动 2 ~ 4 分钟（doctor 联网检查插件更新，OC-0 ADR F5）；最多等 15 分钟
ONLINE_TIMEOUT = 15 * 60.0
ONLINE_POLL = 5.0
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
    """登录密码只在密码阶段使用：用完 clear()，之后任务里不再有对它的引用。"""

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
    ) -> None:
        self.sessionmaker = sessionmaker
        self.job_id = job_id
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
                update(AgentSshJob)
                .where(AgentSshJob.id == self.job_id, AgentSshJob.status == "running")
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


# ---------------------------------------------------------------- 安装


class Installer:
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
        ref = self.target.client_key_ref
        if ref is None:
            return None
        try:
            secret = self._box().resolve(ref, context=agent_ssh_context(self.target.target_id))
            return asyncssh.import_private_key(secret.get_secret_value())
        except (CredentialError, asyncssh.KeyImportError) as error:
            raise InstallFailed(
                "client_key_invalid", "专用私钥无法解密，请吊销后重新部署"
            ) from error

    async def _save_key(self, key: asyncssh.SSHKey, public: str) -> None:
        box = self._box()
        sealed = box.seal(
            key.export_private_key().decode(), context=agent_ssh_context(self.target.target_id)
        )
        async with self.sessionmaker() as db:
            target = await db.get(AgentSshTarget, self.target.target_id, with_for_update=True)
            if target is None:
                raise JobGone
            if not self._unchanged(target):
                raise InstallFailed("target_changed", "目标服务器在安装期间被更换，已停止")
            target.client_key_ref = sealed
            target.client_public_key = public
            target.version += 1
            await AuditTrail(db, self.settings).record(
                "agents.ssh.key_authorized",
                workspace_ids=[target.workspace_id],
                actor_id=self.target.started_by,
                resource_type="agent_instance",
                resource_id=target.instance_id,
                metadata={"client_key_fingerprint": key.get_fingerprint("sha256")},
                client_ip=self.client_ip,
            )
            await db.commit()

    async def _authorize(
        self, conn: asyncssh.SSHClientConnection, key: asyncssh.SSHKey | None
    ) -> asyncssh.SSHKey:
        await self.rec.step("authorize_key", "running")
        fresh = key is None
        if key is None:
            key = asyncssh.generate_private_key(
                "ssh-ed25519", comment=f"myworkspace-agent-{str(self.target.instance_id)[:8]}"
            )
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

    async def _docker(
        self, conn: asyncssh.SSHClientConnection, info: HostInfo, password: SecretStr | None
    ) -> None:
        if info.docker_ready:
            await self.rec.step("docker", "skipped", "已经可以使用 Docker 与 Compose")
            return
        await self.rec.step("docker", "running")
        if password is None:
            raise InstallFailed(
                "docker_unavailable", "这个账号现在不能使用 Docker：请填写登录密码重新部署"
            )
        if (not info.docker or not info.compose) and not info.supported:
            raise InstallFailed(
                "unsupported_os",
                "自动安装 Docker 只支持 Ubuntu 22.04 / 24.04、Debian 12（amd64、arm64）；"
                "请先手动安装 Docker 与 Compose 插件，再重新部署",
            )
        secret = password.get_secret_value() + "\n"
        if not info.root:
            if not info.sudo:
                raise InstallFailed(
                    "sudo_unavailable", "服务器上没有 sudo，无法以 root 安装 Docker"
                )
            check = await self._run(
                conn, "检查 sudo", "sudo -S -p '' -k true", stdin=secret, seconds=30
            )
            if not check.ok:
                raise InstallFailed("sudo_denied", "这个账号不能使用 sudo（或密码不对）")
        staging = f"{info.home}/{prov.STAGING_DIR_NAME}"
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
                stdin=None if info.root else secret,
                seconds=DOCKER_TIMEOUT,
            )
        finally:
            await self._run(conn, "删除安装脚本", f"rm -rf {prov.q(staging)}", log=False)
        if not result.ok:
            raise _script_failure(result, "docker_install_failed", "安装 Docker 失败，详见日志")
        version = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
        await self.rec.step("docker", "done", f"Docker {version}".strip())

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
                directory = f"{self.home}/{prov.INSTALL_DIR_NAME}"
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

    async def run(self, secret: OneTimePassword) -> None:
        if not self.target.host_key_confirmed:
            # 接口里已经检查过；任务开始时再确认一次，未确认的主机公钥绝不用来登录
            raise InstallFailed("host_key_unconfirmed", "服务器的主机公钥还没有确认")
        key = self._existing_key()
        password = secret.get()
        if password is None and key is None:
            raise InstallFailed("password_required", "第一次部署需要登录密码")
        conn: asyncssh.SSHClientConnection | None = None
        try:
            await self.rec.step("connect", "running")
            if password is not None:
                conn = await self._connect(password=password)
            else:
                conn = await self._connect(key=key)
            await self.rec.step(
                "connect", "done", "密码登录（只用这一次）" if password else "专用密钥登录"
            )
            info = await self._inspect(conn)
            self.home = info.home
            if password is not None:
                key = await self._authorize(conn, key)
            else:
                await self.rec.step("authorize_key", "skipped", "专用公钥已经装好")
            await self._docker(conn, info, password)
            if password is not None:
                # 密码阶段结束：丢掉密码、断开密码连接，改用专用密钥重新登录
                # （新会话里 docker 组才生效）
                self.rec.forget(password.get_secret_value())
                secret.clear()
                password = None
                conn.close()
                await conn.wait_closed()
                conn = None
                await self.rec.step("reconnect", "running")
                conn = await self._connect(key=key)
            else:
                await self.rec.step("reconnect", "running")
            check = await self._run(
                conn,
                "检查 Docker",
                "docker info --format '{{.ServerVersion}}' && docker compose version --short",
                seconds=60,
            )
            if not check.ok:
                raise InstallFailed("docker_unavailable", "专用密钥登录后仍然不能使用 Docker")
            await self.rec.step("reconnect", "done", "专用密钥登录，Docker 可用")
            await self._deploy(conn, info)
        finally:
            if conn is not None:
                conn.close()

    async def _deploy(self, conn: asyncssh.SSHClientConnection, info: HostInfo) -> None:
        directory = f"{info.home}/{prov.INSTALL_DIR_NAME}"
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
        await self.rec.step("plugin", "done", prov.PLUGIN_PACKAGE)

        await self.rec.step("start", "running")
        result = await self._run(
            conn, "启动", prov.compose_command(directory, "up", "-d"), seconds=START_TIMEOUT
        )
        if not result.ok:
            raise InstallFailed("start_failed", "启动失败，详见日志")
        await self.rec.step("start", "done")

        if info.crontab:
            await self.rec.step("schedule", "running")
            result = await self._run(conn, "定时任务", prov.schedule_command(directory), seconds=30)
            if not result.ok:
                raise InstallFailed("schedule_failed", "写入 crontab 失败")
            await self.rec.step("schedule", "done", "每 10 分钟取密钥、每周一重启")
        else:
            await self.rec.step(
                "schedule",
                "skipped",
                "服务器上没有 crontab：按接入包 CHECKLIST.md 手动配置定时任务",
            )

        await self._wait_online(token, conn)
        async with self.sessionmaker() as db:
            target = await db.get(AgentSshTarget, self.target.target_id, with_for_update=True)
            if target is None:
                raise JobGone
            if not self._unchanged(target):
                raise InstallFailed("target_changed", "目标服务器在安装期间被更换，已停止")
            target.install_dir = directory
            target.installed_at = _now()
            target.version += 1
            await db.commit()


class Uninstaller(Installer):
    """卸载（B3）：只用专用密钥。停止容器、删掉 crontab 里带标记的行，需要时删除接入包目录。

    不碰 Docker 本身、专用公钥（另有吊销）和实例凭证（实例页可以吊销）。
    """

    async def uninstall(self, *, remove_files: bool) -> None:
        if not self.target.host_key_confirmed:
            raise InstallFailed("host_key_unconfirmed", "服务器的主机公钥还没有确认")
        key = self._existing_key()
        if key is None:
            raise InstallFailed("client_key_missing", "这台服务器上没有工作台的专用公钥")
        conn: asyncssh.SSHClientConnection | None = None
        removed = False
        try:
            await self.rec.step("connect", "running")
            conn = await self._connect(key=key)
            await self.rec.step("connect", "done", "专用密钥登录")
            info = await self._inspect(conn)
            directory = f"{info.home}/{prov.INSTALL_DIR_NAME}"

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
                    conn, "删除定时任务", prov.unschedule_command(), seconds=30
                )
                if not result.ok:
                    raise InstallFailed("schedule_failed", "修改 crontab 失败")
                await self.rec.step("unschedule", "done", "已删除带标记的定时任务")
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
                raise InstallFailed("target_changed", "目标服务器在卸载期间被更换，已停止")
            target.installed_at = None
            if removed:
                target.install_dir = None
            target.version += 1
            await db.commit()


# ---------------------------------------------------------------- 任务入口


async def _load(sessionmaker: async_sessionmaker[AsyncSession], job_id: uuid.UUID) -> Target:
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
        instance_id=target.instance_id,
        workspace_id=target.workspace_id,
        started_by=job.started_by,
        address=target.address,
        port=target.port,
        username=target.username,
        host_key=target.host_key,
        client_key_ref=target.client_key_ref,
        host_key_confirmed=target.host_key_confirmed_at is not None,
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
) -> None:
    """写回结果；数据库出错时只记日志（任务记录保持 running，由下一次操作或重启标为中断）。"""
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
            f"agents.ssh.{target.kind}_{status}",
            workspace_ids=[target.workspace_id],
            actor_id=target.started_by,
            resource_type="agent_instance",
            resource_id=target.instance_id,
            metadata={"job_id": str(target.job_id), "error_code": code},
            client_ip=client_ip,
        )
        await db.commit()


async def run_job(
    sessionmaker: async_sessionmaker[AsyncSession],
    settings: Settings,
    job_id: uuid.UUID,
    secret: OneTimePassword,
    *,
    client_ip: str | None = None,
    remove_files: bool = False,
) -> None:
    """后台协程：安装或卸载（按任务记录的 kind）。"""
    try:
        target = await _load(sessionmaker, job_id)
    except JobGone:
        return
    password = secret.get()
    recorder = JobRecorder(
        sessionmaker,
        job_id,
        [password.get_secret_value()] if password is not None else [],
        steps=prov.STEPS if target.kind == "install" else prov.UNINSTALL_STEPS,
    )
    del password
    label = "安装" if target.kind == "install" else "卸载"
    try:
        if target.kind == "install":
            await Installer(sessionmaker, settings, recorder, target, client_ip=client_ip).run(
                secret
            )
        else:
            await Uninstaller(
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
        )
    else:
        await _finish(
            sessionmaker, settings, recorder, target, status="succeeded", client_ip=client_ip
        )


class SshJobRunner:
    """进程内的安装 / 卸载任务。启动时把上次没跑完的任务标为中断，停止时取消还在跑的任务。"""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession], settings: Settings) -> None:
        self._sessionmaker = sessionmaker
        self.settings = settings
        self._tasks: dict[uuid.UUID, asyncio.Task[None]] = {}
        # 已经提交、还没启动协程的任务（提交与启动之间别的请求不能把它当成失效的任务）
        self._reserved: set[uuid.UUID] = set()

    def reserve(self, job_id: uuid.UUID) -> None:
        self._reserved.add(job_id)

    def release(self, job_id: uuid.UUID) -> None:
        self._reserved.discard(job_id)

    def start(
        self,
        job_id: uuid.UUID,
        password: SecretStr | None,
        *,
        client_ip: str | None,
        remove_files: bool = False,
    ) -> None:
        task = asyncio.get_running_loop().create_task(
            run_job(
                self._sessionmaker,
                self.settings,
                job_id,
                OneTimePassword(password),
                client_ip=client_ip,
                remove_files=remove_files,
            ),
            name=f"agent-ssh-job:{job_id}",
        )
        self._tasks[job_id] = task
        self._reserved.discard(job_id)
        task.add_done_callback(lambda _: self._tasks.pop(job_id, None))

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
