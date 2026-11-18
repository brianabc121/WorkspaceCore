"""模块建的容器服务器环境与托管进程（docs/v2/LLM_BRIDGE_REMOTE_ENV_PLAN.md 21.3、21.4）。

和智能体经 env_* 工具建的环境不同：

- 归属是模块（owner_kind = 模块 key，owner_ref = 模块里的编号），不算孤儿、不占任何实例的配额，
  容器服务器页面上不能归档 / 删除（由模块管理）；
- 常驻，默认不自动归档（no_auto_archive），
  空闲停止看 runtime_metadata.idle_stop_seconds（0 = 不停）；
- 托管进程（runner_runs.kind = proc）：runner-agent 起好就回报，输出边跑边经
  /runner-api/v1/procs/{id}/output 交给模块（runner_proc_sink），结束经 /exit。

托管进程的标准输入和凭据不落库：加密后放进 Redis（10 分钟过期），
runner-agent 领指令时才解开放进载荷。
宿主保护照旧由 runner-agent 在本机生成容器参数时强制（RUNNER_PLAN 8.1），这里不能放宽。
"""

from __future__ import annotations

import base64
import json
import re
import uuid
from dataclasses import dataclass
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.domain import runner_specs as specs
from app.domain.credentials import CredentialBox, CredentialError
from app.domain.runner import Runner, RunnerError, Viewer, _redact
from app.models import RunnerCommand, RunnerEnvironment, RunnerHost, RunnerRun, RunnerTemplate

OWNER_INSTANCE = "instance"
PROC_ROOT = "/workspace/.mw/procs"
PROC_INPUT_TTL = 600
# 标准输入（提问、图片）上限；凭据的条数与长度上限
STDIN_LIMIT = 2 * 1024 * 1024
INPUT_LIMIT = 4096
SECRET_ENV_MAX = 8
PROC_START_DEADLINE = 1800  # 容器停着要先启动、归档的要先重建
SIGNALS = ("TERM", "KILL")
_ENV_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_PROXY_ENV = specs.RESERVED_ENV - {"HOME"}


def proc_input_key(command_id: uuid.UUID) -> str:
    return f"runner:proc-input:{command_id}"


def _context(command_id: uuid.UUID) -> str:
    return f"runner-proc-input:{command_id}"


def proc_secrets_key(run_id: uuid.UUID) -> str:
    return f"runner:proc-secrets:{run_id}"


async def remember_proc_secrets(
    redis: Redis | None, settings: Settings, run_id: uuid.UUID, values: list[str], ttl: int
) -> None:
    """托管进程注入的凭据值：进程结束时用来给错误输出脱敏（加密放 Redis，不落库，结束后删除）。"""
    if redis is None or not values:
        return
    box = CredentialBox.from_settings(settings)
    sealed = box.seal(json.dumps(values), context=f"runner-proc-secrets:{run_id}")
    await redis.set(proc_secrets_key(run_id), sealed, ex=ttl)


async def take_proc_secrets(
    redis: Redis | None, settings: Settings, run_id: uuid.UUID
) -> list[str]:
    if redis is None:
        return []
    raw = await redis.getdel(proc_secrets_key(run_id))
    if not raw:
        return []
    try:
        text = raw.decode() if isinstance(raw, bytes) else str(raw)
        opened = CredentialBox.from_settings(settings).resolve(
            text, context=f"runner-proc-secrets:{run_id}"
        )
        values = json.loads(opened.get_secret_value())
    except (CredentialError, ValueError, UnicodeDecodeError):
        return []
    return [str(v) for v in values] if isinstance(values, list) else []


def proc_dir(run_id: uuid.UUID) -> str:
    return f"{PROC_ROOT}/{run_id}"


@dataclass(frozen=True)
class _Placement:
    """指定服务器的放置（和 app.schemas.placement.Placement 的字段一致）。"""

    server_id: uuid.UUID
    mode: str = "specified"


async def seal_proc_input(
    redis: Redis | None, settings: Settings, command_id: uuid.UUID, data: dict[str, Any]
) -> None:
    if redis is None:
        raise RunnerError("UNAVAILABLE", "工作台的 Redis 不可用，不能把输入交给容器服务器")
    box = CredentialBox.from_settings(settings)
    sealed = box.seal(json.dumps(data, ensure_ascii=False), context=_context(command_id))
    await redis.set(proc_input_key(command_id), sealed, ex=PROC_INPUT_TTL)


