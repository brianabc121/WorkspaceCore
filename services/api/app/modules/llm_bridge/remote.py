"""远程开发容器（docs/v2/LLM_BRIDGE_REMOTE_ENV_PLAN.md 第 5、8、9、21 节）。

- 建容器：先在选定的容器服务器上构建内置“大模型开发环境”模板（自定义镜像再加一层命令行），
  模板好了才建容器服务器环境（owner_kind=llm_bridge，常驻、不自动归档）。构建要几分钟，
  所以建容器是异步的：登记一行，定时任务 remote_tick 看模板好了就接着建。
- 中转：管理器不连工作台。remote_tick 对每个有远程容器的管理器长轮询 /admin/remote/ops，
  按 21.2 校验后转成容器服务器的操作，把结果回报给管理器。
- 输出：runner-agent 交上来的托管进程输出经 RemoteProcSink 直接转给管理器（runner-api 的请求里）；
  结束同理。转不出去的结束由 remote_tick 补发。
"""

from __future__ import annotations

import asyncio
import base64
import logging
import re
import uuid
from typing import Any
from urllib.parse import urlparse

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.domain import runner_specs as specs
from app.domain.connectors import ConnectorRuntime
from app.domain.runner import Runner, RunnerError
from app.domain.runner_managed import ManagedRunner
from app.domain.sync import SyncContext
from app.integrations.base import ConnectorError
from app.integrations.http import CircuitBreaker
from app.models import RunnerEnvironment, RunnerHost, RunnerRun, RunnerTemplate
from app.modules import service as modules
from app.modules.llm_bridge import remote_config as cfg
from app.modules.llm_bridge.client import BridgeCallError
from app.modules.llm_bridge.models import (
    LlmBridge,
    LlmBridgeRemoteContainer,
    LlmBridgeRemoteProc,
)
from app.modules.llm_bridge.service import LlmBridgeService

logger = logging.getLogger(__name__)

_SESSION_ID = re.compile(r"^bs_[A-Za-z0-9_-]{4,64}$")
_PROC_ID = re.compile(r"^rp_[A-Za-z0-9_-]{4,64}$")
_NATIVE_ID = re.compile(r"^[\w.\-:]{1,100}$")
PURPOSES = ("turn", "login", "job", "prepare")
# 管理器的状态 → 页面上的容器状态
OPS_WAIT = 20
TICK_SECONDS = 50
HELPER_WAIT = 40
FILES_WAIT = 30
MAX_TIMEOUT = 6 * 3600 + 600

PREPARE_SCRIPT = (
    'set -e; d="$1"; url="$2"; br="$3"; mkdir -p "$(dirname "$d")"; '
    'if [ -d "$d" ] && [ -n "$(ls -A "$d" 2>/dev/null)" ]; then echo "已存在"; exit 0; fi; '
    'mkdir -p "$d"; '
    'if [ -n "$url" ]; then '
    'if [ -n "$br" ]; then git clone --depth 1 --branch "$br" -- "$url" "$d"; '
    'else git clone --depth 1 -- "$url" "$d"; fi; fi; echo "准备好了"'
)
VERSIONS_SCRIPT = (
    f'for p in codex claude codebuddy; do printf "%s=" "$p"; '
    f'"{cfg.CLI_BIN}/$p" --version 2>/dev/null | tail -n 1; done'
)


class RemoteError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def session_dir(session_id: str) -> str:
    if not _SESSION_ID.match(session_id):
        raise RemoteError("VALIDATION_FAILED", "会话号不合法")
    return f"{cfg.SESSIONS_ROOT}/{session_id}"


def repo_host(url: str | None) -> str | None:
    if not url:
        return None
    host = urlparse(url).hostname
    return host.lower() if host else None


def parse_versions(output: str) -> dict[str, str]:
    """`codex=codex-cli 0.169.0` / `claude=2.1.323 (Claude Code)` / `codebuddy=2.184.0`。"""
    found: dict[str, str] = {}
    for line in output.splitlines():
        program, _, text = line.partition("=")
        program = program.strip()
        match = re.search(r"\d+\.\d+\.\d+", text)
        if program in ("codex", "claude", "codebuddy") and match:
            found[program] = match.group(0)  # 管理器按程序名比对（codebuddy 是 WorkBuddy 平台）
    return found


# ---------------------------------------------------------------- 容器的状态


def container_state(
    row: LlmBridgeRemoteContainer,
    env: RunnerEnvironment | None,
    template: RunnerTemplate | None,
) -> tuple[str, str | None]:
    """页面上的状态。

    building / creating / running / stopped / archived / deleting / failed / gone。"""
    error = (row.settings or {}).get("error")
    if row.runner_environment_id is None:
        if error:
            return "failed", str(error)
        if (row.settings or {}).get("provisioned"):
            return "gone", "容器已经不在了（在容器服务器上被删除）"
        if template is not None and template.build_status == "failed":
            return "failed", f"镜像构建失败：{(template.build_error or '')[:300]}"
        return "building", "正在构建镜像（第一次要几分钟）"
    if env is None or env.state == "deleted":
        return "gone", "容器已经不在了"
    if env.state == "pending_delete":
        return "deleting", env.state_reason
    if env.state == "failed":
        return "failed", env.state_reason
    return env.state, env.state_reason