async def take_proc_input(
    redis: Redis | None, settings: Settings, command_id: uuid.UUID
) -> dict[str, Any]:
    """领指令时取出（并删掉）托管进程的输入；取不到返回 {"missing": True}。"""
    if redis is None:
        return {"missing": True}
    key = proc_input_key(command_id)
    raw = await redis.getdel(key)
    if not raw:
        return {"missing": True}
    try:
        text = raw.decode() if isinstance(raw, bytes) else str(raw)
        box = CredentialBox.from_settings(settings)
        opened = box.resolve(text, context=_context(command_id))
        data = json.loads(opened.get_secret_value())
    except (CredentialError, ValueError, UnicodeDecodeError):
        return {"missing": True}
    return data if isinstance(data, dict) else {"missing": True}


def _clean_env(values: dict[str, str] | None, *, secret: bool) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in (values or {}).items():
        if key == "HOME":
            continue  # HOME 由容器设成 /workspace/.home，不让调用方改
        if not _ENV_KEY.match(key) or key.startswith("MYWS_") or key in _PROXY_ENV:
            raise RunnerError("VALIDATION_FAILED", f"环境变量名不合法：{key[:64]}")
        if not isinstance(value, str) or len(value) > 16384 or "\x00" in value:
            raise RunnerError("VALIDATION_FAILED", f"环境变量 {key} 的值不合法")
        out[key] = value
    if secret and len(out) > SECRET_ENV_MAX:
        raise RunnerError("VALIDATION_FAILED", "凭据最多 8 个")
    if not secret and len(out) > 64:
        raise RunnerError("VALIDATION_FAILED", "环境变量最多 64 个")
    return out


def _clean_path(path: str) -> str:
    """容器里的绝对路径：/workspace 或它下面的路径，不能有 .. 和空段。"""
    if path == "/workspace":
        return path
    if not path.startswith("/workspace/") or "\x00" in path or len(path) > 1000:
        raise RunnerError("VALIDATION_FAILED", "路径必须在 /workspace 下")
    parts = path.split("/")[2:]
    if not parts or any(p in ("", ".", "..") for p in parts):
        raise RunnerError("VALIDATION_FAILED", "路径不合法")
    return path


class ManagedRunner:
    def __init__(self, db: AsyncSession, settings: Settings, redis: Redis | None = None) -> None:
        self.db = db
        self.settings = settings
        self.redis = redis
        self.runner = Runner(db, settings, redis)

    # ------------------------------------------------------------------ 模板

    async def ensure_template(
        self,
        workspace_id: uuid.UUID,
        host: RunnerHost,
        *,
        name: str,
        description: str,
        spec: dict[str, Any],
        actor_id: uuid.UUID | None,
    ) -> RunnerTemplate:
        """找到（或建）在这台服务器上构建的模板；规格变了就按新规格重建。调用方提交。"""
        try:
            normalized = specs.normalize(spec)
        except specs.SpecError as error:
            raise RunnerError("VALIDATION_FAILED", str(error)) from None
        found = await self.runner.template_by_name(workspace_id, name)
        if (
            found is not None
            and found.status == "active"
            and found.spec == normalized
            and found.build_host_id == host.id
            and found.build_status in ("building", "ready")
        ):
            return found
        template, _ = await self.runner.save_template(
            Viewer(workspace_id, user_id=actor_id),
            name=name,
            description=description,
            spec=spec,
            host=host,
        )
        return template

    # ------------------------------------------------------------------ 环境

    async def create(
        self,
        workspace_id: uuid.UUID,
        *,
        owner_kind: str,
        owner_ref: str,
        owner_label: str,
        host: RunnerHost,
        template: RunnerTemplate,
        overrides: dict[str, Any],
        purpose: str,
        idle_stop_seconds: int,
        actor_id: uuid.UUID | None,
    ) -> RunnerEnvironment:
        """从一个已经构建好的模板建常驻环境（不自动归档）。调用方提交。"""
        if owner_kind == OWNER_INSTANCE or not owner_ref:
            raise RunnerError("VALIDATION_FAILED", "模块建的环境要写明归属")
        if template.build_status != "ready" or not template.image_ref:
            raise RunnerError("TEMPLATE_NOT_READY", f"模板 {template.name} 还没有构建好")
        raw = dict(template.spec)
        for key in ("memory_mb", "cpus", "pids", "disk_gb", "env", "network", "domains", "secrets"):
            if key in overrides:
                raw[key] = overrides[key]
        raw["user"] = "user"  # 远程开发容器固定非 root（方案 5.3）
        if overrides.get("runtime") in ("gvisor", "runc"):
            raw["runtime"] = overrides["runtime"]
        try:
            spec = specs.normalize(raw)
        except specs.SpecError as error:
            raise RunnerError("VALIDATION_FAILED", str(error)) from None
        admission = {
            **spec,
            "files": [],
            "copy_from_volume": False,
            "command": None,
            "template_image": template.image_ref,
            "image_digest": template.image_digest,
        }
        chosen = await self.runner.select_host(
            workspace_id, admission, _Placement(server_id=host.id), affinity=host.id
        )
        env_id = uuid.uuid4()
        names = specs.names(env_id)
        record = {
            **spec,
            "template_image": template.image_ref,
            "image_digest": template.image_digest,
        }
        env = RunnerEnvironment(
            id=env_id,
            workspace_id=workspace_id,
            host_id=chosen.id,
            runtime_backend=chosen.backend,
            runtime_metadata={
                "generation": 1,
                "node_name": chosen.node_name,
                "compute_stopped": False,
                "compute_reserved_at": specs.now().isoformat(),
                "idle_stop_seconds": max(int(idle_stop_seconds), 0),
            },
            placement={"mode": "specified", "server_id": str(chosen.id), "reason": "specified"},
            template_id=template.id,
            template_revision=template.revision,
            owner_instance_id=None,
            owner_kind=owner_kind,
            owner_ref=owner_ref,
            owner_label=owner_label[:200],
            no_auto_archive=True,
            purpose=purpose[:200],
            lifetime="persistent",
            reuse_key=None,
            state="creating",
            spec=record,
            container_name=names["container"],
            volume_name=names["volume"],
            network_name=names["network"],
        )
        self.db.add(env)
        await self.db.flush()
        self.runner.enqueue(
            chosen,
            "create_env",
            self.runner.create_payload(env, template=template),
            env=env,
        )
        template.use_count += 1
        template.last_used_at = specs.now()
        await self.runner.audit.record(
            "runner.environment_created",
            workspace_ids=[workspace_id],
            actor_id=actor_id,
            resource_type="runner_environment",
            resource_id=env.id,
            metadata={
                "owner": owner_kind,
                "owner_label": owner_label[:200],
                "purpose": purpose[:200],
                "lifetime": "persistent",
                "template": template.name,
                "non_default": specs.non_default(spec),
            },
        )
        return env

    async def env(
        self,
        workspace_id: uuid.UUID,
        env_id: uuid.UUID,
        *,
        owner_kind: str,
        lock: bool = False,
    ) -> RunnerEnvironment:
        query = select(RunnerEnvironment).where(
            RunnerEnvironment.id == env_id,
            RunnerEnvironment.workspace_id == workspace_id,
            RunnerEnvironment.owner_kind == owner_kind,
        )
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        env = await self.db.scalar(query)
        if env is None:
            raise RunnerError("NOT_FOUND", "容器不在了")
        return env

    async def start(self, env: RunnerEnvironment) -> None:
        if env.state == "archived":
            await self.runner.rebuild(env, "管理员启动，按配置记录重建")
        else:
            await self.runner.start(env)
        await self.runner.touch(env)

    async def stop(self, env: RunnerEnvironment, reason: str) -> None:
        await self.runner.stop(Viewer(env.workspace_id), env, reason)

    async def delete(self, env: RunnerEnvironment, reason: str) -> None:
        """立即删除（不做快照：远程开发容器里有登录凭据）。正在跑的托管进程随容器一起停掉。"""
        if env.state in ("deleted",):
            return
        await self.runner.delete(env, reason, snapshot=False)

    def set_policy(
        self,
        env: RunnerEnvironment,
        *,
        idle_stop_seconds: int | None = None,
        network: str | None = None,
        domains: list[str] | None = None,
    ) -> None:
        """改空闲停止和网络。网络在下一次启动或执行时生效（出口代理每次都按载荷登记）。"""
        if idle_stop_seconds is not None:
            env.runtime_metadata = {
                **(env.runtime_metadata or {}),
                "idle_stop_seconds": max(int(idle_stop_seconds), 0),
            }
        if network is not None or domains is not None:
            raw = {
                **env.spec,
                **({"network": network} if network is not None else {}),
                **({"domains": domains} if domains is not None else {}),
            }
            try:
                normalized = specs.normalize(raw)
            except specs.SpecError as error:
                raise RunnerError("VALIDATION_FAILED", str(error)) from None
            env.spec = {
                **env.spec,
                "network": normalized["network"],
                "domains": normalized["domains"],
            }

    # ------------------------------------------------------------------ 托管进程

    async def start_proc(
        self,
        env: RunnerEnvironment,
        *,
        owner_kind: str,
        owner_ref: str,
        label: str,
        argv: list[str],
        cwd: str,
        env_vars: dict[str, str] | None = None,
        secret_env: dict[str, str] | None = None,
        stdin: bytes = b"",
        timeout_seconds: int = specs.DEFAULT_RUN_SECONDS,
        interactive: bool = False,
    ) -> RunnerRun:
        """起一个托管进程。调用方提交（提交后 runner-agent 才领得到）。"""
        if env.state in ("pending_delete", "deleted", "failed"):
            raise RunnerError("ENV_UNAVAILABLE", f"容器现在是 {env.state}，不能运行")
        host = await self.db.get(RunnerHost, env.host_id)
        if host is None:
            raise RunnerError("RUNNER_UNAVAILABLE", "容器服务器已移除")
        if not argv or len(argv) > 256 or any(not isinstance(a, str) or "\x00" in a for a in argv):
            raise RunnerError("VALIDATION_FAILED", "命令不合法")
        if sum(len(a) for a in argv) > 64 * 1024:
            raise RunnerError("VALIDATION_FAILED", "命令太长")
        if len(stdin) > STDIN_LIMIT:
            raise RunnerError("PAYLOAD_TOO_LARGE", "输入最多 2 MB")
        cwd = _clean_path(cwd)
        plain = _clean_env(env_vars, secret=False)
        secret = _clean_env(secret_env, secret=True)
        timeout = max(10, min(int(timeout_seconds), 6 * 3600 + 600))
        extra = 0
        if env.state == "stopped":
            await self.runner.start(env)
        elif env.state == "archived":
            await self.runner.rebuild(env, "有人使用，自动重建")
            extra = 3600
        secrets_known = [*secret.values()]
        run = RunnerRun(
            id=uuid.uuid4(),
            workspace_id=env.workspace_id,
            environment_id=env.id,
            instance_id=None,
            kind="proc",
            owner_kind=owner_kind,
            owner_ref=owner_ref,
            command=_redact(label[:500], secrets_known),
            timeout_seconds=timeout,
        )
        self.db.add(run)
        await self.db.flush()
        payload = self.runner.runtime_payload(env)
        payload.update(
            {
                "run_id": str(run.id),
                "proc_dir": proc_dir(run.id),
                "argv": argv,
                "cwd": cwd,
                "env": plain,
                "timeout": timeout,
                "interactive": bool(interactive),
            }
        )
        command = self.runner.enqueue(
            host, "proc_start", payload, env=env, run=run, seconds=PROC_START_DEADLINE + extra
        )
        await seal_proc_input(
            self.redis,
            self.settings,
            command.id,
            {"stdin_b64": base64.b64encode(stdin).decode(), "secret_env": secret},
        )
        await remember_proc_secrets(
            self.redis,
            self.settings,
            run.id,
            list(secret.values()),
            timeout + PROC_START_DEADLINE + extra + 3600,
        )
        await self.runner.touch(env)
        return run

    async def _proc_host(self, run: RunnerRun) -> tuple[RunnerEnvironment, RunnerHost]:
        env = await self.db.get(RunnerEnvironment, run.environment_id)
        if env is None:
            raise RunnerError("NOT_FOUND", "容器不在了")
        host = await self.db.get(RunnerHost, env.host_id)
        if host is None:
            raise RunnerError("RUNNER_UNAVAILABLE", "容器服务器已移除")
        return env, host

    async def signal(self, run: RunnerRun, signal: str) -> None:
        """给托管进程整组发信号（TERM 后 10 秒还在 runner-agent 自己升级成 KILL）。调用方提交。"""
        if run.kind != "proc" or signal not in SIGNALS:
            raise RunnerError("VALIDATION_FAILED", "只能给托管进程发 TERM / KILL")
        if run.finished_at is not None:
            return
        env, host = await self._proc_host(run)
        if run.status == "queued":
            # 还没起来：撤掉起进程的指令，直接记成取消
            pending = await self.db.scalar(
                select(RunnerCommand).where(
                    RunnerCommand.run_id == run.id,
                    RunnerCommand.kind == "proc_start",
                    RunnerCommand.status == "pending",
                )
            )
            if pending is not None:
                pending.status = "cancelled"
                run.status = "cancelled"
                run.finished_at = specs.now()
                return
        self.runner.enqueue(
            host,
            "proc_signal",
            {
                "env_id": str(env.id),
                "container": env.container_name,
                "run_id": str(run.id),
                "proc_dir": proc_dir(run.id),
                "signal": signal,
            },
            env=env,
            seconds=120,
        )

    async def send_input(self, run: RunnerRun, data: str) -> None:
        """往交互进程的输入管道写一小段（登录时粘贴的授权码）。调用方提交。"""
        if run.kind != "proc" or run.finished_at is not None:
            raise RunnerError("ENV_UNAVAILABLE", "这个进程已经结束")
        if not data or len(data.encode()) > INPUT_LIMIT:
            raise RunnerError("VALIDATION_FAILED", "输入最多 4 KB")
        env, host = await self._proc_host(run)
        command = self.runner.enqueue(
            host,
            "proc_input",
            {
                "env_id": str(env.id),
                "container": env.container_name,
                "run_id": str(run.id),
                "proc_dir": proc_dir(run.id),
            },
            env=env,
            seconds=120,
        )
        await seal_proc_input(self.redis, self.settings, command.id, {"data": data})

    # ------------------------------------------------------------------ 辅助命令与文件

    async def helper(
        self, env: RunnerEnvironment, argv: list[str], *, timeout_seconds: int = 60
    ) -> RunnerRun:
        """跑一条一次性的辅助命令（删除会话目录、查会话记录、查版本），返回运行。调用方提交后等它。"""
        run = await self.runner.start_run(None, env, argv, timeout_seconds)
        await self.runner.touch(env)
        return run

    async def files(
        self,
        env: RunnerEnvironment,
        action: str,
        path: str,
        *,
        files: list[tuple[str, bytes]] | None = None,
    ) -> RunnerCommand:
        """列目录（list）、读文件（read）、写文件（write，path 是目录，files 里是相对它的路径）。

        path 是相对 /workspace 的路径；调用方提交后用 Runner.wait_command 等结果。"""
        host = await self.db.get(RunnerHost, env.host_id)
        if host is None:
            raise RunnerError("RUNNER_UNAVAILABLE", "容器服务器已移除")
        if env.state == "archived":
            await self.runner.rebuild(env, "读写文件，自动重建")
        elif env.state not in ("running", "stopped", "creating"):
            raise RunnerError("ENV_UNAVAILABLE", f"容器现在是 {env.state}，不能读写文件")
        try:
            clean = specs.clean_relative(path, allow_root=action == "list")
        except specs.SpecError as error:
            raise RunnerError("VALIDATION_FAILED", str(error)) from None
        base = {"env_id": str(env.id), "volume": env.volume_name, "path": clean}
        if action == "write":
            items = []
            total = 0
            for rel, data in files or []:
                try:
                    target = specs.clean_relative(f"{clean}/{rel}")
                except specs.SpecError as error:
                    raise RunnerError("VALIDATION_FAILED", str(error)) from None
                total += len(data)
                items.append({"path": target, "b64": base64.b64encode(data).decode()})
            if total > specs.FILE_LIMIT:
                raise RunnerError("PAYLOAD_TOO_LARGE", "一次最多写 20 MB")
            if not items:
                raise RunnerError("VALIDATION_FAILED", "没有要写的文件")
            return self.runner.enqueue(host, "put_files", {**base, "files": items}, env=env)
        if action == "read":
            return self.runner.enqueue(
                host, "get_files", {**base, "limit": specs.FILE_LIMIT}, env=env
            )
        if action == "list":
            return self.runner.enqueue(host, "list_files", base, env=env)
        raise RunnerError("VALIDATION_FAILED", "不认识的文件操作")