class RemoteContainers:
    """远程开发容器的建、改、删和中转。调用方负责模块启用与角色检查。"""

    def __init__(
        self,
        db: AsyncSession,
        settings: Settings,
        runtime: ConnectorRuntime,
        redis: Redis | None,
    ) -> None:
        self.db = db
        self.settings = settings
        self.runtime = runtime
        self.redis = redis
        self.managed = ManagedRunner(db, settings, redis)
        self.service = LlmBridgeService(db, settings, runtime)

    # ------------------------------------------------------------ 查询

    async def get(
        self, workspace_id: uuid.UUID, bridge_id: uuid.UUID, container_id: uuid.UUID
    ) -> LlmBridgeRemoteContainer:
        row = await self.db.scalar(
            select(LlmBridgeRemoteContainer).where(
                LlmBridgeRemoteContainer.id == container_id,
                LlmBridgeRemoteContainer.workspace_id == workspace_id,
                LlmBridgeRemoteContainer.bridge_id == bridge_id,
            )
        )
        if row is None:
            raise RemoteError("NOT_FOUND", "没有这个远程容器")
        return row

    async def env_of(self, row: LlmBridgeRemoteContainer) -> RunnerEnvironment | None:
        if row.runner_environment_id is None:
            return None
        return await self.db.get(RunnerEnvironment, row.runner_environment_id)

    async def template_of(self, row: LlmBridgeRemoteContainer) -> RunnerTemplate | None:
        if row.template_id is None:
            return None
        return await self.db.get(RunnerTemplate, row.template_id)

    async def usage(self, workspace_id: uuid.UUID) -> dict[str, float]:
        """这个工作空间的远程开发容器一共占了多少（D8 的上限）。"""
        rows = list(
            await self.db.scalars(
                select(LlmBridgeRemoteContainer).where(
                    LlmBridgeRemoteContainer.workspace_id == workspace_id
                )
            )
        )
        used = {"count": 0.0, "cpus": 0.0, "memory_mb": 0.0, "disk_gb": 0.0}
        for row in rows:
            env = await self.env_of(row)
            if row.runner_environment_id is not None and (env is None or env.state == "deleted"):
                continue
            settings = row.settings or {}
            used["count"] += 1
            for key in ("cpus", "memory_mb", "disk_gb"):
                used[key] += float(settings.get(key) or 0)
        return used

    # ------------------------------------------------------------ 建、改、删

    async def create(
        self,
        workspace_id: uuid.UUID,
        bridge: LlmBridge,
        *,
        name: str,
        host_id: uuid.UUID,
        image_kind: str,
        image: str | None,
        template_name: str | None,
        resources: dict[str, Any],
        runtime: str,
        network: str,
        domains: list[str],
        secrets: list[str],
        idle_stop_minutes: int,
        repo_hosts: list[str],
        actor_id: uuid.UUID | None,
    ) -> LlmBridgeRemoteContainer:
        """登记一个远程容器并开始构建镜像。调用方提交。"""
        host = await self.db.scalar(
            select(RunnerHost).where(
                RunnerHost.id == host_id, RunnerHost.workspace_id == workspace_id
            )
        )
        if host is None:
            raise RemoteError("NOT_FOUND", "没有这台容器服务器")
        if host.backend != "docker":
            raise RemoteError("NOT_SUPPORTED", "Kubernetes 类的容器服务器暂时不能放远程开发容器")
        if host.status == "disabled":
            raise RemoteError("RUNNER_UNAVAILABLE", "这台容器服务器已停用")
        exists = await self.db.scalar(
            select(LlmBridgeRemoteContainer.id).where(
                LlmBridgeRemoteContainer.bridge_id == bridge.id,
                LlmBridgeRemoteContainer.name == name,
            )
        )
        if exists is not None:
            raise RemoteError("CONFLICT", f"已经有一个叫「{name}」的远程容器")
        used = await self.usage(workspace_id)
        for key, limit in cfg.LIMITS.items():
            add = 1 if key == "count" else float(resources.get(key) or 0)
            if used[key] + add > limit:
                names = {"count": "个数", "cpus": "CPU", "memory_mb": "内存", "disk_gb": "磁盘"}
                raise RemoteError(
                    "QUOTA_EXCEEDED",
                    f"远程开发容器的{names[key]}超过工作空间的上限（{limit:g}）",
                )
        settings = {
            **{k: resources[k] for k in ("cpus", "memory_mb", "disk_gb")},
            "runtime": runtime,
            "network": network,
            "domains": domains,
            "secrets": secrets,
            "idle_stop_minutes": idle_stop_minutes,
            "repo_hosts": repo_hosts,
        }
        row = LlmBridgeRemoteContainer(
            id=uuid.uuid4(),
            workspace_id=workspace_id,
            bridge_id=bridge.id,
            host_id=host.id,
            name=name,
            image_kind=image_kind,
            image=image if image_kind == "custom" else template_name,
            cli_versions=dict(cfg.CLI_VERSIONS),
            settings=settings,
            created_by=actor_id,
        )
        self.db.add(row)
        await self.db.flush()
        await self._start_build(row, host, actor_id)
        return row

    async def _start_build(
        self, row: LlmBridgeRemoteContainer, host: RunnerHost, actor_id: uuid.UUID | None
    ) -> None:
        """确保内置模板在这台服务器上构建；自定义镜像 / 模板等内置模板好了再加一层命令行。"""
        builtin = await self.managed.ensure_template(
            row.workspace_id,
            host,
            name=f"mw-llm-dev-{str(host.id)[:8]}",
            description=cfg.BUILTIN_DESCRIPTION,
            spec={"image": cfg.BASE_IMAGE, "steps": cfg.BUILTIN_STEPS, "lifetime": "persistent"},
            actor_id=actor_id,
        )
        if row.image_kind == "builtin":
            row.template_id = builtin.id
            return
        if builtin.build_status != "ready" or not builtin.image_ref:
            row.template_id = None  # 等内置模板好了，remote_tick 再建派生模板
            row.settings = {**(row.settings or {}), "builtin_template_id": str(builtin.id)}
            return
        if row.image_kind == "custom":
            base = row.image or ""
        else:
            source = await self.runner().template_by_name(row.workspace_id, row.image or "")
            if source is None or source.build_status != "ready" or not source.image_ref:
                raise RemoteError("TEMPLATE_NOT_READY", f"模板 {row.image} 不存在或还没构建好")
            if source.build_host_id != host.id:
                raise RemoteError(
                    "PLACEMENT_CONFLICT", f"模板 {row.image} 不是在这台容器服务器上构建的"
                )
            base = source.image_ref
        derived = await self.managed.ensure_template(
            row.workspace_id,
            host,
            name=f"mw-llm-dev-{str(row.id)[:8]}",
            description=f"远程开发容器「{row.name}」的镜像：{base} + 大模型命令行",
            spec={
                "image": base,
                "steps": cfg.derived_steps(builtin.image_ref),
                "lifetime": "persistent",
            },
            actor_id=actor_id,
        )
        row.template_id = derived.id

    def runner(self) -> Runner:
        return self.managed.runner

    async def provision(self, row: LlmBridgeRemoteContainer) -> bool:
        """模板好了就建容器服务器环境。返回这次有没有推进。调用方提交。"""
        if row.runner_environment_id is not None or (row.settings or {}).get("error"):
            return False
        host = await self.db.get(RunnerHost, row.host_id) if row.host_id else None
        if host is None:
            row.settings = {**(row.settings or {}), "error": "容器服务器已移除"}
            return True
        if row.template_id is None:
            builtin_id = (row.settings or {}).get("builtin_template_id")
            builtin = (
                await self.db.get(RunnerTemplate, uuid.UUID(builtin_id)) if builtin_id else None
            )
            if builtin is not None and builtin.build_status == "failed":
                row.settings = {
                    **(row.settings or {}),
                    "error": f"镜像构建失败：{(builtin.build_error or '')[:300]}",
                }
                return True
            if builtin is None or builtin.build_status != "ready":
                return False
            try:
                await self._start_build(row, host, row.created_by)
            except RemoteError as error:
                row.settings = {**(row.settings or {}), "error": error.message}
            return True
        template = await self.db.get(RunnerTemplate, row.template_id)
        if template is None:
            row.settings = {**(row.settings or {}), "error": "镜像模板不在了"}
            return True
        if template.build_status == "failed":
            row.settings = {
                **(row.settings or {}),
                "error": f"镜像构建失败：{(template.build_error or '')[:300]}",
            }
            return True
        if template.build_status != "ready":
            return False
        settings = row.settings or {}
        bridge = await self.db.get(LlmBridge, row.bridge_id)
        label = f"MCP 管理器「{bridge.name if bridge else '?'}」· 远程容器「{row.name}」"
        try:
            env = await self.managed.create(
                row.workspace_id,
                owner_kind=cfg.OWNER,
                owner_ref=str(row.id),
                owner_label=label,
                host=host,
                template=template,
                overrides={
                    "cpus": settings.get("cpus"),
                    "memory_mb": settings.get("memory_mb"),
                    "disk_gb": settings.get("disk_gb"),
                    "runtime": settings.get("runtime"),
                    **cfg.runner_network(
                        str(settings.get("network") or "all"),
                        list(settings.get("domains") or []),
                        list(settings.get("repo_hosts") or []),
                    ),
                    "secrets": list(settings.get("secrets") or []),
                },
                purpose=f"大模型开发环境：{row.name}",
                idle_stop_seconds=int(settings.get("idle_stop_minutes") or 0) * 60,
                actor_id=row.created_by,
            )
        except RunnerError as error:
            if error.code in ("RESOURCE_BUSY",):
                return False  # 稍后再试
            row.settings = {**settings, "error": error.message}
            return True
        row.runner_environment_id = env.id
        row.settings = {**settings, "provisioned": True}
        return True

    async def set_policy(
        self,
        row: LlmBridgeRemoteContainer,
        *,
        idle_stop_minutes: int | None,
        network: str | None,
        domains: list[str] | None,
        repo_hosts: list[str] | None = None,
    ) -> None:
        settings = dict(row.settings or {})
        if idle_stop_minutes is not None:
            settings["idle_stop_minutes"] = idle_stop_minutes
        if network is not None:
            settings["network"] = network
        if domains is not None:
            settings["domains"] = domains
        if repo_hosts is not None:
            settings["repo_hosts"] = sorted(set(settings.get("repo_hosts") or []) | set(repo_hosts))
        row.settings = settings
        row.version += 1
        env = await self.env_of(row)
        if env is not None and env.state != "deleted":
            self.managed.set_policy(
                env,
                idle_stop_seconds=int(settings.get("idle_stop_minutes") or 0) * 60,
                **cfg.runner_network(
                    str(settings.get("network") or "all"),
                    list(settings.get("domains") or []),
                    list(settings.get("repo_hosts") or []),
                ),
            )

    async def action(self, row: LlmBridgeRemoteContainer, action: str) -> None:
        """start / stop / retry / update_image。调用方提交。"""
        env = await self.env_of(row)
        try:
            if action == "start":
                if env is None:
                    raise RemoteError("ENV_UNAVAILABLE", "容器还没建好")
                await self.managed.start(env)
            elif action == "stop":
                if env is None:
                    raise RemoteError("ENV_UNAVAILABLE", "容器还没建好")
                await self.managed.stop(env, "管理员在大模型客户端停止")
            elif action == "retry":
                if (
                    row.runner_environment_id is not None
                    and env is not None
                    and env.state != "failed"
                ):
                    raise RemoteError("CONFLICT", "容器没有失败，不用重试")
                if env is not None and env.state == "failed":
                    await self.managed.delete(env, "重试：删掉失败的容器重建")
                    row.runner_environment_id = None
                settings = dict(row.settings or {})
                settings.pop("error", None)
                settings.pop("provisioned", None)
                row.settings = settings
                host = await self.db.get(RunnerHost, row.host_id) if row.host_id else None
                if host is None:
                    raise RemoteError("RUNNER_UNAVAILABLE", "容器服务器已移除")
                await self._start_build(row, host, row.created_by)
            elif action == "update_image":
                # 管理器升级后命令行版本变了：按新版本重建模板，好了以后容器按新镜像重建
                # （数据卷保留：会话目录和容器里的登录都还在），remote_tick 接着做
                host = await self.db.get(RunnerHost, row.host_id) if row.host_id else None
                if host is None or env is None or env.state in ("deleted", "pending_delete"):
                    raise RemoteError("ENV_UNAVAILABLE", "容器还没建好")
                await self._start_build(row, host, row.created_by)
                row.cli_versions = dict(cfg.CLI_VERSIONS)
                settings = dict(row.settings or {})
                settings.pop("image_error", None)
                settings["pending_image"] = True
                row.settings = settings
            else:
                raise RemoteError("VALIDATION_FAILED", "不认识的操作")
        except RunnerError as error:
            raise RemoteError(error.code, error.message) from None
        row.version += 1

    async def delete(self, row: LlmBridgeRemoteContainer) -> None:
        """删掉容器（连数据卷，不做快照）和登记。调用方提交。"""
        env = await self.env_of(row)
        if env is not None and env.state not in ("deleted",):
            try:
                await self.managed.delete(env, "在大模型客户端删除远程容器")
            except RunnerError as error:
                raise RemoteError(error.code, error.message) from None
        await self.db.delete(row)

    # ------------------------------------------------------------ 中转：一条操作

    async def handle_op(self, bridge: LlmBridge, op: dict[str, Any]) -> dict[str, Any]:
        """执行管理器的一条远程操作（21.2），返回 data；不合法或失败抛 RemoteError。"""
        kind = str(op.get("kind") or "")
        raw_args = op.get("args")
        args: dict[str, Any] = raw_args if isinstance(raw_args, dict) else {}
        try:
            container_id = uuid.UUID(str(op.get("container")))
        except ValueError:
            raise RemoteError("VALIDATION_FAILED", "容器号不合法") from None
        row = await self.get(bridge.workspace_id, bridge.id, container_id)
        env = await self.env_of(row)
        if kind == "status":
            return await self._status(row, env)
        if env is None or env.state in ("deleted", "pending_delete", "failed"):
            state, reason = container_state(row, env, await self.template_of(row))
            raise RemoteError("CONTAINER_NOT_READY", reason or f"远程容器现在是 {state}")
        try:
            if kind == "start_proc":
                return await self._start_proc(bridge, row, env, args)
            if kind in ("signal_proc", "input_proc"):
                return await self._proc_control(bridge, kind, args)
            if kind == "put_files":
                return await self._put_files(env, args)
            if kind == "helper":
                return await self._helper(env, args)
        except RunnerError as error:
            raise RemoteError(error.code, error.message) from None
        raise RemoteError("VALIDATION_FAILED", f"不认识的操作：{kind[:40]}")

    async def _status(
        self, row: LlmBridgeRemoteContainer, env: RunnerEnvironment | None
    ) -> dict[str, Any]:
        state, reason = container_state(row, env, await self.template_of(row))
        host = await self.db.get(RunnerHost, row.host_id) if row.host_id else None
        settings = row.settings or {}
        return {
            "state": state,
            "reason": reason,
            "host": host.name if host else None,
            "cpus": settings.get("cpus"),
            "memory_mb": settings.get("memory_mb"),
            "versions_ok": dict(row.cli_versions or {}) == cfg.CLI_VERSIONS,
        }

    def _check_argv(self, purpose: str, platform: str | None, argv: list[str]) -> list[str]:
        if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
            raise RemoteError("VALIDATION_FAILED", "命令不合法")
        if purpose == "job":
            if len(argv) != 3 or argv[:2] != ["bash", "-c"]:
                raise RemoteError("VALIDATION_FAILED", "后台作业只能是 bash -c <命令>")
            return argv
        if platform not in cfg.PROGRAMS:
            raise RemoteError("VALIDATION_FAILED", "不认识的平台")
        program = cfg.PROGRAMS[platform]
        if argv[0] not in (program, f"{cfg.CLI_BIN}/{program}"):
            raise RemoteError("VALIDATION_FAILED", f"只能运行 {program}")
        if purpose == "login" and (len(argv) < 2 or argv[1] not in ("login", "auth")):
            raise RemoteError("VALIDATION_FAILED", "登录只能运行登录子命令")
        return [f"{cfg.CLI_BIN}/{program}", *argv[1:]]

    async def _start_proc(
        self,
        bridge: LlmBridge,
        row: LlmBridgeRemoteContainer,
        env: RunnerEnvironment,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        proc_id = str(args.get("proc_id") or "")
        purpose = str(args.get("purpose") or "")
        if not _PROC_ID.match(proc_id) or purpose not in PURPOSES:
            raise RemoteError("VALIDATION_FAILED", "进程号或用途不合法")
        found = await self.db.scalar(
            select(LlmBridgeRemoteProc).where(
                LlmBridgeRemoteProc.bridge_id == bridge.id, LlmBridgeRemoteProc.proc_id == proc_id
            )
        )
        if found is not None:  # 同一个进程号重发：返回原来的运行
            return {"run_id": str(found.run_id) if found.run_id else None}
        session_id = args.get("session_id")
        platform = args.get("platform")
        timeout = int(args.get("timeout_seconds") or 1800)
        timeout = max(30, min(timeout, MAX_TIMEOUT))
        raw_env, raw_secret = args.get("env"), args.get("secret_env")
        env_vars: dict[str, Any] = raw_env if isinstance(raw_env, dict) else {}
        secret_env: dict[str, Any] = raw_secret if isinstance(raw_secret, dict) else {}
        if purpose == "prepare":
            if not isinstance(session_id, str):
                raise RemoteError("VALIDATION_FAILED", "准备会话目录要给会话号")
            folder = session_dir(session_id)
            url = args.get("repo_url") or ""
            branch = args.get("branch") or ""
            if url and (not isinstance(url, str) or not url.startswith("https://")):
                raise RemoteError("VALIDATION_FAILED", "代码仓库只支持 https:// 地址")
            if not isinstance(branch, str) or len(branch) > 200 or branch.startswith("-"):
                raise RemoteError("VALIDATION_FAILED", "分支名不合法")
            argv = ["sh", "-c", PREPARE_SCRIPT, "mw-prepare", folder, str(url), branch]
            cwd = "/workspace"
            env_vars = {"GIT_TERMINAL_PROMPT": "0"}
            secret_env = {}
            stdin = b""
            label = f"准备会话目录 {session_id}"
            host = repo_host(str(url))
            if host:
                await self.set_policy(
                    row, idle_stop_minutes=None, network=None, domains=None, repo_hosts=[host]
                )
        else:
            argv = self._check_argv(
                purpose, platform if isinstance(platform, str) else None, args.get("argv") or []
            )
            cwd = str(args.get("cwd") or "")
            if purpose == "login":
                cwd = cwd or cfg.HOME
                if cwd != cfg.HOME:
                    raise RemoteError("VALIDATION_FAILED", "登录在 HOME 里跑")
            else:
                if not isinstance(session_id, str):
                    raise RemoteError("VALIDATION_FAILED", "要给会话号")
                folder = session_dir(session_id)
                if cwd != folder and not cwd.startswith(folder + "/"):
                    raise RemoteError("VALIDATION_FAILED", "工作目录必须在这个会话的目录里")
            try:
                stdin = base64.b64decode(str(args.get("stdin_b64") or ""), validate=True)
            except ValueError:
                raise RemoteError("VALIDATION_FAILED", "stdin_b64 不对") from None
            label = f"{purpose} · {platform or 'job'} · {session_id or '登录'}"
        run = await self.managed.start_proc(
            env,
            owner_kind=cfg.OWNER,
            owner_ref=str(row.id),
            label=label,
            argv=argv,
            cwd=cwd,
            env_vars={str(k): str(v) for k, v in env_vars.items()},
            secret_env={str(k): str(v) for k, v in secret_env.items()},
            stdin=stdin,
            timeout_seconds=timeout,
            interactive=bool(args.get("interactive")) and purpose == "login",
        )
        self.db.add(
            LlmBridgeRemoteProc(
                workspace_id=bridge.workspace_id,
                bridge_id=bridge.id,
                container_id=row.id,
                proc_id=proc_id,
                run_id=run.id,
                purpose=purpose,
                session_id=session_id if isinstance(session_id, str) else None,
            )
        )
        await self.db.commit()
        return {"run_id": str(run.id)}

    async def _proc_control(
        self, bridge: LlmBridge, kind: str, args: dict[str, Any]
    ) -> dict[str, Any]:
        proc = await self.db.scalar(
            select(LlmBridgeRemoteProc).where(
                LlmBridgeRemoteProc.bridge_id == bridge.id,
                LlmBridgeRemoteProc.proc_id == str(args.get("proc_id") or ""),
            )
        )
        run = await self.db.get(RunnerRun, proc.run_id) if proc and proc.run_id else None
        if run is None:
            raise RemoteError("NOT_FOUND", "没有这个进程")
        if kind == "signal_proc":
            await self.managed.signal(run, str(args.get("signal") or "TERM"))
        else:
            await self.managed.send_input(run, str(args.get("data") or ""))
        await self.db.commit()
        return {}

    async def _put_files(self, env: RunnerEnvironment, args: dict[str, Any]) -> dict[str, Any]:
        session_id = str(args.get("session_id") or "")
        session_dir(session_id)
        files: list[tuple[str, bytes]] = []
        for item in args.get("files") or []:
            if not isinstance(item, dict):
                raise RemoteError("VALIDATION_FAILED", "文件不合法")
            try:
                files.append(
                    (str(item.get("path")), base64.b64decode(str(item.get("b64")), validate=True))
                )
            except ValueError:
                raise RemoteError("VALIDATION_FAILED", "文件内容不对") from None
        command = await self.managed.files(env, "write", f"sessions/{session_id}", files=files)
        await self.db.commit()
        done = await self.runner().wait_command(command.id, FILES_WAIT)
        if done is None:
            raise RemoteError("TIMEOUT", "容器服务器没有及时写好文件")
        if done.status != "done":
            raise RemoteError("RUNNER_FAILED", done.error or "写文件失败")
        return {"written": len(files)}

    async def _helper(self, env: RunnerEnvironment, args: dict[str, Any]) -> dict[str, Any]:
        name = str(args.get("name") or "")
        extra: dict[str, Any] = {}
        if name == "remove_session":
            argv = ["rm", "-rf", "--", session_dir(str(args.get("session_id") or ""))]
        elif name == "transcript_exists":
            native = str(args.get("native_id") or "")
            platform = str(args.get("platform") or "")
            if not _NATIVE_ID.match(native) or platform not in cfg.CONFIG_DIRS:
                raise RemoteError("VALIDATION_FAILED", "会话号或平台不合法")
            root = cfg.CONFIG_DIRS[platform]
            if platform == "codex":
                script = 'find "$1/sessions" -name "*$2.jsonl" -print -quit 2>/dev/null'
            else:
                script = 'ls "$1"/projects/*/"$2".jsonl 2>/dev/null | head -n 1'
            argv = ["sh", "-c", script, "mw-check", root, native]
        elif name == "versions":
            argv = ["sh", "-c", VERSIONS_SCRIPT]
        else:
            raise RemoteError("VALIDATION_FAILED", "不认识的辅助命令")
        run = await self.managed.helper(env, argv, timeout_seconds=HELPER_WAIT + 20)
        await self.db.commit()
        done = await self.runner().wait_run(run.id, HELPER_WAIT)
        if done is None or done.finished_at is None:
            raise RemoteError("TIMEOUT", "容器服务器没有及时执行完（容器可能正在启动），稍后再试")
        output = done.output or ""
        if name == "transcript_exists":
            extra["exists"] = bool(output.strip())
        if name == "versions":
            extra["versions"] = parse_versions(output)
        return {"exit_code": done.exit_code, "output": output[-4000:], **extra}


# ---------------------------------------------------------------- 输出转发（runner_proc_sink）


class RemoteProcSink:
    """容器服务器托管进程的输出、结束 → 管理器（21.2 的 output / exit）。"""

    @staticmethod
    async def _target(
        db: AsyncSession, run: RunnerRun
    ) -> tuple[LlmBridgeRemoteProc, LlmBridge] | None:
        proc = await db.scalar(
            select(LlmBridgeRemoteProc).where(LlmBridgeRemoteProc.run_id == run.id)
        )
        if proc is None:
            return None
        bridge = await db.get(LlmBridge, proc.bridge_id)
        return (proc, bridge) if bridge is not None else None

    async def output(
        self,
        db: AsyncSession,
        settings: Settings,
        run: RunnerRun,
        offset: int,
        data: bytes,
        runtime: ConnectorRuntime | None = None,
    ) -> int:
        target = await self._target(db, run)
        if target is None:
            return offset + len(data)  # 管理器那头已经没有这个进程：丢掉
        proc, bridge = target
        service = LlmBridgeService(
            db, settings, runtime or ConnectorRuntime(settings, CircuitBreaker())
        )
        try:
            client = await service.client(bridge)
            result = await client.admin(
                "POST",
                f"/remote/procs/{proc.proc_id}/output",
                {"offset": offset, "data_b64": base64.b64encode(data).decode()},
            )
        except BridgeCallError as error:
            if error.code == "NOT_FOUND":
                # 管理器重启后不认识这个进程了：停掉它，输出丢掉
                await ManagedRunner(db, settings).signal(run, "KILL")
                return offset + len(data)
            return offset
        except ConnectorError:
            return offset  # 管理器暂时连不上：runner-agent 稍后从这里重发
        acked = result.get("acked") if isinstance(result, dict) else None
        return int(acked) if isinstance(acked, int) else offset

    async def exit(
        self,
        db: AsyncSession,
        settings: Settings,
        run: RunnerRun,
        info: dict[str, Any],
        runtime: ConnectorRuntime | None = None,
    ) -> None:
        target = await self._target(db, run)
        if target is None:
            return
        proc, bridge = target
        await send_exit(db, settings, runtime, proc, bridge, info)


async def send_exit(
    db: AsyncSession,
    settings: Settings,
    runtime: ConnectorRuntime | None,
    proc: LlmBridgeRemoteProc,
    bridge: LlmBridge,
    info: dict[str, Any],
) -> bool:
    if proc.exit_sent:
        return True
    proc.finished_at = proc.finished_at or specs.now()
    service = LlmBridgeService(
        db, settings, runtime or ConnectorRuntime(settings, CircuitBreaker())
    )
    body = {
        "exit_code": info.get("exit_code"),
        "state": info.get("state")
        if info.get("state") in ("exited", "killed", "timeout", "lost", "failed")
        else "lost",
        "error": info.get("error"),
        "stderr_tail": str(info.get("stderr_tail") or "")[-16384:],
    }
    try:
        client = await service.client(bridge)
        await client.admin("POST", f"/remote/procs/{proc.proc_id}/exit", body)
    except BridgeCallError as error:
        if error.code != "NOT_FOUND":
            return False
    except ConnectorError:
        return False
    proc.exit_sent = True
    return True


SINK = RemoteProcSink()


def _exit_info(run: RunnerRun | None) -> dict[str, Any]:
    if run is None:
        return {"exit_code": None, "state": "lost", "error": "容器服务器上的运行记录已不在"}
    state = {
        "succeeded": "exited",
        "failed": "exited" if run.exit_code is not None else "failed",
        "timeout": "timeout",
        "cancelled": "killed",
    }.get(run.status, "lost")
    error = None if state == "exited" else (run.output or "")[-500:] or None
    return {"exit_code": run.exit_code, "state": state, "error": error, "stderr_tail": ""}


# ---------------------------------------------------------------- 定时任务


async def recreate(runner: Runner, env: RunnerEnvironment, template: RunnerTemplate) -> None:
    """按新镜像重建容器，数据卷保留（create_env 带 recreate，runner-agent 先删旧容器）。"""
    host = await runner.admit_restart(env)
    env.template_id = template.id
    env.template_revision = template.revision
    env.spec = {
        **env.spec,
        "template_image": template.image_ref,
        "image_digest": template.image_digest,
    }
    env.runtime_metadata = {
        **(env.runtime_metadata or {}),
        "compute_stopped": False,
        "compute_reserved_at": specs.now().isoformat(),
        "generation": int((env.runtime_metadata or {}).get("generation", 1)) + 1,
    }
    env.state = "creating"
    env.state_reason = "更新镜像"
    payload = runner.create_payload(env, template=template)
    payload["recreate"] = True
    runner.enqueue(host, "create_env", payload, env=env)


async def _relay_bridge(context: SyncContext, bridge_id: uuid.UUID, until: float) -> int:
    """长轮询这个管理器的远程操作，一条一个任务地执行，直到 until。"""
    loop = asyncio.get_running_loop()
    handled = 0
    tasks: set[asyncio.Task[None]] = set()

    async def run_one(op: dict[str, Any]) -> None:
        async with context.sessions() as db:
            bridge = await db.get(LlmBridge, bridge_id)
            if bridge is None:
                return
            remote = RemoteContainers(db, context.settings, context.runtime, context.redis)
            try:
                data = await remote.handle_op(bridge, op)
                result: dict[str, Any] = {"ok": True, "data": data}
            except RemoteError as error:
                await db.rollback()
                result = {"ok": False, "error": {"code": error.code, "message": error.message}}
            except Exception:
                await db.rollback()
                logger.exception("远程操作 %s 出错", op.get("id"))
                result = {"ok": False, "error": {"code": "INTERNAL", "message": "工作台处理出错"}}
            bridge = await db.get(LlmBridge, bridge_id)
            if bridge is None:
                return
            try:
                client = await LlmBridgeService(db, context.settings, context.runtime).client(
                    bridge
                )
                await client.admin("POST", f"/remote/ops/{op.get('id')}/result", result)
            except (BridgeCallError, ConnectorError):
                logger.info("远程操作 %s 的结果没交给管理器（管理器会重发）", op.get("id"))

    while loop.time() < until:
        wait = int(min(OPS_WAIT, max(until - loop.time() - 2, 0)))
        async with context.sessions() as db:
            bridge = await db.get(LlmBridge, bridge_id)
            if bridge is None or not bridge.enabled:
                break
            client = await LlmBridgeService(db, context.settings, context.runtime).client(bridge)
        # 长轮询时不占着数据库连接
        try:
            data = await client.admin(
                "GET", f"/remote/ops?wait={wait}&limit=20", read_timeout=wait + 10
            )
        except (BridgeCallError, ConnectorError) as error:
            logger.info("管理器 %s 的远程操作取不到：%s", bridge_id, type(error).__name__)
            await asyncio.sleep(min(5, max(until - loop.time(), 0)))
            continue
        ops = data.get("ops") if isinstance(data, dict) else None
        for op in ops or []:
            if isinstance(op, dict) and op.get("id"):
                task = asyncio.create_task(run_one(op))
                tasks.add(task)
                task.add_done_callback(tasks.discard)
                handled += 1
        if not ops and wait == 0:
            break
    if tasks:
        await asyncio.wait(tasks, timeout=max(HELPER_WAIT + 5, until - loop.time()))
    return handled


async def _housekeeping(context: SyncContext) -> None:
    """建等着的容器、补发没送到的结束、停用的管理器停掉它的容器。"""
    async with context.sessions() as db:
        rows = list(await db.scalars(select(LlmBridgeRemoteContainer)))
        for row in rows:
            if not await modules.is_enabled(db, row.workspace_id, "llm_bridge"):
                continue
            remote = RemoteContainers(db, context.settings, context.runtime, context.redis)
            try:
                if await remote.provision(row):
                    await db.commit()
            except Exception:
                await db.rollback()
                logger.exception("远程容器 %s 没建成", row.id)
            # 管理器升级后：新镜像好了就按新镜像重建容器（数据卷保留）
            settings = row.settings or {}
            if settings.get("pending_image"):
                template = await remote.template_of(row)
                env = await remote.env_of(row)
                if template is not None and template.build_status == "ready" and env is not None:
                    try:
                        await recreate(remote.runner(), env, template)
                        row.settings = {k: v for k, v in settings.items() if k != "pending_image"}
                        await db.commit()
                    except RunnerError as error:
                        await db.rollback()
                        logger.info("远程容器 %s 更新镜像没成：%s", row.id, error.message)
                elif template is not None and template.build_status == "failed":
                    row.settings = {
                        **{k: v for k, v in settings.items() if k != "pending_image"},
                        "image_error": (template.build_error or "镜像构建失败")[:300],
                    }
                    await db.commit()
            bridge = await db.get(LlmBridge, row.bridge_id)
            env = await remote.env_of(row)
            if (
                bridge is not None
                and not bridge.enabled
                and env is not None
                and env.state == "running"
            ):
                try:
                    await remote.managed.stop(env, "MCP 管理器已停用")
                    await db.commit()
                except RunnerError:
                    await db.rollback()
        unsent = list(
            await db.execute(
                select(LlmBridgeRemoteProc, RunnerRun)
                .outerjoin(RunnerRun, RunnerRun.id == LlmBridgeRemoteProc.run_id)
                .where(LlmBridgeRemoteProc.exit_sent.is_(False))
                .limit(200)
            )
        )
        for proc, run in unsent:
            if run is not None and run.finished_at is None:
                continue
            bridge = await db.get(LlmBridge, proc.bridge_id)
            if bridge is None:
                continue
            await send_exit(db, context.settings, context.runtime, proc, bridge, _exit_info(run))
        await db.commit()


async def remote_tick(ctx: dict[str, Any]) -> int:
    """每分钟一次，跑大约 50 秒：中转远程操作、建等着的容器、补发结束。"""
    context: SyncContext = ctx["sync"]
    loop = asyncio.get_running_loop()
    until = loop.time() + TICK_SECONDS
    async with context.sessions() as db:
        bridges = list(
            await db.execute(
                select(LlmBridge.id, LlmBridge.workspace_id)
                .where(
                    LlmBridge.enabled.is_(True),
                    LlmBridge.id.in_(select(LlmBridgeRemoteContainer.bridge_id)),
                )
                .distinct()
            )
        )
        active = [b for b, w in bridges if await modules.is_enabled(db, w, "llm_bridge")]
    try:
        await _housekeeping(context)
    except Exception:
        logger.exception("远程容器的例行检查出错")
    if not active:
        return 0
    results = await asyncio.gather(
        *(_relay_bridge(context, bridge_id, until) for bridge_id in active),
        return_exceptions=True,
    )
    return sum(r for r in results if isinstance(r, int))
