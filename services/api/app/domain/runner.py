"""运行服务器的核心（T-0136 ~ T-0138，docs/v2/RUNNER_PLAN.md）。

- 给 runner-agent 的指令：写进 runner_commands，runner-agent 长轮询领走、执行、回报结果；
  工具调用按需等结果（最多 55 秒），更久的运行结束后用唤醒叫醒智能体。
- 模板：智能体与管理员都能直接建、改（D1、D9），保存后在运行服务器上构建或拉取镜像。
- 环境：先复用（同模板、同复用键）；按类别建；配置记录写在 spec 里；限额在这里检查。
- 运行：命令原文脱敏后记录，输出最多 1 MB（脱敏），工具返回最后 16 KB。

宿主保护（不开特权、不挂 socket 与宿主目录、私有网段不可达）
由 runner-agent 在本机生成容器参数时强制。
注入的密钥不写进 runner_commands：下发指令时才解密（app/domain/runner_hosts.py）。
"""

import asyncio
import base64
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.domain import agent_ops, notifications
from app.domain import runner_specs as specs
from app.domain.audit import AuditTrail
from app.domain.ratelimit import WindowLimiter
from app.models import (
    AgentInstance,
    AgentSecretGrant,
    AgentWakeup,
    ProjectAgent,
    RunnerCommand,
    RunnerDeleteCondition,
    RunnerEnvironment,
    RunnerHost,
    RunnerRun,
    RunnerSnapshot,
    RunnerTemplate,
)
from app.schemas.runner import (
    EnvCreateInput,
    EnvExecInput,
    EnvFileEntry,
    EnvFilesInput,
    EnvFilesOutput,
    EnvOut,
    EnvText,
    EnvUpdateInput,
    RunnerSpecOut,
    RunnerTemplateOut,
    RunOut,
    RunText,
    TemplateText,
)

# 指令的最长时限（秒）：超过还没有结果的，exec 记为结果未知，可以重发的重发（最多 3 次）
DEADLINES: dict[str, int] = {
    "build_template": 3600,
    "create_env": 1800,
    "start_env": 600,
    "stop_env": 120,
    "delete_env": 300,
    "archive_env": 300,
    "list_files": 120,
    "get_files": 300,
    "put_files": 300,
    "snapshot": 3600,
    "restore": 3600,
    "prune_images": 600,
}
RETRYABLE = frozenset(
    {"stop_env", "delete_env", "archive_env", "list_files", "get_files", "prune_images"}
)
MAX_ATTEMPTS = 3
ONLINE_SECONDS = 90  # 这么久没有长轮询就算不在线
LIVE_STATES = ("creating", "running", "stopped")
ACTIVE_STATES = ("creating", "running")


class RunnerError(Exception):
    """领域错误：code 是工具错误码，message 写给智能体或管理员看。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Viewer:
    """谁在看：实例（工具）或管理员（页面）。"""

    workspace_id: uuid.UUID
    instance: AgentInstance | None = None
    user_id: uuid.UUID | None = None

    @property
    def admin(self) -> bool:
        return self.instance is None


def _redact(text: str, secrets: list[str]) -> str:
    for value in secrets:
        if len(value) >= 6:
            text = text.replace(value, "***")
    return agent_ops.redact(text)


def spec_out(spec: dict[str, Any]) -> RunnerSpecOut:
    return RunnerSpecOut(
        image=spec.get("image", ""),
        steps=list(spec.get("steps") or []),
        start_command=spec.get("start_command"),
        runtime=spec.get("runtime", "gvisor"),
        user=spec.get("user", "user"),
        network=spec.get("network", "none"),
        domains=list(spec.get("domains") or []),
        memory_mb=int(spec.get("memory_mb", specs.DEFAULT_RESOURCES["memory_mb"])),
        cpus=float(spec.get("cpus", specs.DEFAULT_RESOURCES["cpus"])),
        pids=int(spec.get("pids", specs.DEFAULT_RESOURCES["pids"])),
        disk_gb=int(spec.get("disk_gb", specs.DEFAULT_RESOURCES["disk_gb"])),
        env=dict(spec.get("env") or {}),
        secrets=list(spec.get("secrets") or []),
        persist=list(spec.get("persist") or []),
        lifetime=spec.get("lifetime", "session"),
    )


class Runner:
    def __init__(self, db: AsyncSession, settings: Settings, redis: Redis | None = None) -> None:
        self.db = db
        self.settings = settings
        self.redis = redis
        self.audit = AuditTrail(db, settings)

    # ------------------------------------------------------------------ 服务器与指令

    async def host(self, workspace_id: uuid.UUID) -> RunnerHost | None:
        result: RunnerHost | None = await self.db.scalar(
            select(RunnerHost).where(RunnerHost.workspace_id == workspace_id)
        )
        return result

    async def online_host(self, workspace_id: uuid.UUID) -> RunnerHost:
        host = await self.host(workspace_id)
        if host is None:
            raise RunnerError(
                "RUNNER_UNAVAILABLE",
                "工作空间还没有容器服务器（管理员在“容器服务器”页添加）",
            )
        if host.status == "disabled":
            raise RunnerError("RUNNER_UNAVAILABLE", "容器服务器已停用")
        fresh = host.last_seen_at is not None and host.last_seen_at > specs.now() - timedelta(
            seconds=ONLINE_SECONDS
        )
        if not fresh:
            raise RunnerError("RUNNER_UNAVAILABLE", "容器服务器不在线，稍后再试；管理员会收到告警")
        return host

    def enqueue(
        self,
        host: RunnerHost,
        kind: str,
        payload: dict[str, Any],
        *,
        env: RunnerEnvironment | None = None,
        run: RunnerRun | None = None,
        template: RunnerTemplate | None = None,
        seconds: int | None = None,
    ) -> RunnerCommand:
        deadline = seconds if seconds is not None else DEADLINES.get(kind, 600)
        command = RunnerCommand(
            id=uuid.uuid4(),
            workspace_id=host.workspace_id,
            host_id=host.id,
            kind=kind,
            payload=payload,
            environment_id=env.id if env is not None else None,
            run_id=run.id if run is not None else None,
            template_id=template.id if template is not None else None,
            deadline_at=specs.later(deadline),
        )
        self.db.add(command)
        return command

    async def wait_command(self, command_id: uuid.UUID, seconds: float) -> RunnerCommand | None:
        """等指令结束（runner-agent 在另一个请求里回报）；超时返回 None。调用前先提交。"""
        loop = asyncio.get_running_loop()
        until = loop.time() + seconds
        while True:
            command = await self.db.get(RunnerCommand, command_id, populate_existing=True)
            if command is None or command.status in ("done", "failed", "unknown", "cancelled"):
                return command
            if loop.time() >= until:
                return None
            await asyncio.sleep(0.5)

    async def wait_run(self, run_id: uuid.UUID, seconds: float) -> RunnerRun | None:
        loop = asyncio.get_running_loop()
        until = loop.time() + seconds
        while True:
            run = await self.db.get(RunnerRun, run_id, populate_existing=True)
            if run is None or run.finished_at is not None:
                return run
            if loop.time() >= until:
                return run
            await asyncio.sleep(0.5)

    async def wake_later(self, run_id: uuid.UUID) -> RunnerRun | None:
        """等不到结果：改成结束后唤醒。已经结束的直接返回（不唤醒）。"""
        run = await self.db.scalar(
            select(RunnerRun)
            .where(RunnerRun.id == run_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if run is not None and run.finished_at is None:
            run.wake = True
        await self.db.commit()
        return run

    # ------------------------------------------------------------------ 模板

    async def templates(self, workspace_id: uuid.UUID) -> list[RunnerTemplate]:
        rows = await self.db.scalars(
            select(RunnerTemplate)
            .where(RunnerTemplate.workspace_id == workspace_id)
            .order_by(RunnerTemplate.name)
        )
        return list(rows)

    async def template_by_name(self, workspace_id: uuid.UUID, name: str) -> RunnerTemplate | None:
        result: RunnerTemplate | None = await self.db.scalar(
            select(RunnerTemplate).where(
                RunnerTemplate.workspace_id == workspace_id, RunnerTemplate.name == name
            )
        )
        return result

    async def save_template(
        self,
        viewer: Viewer,
        *,
        name: str,
        description: str,
        spec: dict[str, Any],
        version: int | None = None,
    ) -> tuple[RunnerTemplate, bool]:
        try:
            normalized = specs.normalize(spec)
        except specs.SpecError as error:
            raise RunnerError("VALIDATION_FAILED", str(error)) from None
        if viewer.instance is not None:
            await self._check_secrets(viewer.instance, normalized["secrets"])
        host = await self.online_host(viewer.workspace_id)
        template = await self.template_by_name(viewer.workspace_id, name)
        created = template is None
        if template is None:
            template = RunnerTemplate(
                id=uuid.uuid4(),
                workspace_id=viewer.workspace_id,
                name=name,
                description=description,
                owner_instance_id=viewer.instance.id if viewer.instance else None,
                created_by=viewer.user_id,
                spec=normalized,
            )
            self.db.add(template)
        else:
            if viewer.instance is not None and template.owner_instance_id != viewer.instance.id:
                raise RunnerError("FORBIDDEN", "这个模板是别人建的，换一个名称建你自己的")
            if version is not None and version != template.version:
                raise RunnerError("VERSION_CONFLICT", "模板已被修改，请刷新后再保存")
            template.description = description
            if template.spec != normalized:
                template.spec = normalized
                template.revision += 1
            template.status = "active"
            template.version += 1
        template.build_status = "building"
        template.build_error = None
        await self.db.flush()
        self.enqueue(
            host,
            "build_template",
            {
                "template_id": str(template.id),
                "revision": template.revision,
                "image": normalized["image"],
                "steps": normalized["steps"],
                "tag": f"myworkspace/tpl-{specs.short(template.id)}:{template.revision}",
            },
            template=template,
        )
        await self.audit.record(
            "runner.template_saved",
            workspace_ids=[viewer.workspace_id],
            actor_id=viewer.user_id,
            resource_type="runner_template",
            resource_id=template.id,
            metadata={
                "name": name,
                "revision": template.revision,
                "by_instance": str(viewer.instance.id) if viewer.instance else None,
                "image": normalized["image"],
                "steps": len(normalized["steps"]),
                "non_default": specs.non_default(normalized),
            },
        )
        return template, created

    async def template_out(
        self, template: RunnerTemplate, viewer: Viewer, owners: dict[uuid.UUID, str] | None = None
    ) -> RunnerTemplateOut:
        owner = "管理员"
        if template.owner_instance_id is not None:
            if owners is not None and template.owner_instance_id in owners:
                owner = owners[template.owner_instance_id]
            else:
                instance = await self.db.get(AgentInstance, template.owner_instance_id)
                owner = instance.name if instance else "已删除的实例"
        mine = viewer.instance is not None and template.owner_instance_id == viewer.instance.id
        return RunnerTemplateOut(
            id=template.id,
            name=template.name,
            untrusted=TemplateText(description=template.description),
            owner=owner,
            mine=mine,
            status=template.status,
            revision=template.revision,
            spec=spec_out(template.spec),
            build_status=template.build_status,
            build_error=template.build_error,
            image_digest=template.image_digest,
            build_seconds=template.build_seconds,
            image_mb=(template.image_bytes // (1024 * 1024)) if template.image_bytes else None,
            use_count=template.use_count,
            last_used_at=template.last_used_at,
            updated_at=template.updated_at,
        )

    async def owner_names(self, ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
        if not ids:
            return {}
        rows = await self.db.execute(
            select(AgentInstance.id, AgentInstance.name).where(AgentInstance.id.in_(ids))
        )
        return {row[0]: row[1] for row in rows}

    # ------------------------------------------------------------------ 环境：查找与权限

    async def _project_ids(self, instance: AgentInstance) -> set[uuid.UUID]:
        rows = await self.db.scalars(
            select(ProjectAgent.project_id).where(ProjectAgent.instance_id == instance.id)
        )
        return set(rows)

    async def can_use(self, env: RunnerEnvironment, viewer: Viewer) -> bool:
        if env.workspace_id != viewer.workspace_id:
            return False
        if viewer.instance is None or env.owner_instance_id == viewer.instance.id:
            return True
        return (
            env.share == "project"
            and env.project_id is not None
            and env.project_id in await self._project_ids(viewer.instance)
        )

    async def env(self, viewer: Viewer, env_id: uuid.UUID) -> RunnerEnvironment:
        env = await self.db.get(RunnerEnvironment, env_id)
        if env is None or not await self.can_use(env, viewer):
            raise RunnerError("NOT_FOUND", "没有这个环境（或没有共享给你）")
        return env

    async def find(
        self,
        viewer: Viewer,
        *,
        template: str | None = None,
        reuse_key: str | None = None,
        query: str | None = None,
        include_deleted: bool = False,
        limit: int = 50,
    ) -> list[RunnerEnvironment]:
        stmt = select(RunnerEnvironment).where(
            RunnerEnvironment.workspace_id == viewer.workspace_id
        )
        if viewer.instance is not None:
            projects = await self._project_ids(viewer.instance)
            mine = RunnerEnvironment.owner_instance_id == viewer.instance.id
            shared = (RunnerEnvironment.share == "project") & RunnerEnvironment.project_id.in_(
                projects or {uuid.UUID(int=0)}
            )
            stmt = stmt.where(or_(mine, shared))
        if not include_deleted:
            stmt = stmt.where(RunnerEnvironment.state != "deleted")
        if template:
            found = await self.template_by_name(viewer.workspace_id, template)
            stmt = stmt.where(RunnerEnvironment.template_id == (found.id if found else None))
        if reuse_key:
            stmt = stmt.where(RunnerEnvironment.reuse_key == reuse_key)
        if query:
            stmt = stmt.where(RunnerEnvironment.purpose.ilike(f"%{query}%"))
        rows = await self.db.scalars(
            stmt.order_by(RunnerEnvironment.last_used_at.desc()).limit(limit)
        )
        return list(rows)

    async def conditions(self, env_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[str]]:
        if not env_ids:
            return {}
        rows = await self.db.scalars(
            select(RunnerDeleteCondition).where(
                RunnerDeleteCondition.environment_id.in_(env_ids),
                RunnerDeleteCondition.fired_at.is_(None),
            )
        )
        out: dict[uuid.UUID, list[str]] = {}
        for row in rows:
            condition = specs.Condition(row.kind, row.ref_id, row.at, row.seconds)
            out.setdefault(row.environment_id, []).append(specs.condition_text(condition))
        return out

    def next_step(
        self, env: RunnerEnvironment, policy: dict[str, float]
    ) -> tuple[str | None, float | None]:
        if env.state == "running":
            if env.lifetime == "once":
                return "运行结束后删除", 0
            idle = specs.idle_stop_after(env.lifetime, env.start_seconds, policy)
            return f"空闲 {round(idle / 60)} 分钟后停止", 0
        if env.state == "stopped":
            wake = env.start_seconds or 5
            if env.lifetime == "session":
                return f"停止 {round(policy['session_archive_hours'])} 小时后归档", wake
            if env.lifetime == "persistent":
                return f"{round(policy['persistent_archive_days'])} 天没用就归档（先做快照）", wake
            return "将被删除", wake
        if env.state == "archived":
            wake = env.rebuild_seconds or ((env.start_seconds or 5) + 30)
            return "再用时按配置记录重建", wake
        if env.state == "pending_delete" and env.delete_after is not None:
            return f"将于 {env.delete_after.isoformat(timespec='minutes')} 删除", None
        if env.state == "creating":
            return "正在创建", None
        return None, None

    async def env_out(
        self,
        env: RunnerEnvironment,
        viewer: Viewer,
        *,
        names: dict[uuid.UUID, str] | None = None,
        templates: dict[uuid.UUID, str] | None = None,
        conditions: dict[uuid.UUID, list[str]] | None = None,
        policy: dict[str, float] | None = None,
    ) -> EnvOut:
        if names is None:
            names = await self.owner_names(
                {env.owner_instance_id} if env.owner_instance_id else set()
            )
        if templates is None:
            templates = {}
            if env.template_id is not None:
                template = await self.db.get(RunnerTemplate, env.template_id)
                if template is not None:
                    templates[template.id] = template.name
        if conditions is None:
            conditions = await self.conditions([env.id])
        if policy is None:
            host = await self.db.get(RunnerHost, env.host_id)
            policy = specs.policy_for(host.policy if host else None)
        step, wake = self.next_step(env, policy)
        spec = env.spec
        return EnvOut(
            id=env.id,
            untrusted=EnvText(purpose=env.purpose),
            lifetime=env.lifetime,
            state=env.state,
            state_reason=env.state_reason,
            template=templates.get(env.template_id) if env.template_id else None,
            reuse_key=env.reuse_key,
            owner=names.get(env.owner_instance_id, "已删除的实例")
            if env.owner_instance_id
            else "已删除的实例",
            mine=viewer.instance is not None and env.owner_instance_id == viewer.instance.id,
            runtime=spec.get("runtime", "gvisor"),
            user=spec.get("user", "user"),
            network=spec.get("network", "none"),
            memory_mb=int(spec.get("memory_mb", 0)),
            cpus=float(spec.get("cpus", 0)),
            delete_when=conditions.get(env.id, []),
            share=env.share,
            project_id=env.project_id,
            pinned=env.pinned,
            use_count=env.use_count,
            created_at=env.created_at,
            last_used_at=env.last_used_at,
            next_step=step,
            wake_seconds=wake,
        )

    # ------------------------------------------------------------------ 环境：限额

    async def _check_secrets(self, instance: AgentInstance, names: list[str]) -> None:
        if not names:
            return
        granted = set(
            await self.db.scalars(
                select(AgentSecretGrant.ref_id).where(AgentSecretGrant.instance_id == instance.id)
            )
        )
        missing = [n for n in names if n not in granted]
        if missing:
            raise RunnerError(
                "FORBIDDEN",
                f"这些密钥没有授权给你：{', '.join(missing[:5])}（管理员在密钥库里授权）",
            )

    async def _check_quota(
        self, instance: AgentInstance, host: RunnerHost, spec: dict[str, Any], lifetime: str
    ) -> None:
        limits = specs.limits_for(instance.runner_limits)
        rows = (
            await self.db.execute(
                select(RunnerEnvironment.lifetime, RunnerEnvironment.spec).where(
                    RunnerEnvironment.owner_instance_id == instance.id,
                    RunnerEnvironment.state.in_(ACTIVE_STATES),
                )
            )
        ).all()
        running = len(rows)
        persistent = sum(1 for life, _ in rows if life == "persistent")
        memory = sum(int(s.get("memory_mb", 0)) for _, s in rows)
        cpus = sum(float(s.get("cpus", 0)) for _, s in rows)
        disk = sum(int(s.get("disk_gb", 0)) for _, s in rows)
        if running + 1 > limits["envs"]:
            raise RunnerError(
                "QUOTA_EXCEEDED",
                f"你同时运行的环境已到上限（{int(limits['envs'])} 个）；先停掉或释放用不着的",
            )
        if lifetime == "persistent" and persistent + 1 > limits["persistent"]:
            raise RunnerError("QUOTA_EXCEEDED", f"常驻环境最多 {int(limits['persistent'])} 个")
        if memory + spec["memory_mb"] > limits["memory_mb"]:
            raise RunnerError(
                "QUOTA_EXCEEDED",
                f"内存合计会超过你的限额（{int(limits['memory_mb'])} MB）；"
                "调小 memory_mb 或先停掉别的",
            )
        if cpus + spec["cpus"] > limits["cpus"]:
            raise RunnerError("QUOTA_EXCEEDED", f"CPU 合计会超过你的限额（{limits['cpus']:g} 核）")
        if disk + spec["disk_gb"] > limits["disk_gb"]:
            raise RunnerError(
                "QUOTA_EXCEEDED", f"磁盘合计会超过你的限额（{int(limits['disk_gb'])} GB）"
            )
        if spec["runtime"] == "gvisor" and host.runtimes and "runsc" not in host.runtimes:
            raise RunnerError("RUNNER_CAPABILITY", "容器服务器没有装 gVisor（runsc）；可以换 runc")
        if spec["runtime"] == "kvm" and not host.kvm:
            raise RunnerError(
                "RUNNER_CAPABILITY",
                "运行服务器没有 /dev/kvm（需要开启硬件虚拟化）；可以换 runc 或 gvisor",
            )
        # 磁盘告急（第 6.4 节）：拒绝新建，先等回收
        policy = specs.policy_for(host.policy)
        total = int((host.report or {}).get("disk_bytes") or host.disk_bytes or 0)
        free = int((host.report or {}).get("disk_free_bytes") or host.disk_free_bytes or 0)
        if total and 1 - free / total > policy["disk_emergency"]:
            raise RunnerError("RESOURCE_BUSY", "容器服务器磁盘告急，暂停新建环境；稍后再试")
        # 整台服务器：内存按上限累计，不超卖（留 10% 给系统）
        if host.mem_bytes:
            used = await self.db.scalars(
                select(RunnerEnvironment.spec).where(
                    RunnerEnvironment.host_id == host.id,
                    RunnerEnvironment.state.in_(ACTIVE_STATES),
                )
            )
            total = sum(int(s.get("memory_mb", 0)) for s in used) + spec["memory_mb"]
            over = total * 1024 * 1024 - int(host.mem_bytes * 0.9)
            if over > 0:
                # 先按保留分停掉空闲的环境腾地方（停止要等 runner-agent 做完，这次仍然返回忙）
                from app.domain.runner_reclaim import make_room

                stopped = await make_room(self, host, over)
                if stopped:
                    await self.db.commit()
                    raise RunnerError(
                        "RESOURCE_BUSY",
                        f"容器服务器内存不够：已按保留分停止 {stopped} 个空闲环境，约 30 秒后再试",
                    )
                raise RunnerError(
                    "RESOURCE_BUSY", "运行服务器的内存已经分配满了；稍后再试，或调小 memory_mb"
                )

    async def check_exec_rate(self, instance: AgentInstance) -> None:
        limits = specs.limits_for(instance.runner_limits)
        if self.redis is not None:
            limiter = WindowLimiter(self.redis, 3600)
            if await limiter.hit(f"runner_exec:{instance.id}", int(limits["execs_per_hour"])):
                raise RunnerError(
                    "RATE_LIMITED", f"每小时最多执行 {int(limits['execs_per_hour'])} 次命令"
                )
        today = specs.now().replace(hour=0, minute=0, second=0, microsecond=0)
        used = await self.db.scalar(
            select(
                func.coalesce(
                    func.sum(
                        func.extract(
                            "epoch",
                            func.coalesce(RunnerRun.finished_at, func.now()) - RunnerRun.started_at,
                        )
                    ),
                    0,
                )
            ).where(
                RunnerRun.instance_id == instance.id,
                RunnerRun.started_at.is_not(None),
                RunnerRun.created_at >= today,
            )
        )
        if float(used or 0) > limits["run_hours_per_day"] * 3600:
            raise RunnerError(
                "RATE_LIMITED", f"今天的运行时长已到上限（{limits['run_hours_per_day']:g} 小时）"
            )

    # ------------------------------------------------------------------ 环境：建、用、停、删

    async def _reusable(
        self, instance: AgentInstance, template_id: uuid.UUID | None, image: str, reuse_key: str
    ) -> RunnerEnvironment | None:
        viewer = Viewer(instance.workspace_id, instance=instance)
        for env in await self.find(viewer, reuse_key=reuse_key):
            # 归档的也复用：用到时按配置记录重建
            if env.state not in ("running", "stopped", "creating", "archived"):
                continue
            if template_id is not None and env.template_id != template_id:
                continue
            if template_id is None and env.spec.get("image") != image:
                continue
            return env
        return None

    async def create(
        self, instance: AgentInstance, body: EnvCreateInput
    ) -> tuple[RunnerEnvironment, bool, str | None, RunnerRun | None]:
        viewer = Viewer(instance.workspace_id, instance=instance)
        host = await self.online_host(instance.workspace_id)
        template: RunnerTemplate | None = None
        if body.template:
            template = await self.template_by_name(instance.workspace_id, body.template)
            if template is None or template.status != "active":
                raise RunnerError(
                    "NOT_FOUND", f"没有模板 {body.template}（runner_templates 能看到）"
                )
            if template.build_status != "ready":
                state = (
                    "构建失败：" + (template.build_error or "")
                    if template.build_status == "failed"
                    else "还在构建"
                )
                raise RunnerError("TEMPLATE_NOT_READY", f"模板 {template.name} {state}")
            raw = dict(template.spec)
        elif body.spec is not None:
            raw = body.spec.model_dump()
            if raw.get("steps"):
                raise RunnerError(
                    "VALIDATION_FAILED",
                    "有安装步骤时先用 runner_template_save 建模板，再用模板建环境",
                )
        else:
            raise RunnerError("VALIDATION_FAILED", "要给 template 或 spec")
        if body.spec is not None and template is not None:
            overrides = body.spec.model_dump(exclude_unset=True)
            for key in ("memory_mb", "cpus", "pids", "disk_gb", "env", "network", "domains"):
                if key in overrides:
                    raw[key] = overrides[key]
        try:
            spec = specs.normalize(raw)
            conditions = specs.parse_conditions(body.delete_when)
            command = specs.command_list(body.command) if body.command is not None else None
        except specs.SpecError as error:
            raise RunnerError("VALIDATION_FAILED", str(error)) from None
        if body.lifetime == "once" and command is None:
            raise RunnerError(
                "VALIDATION_FAILED", "lifetime=once 要带 command：建好就执行，执行完就删"
            )
        if command is not None:
            await self.check_exec_rate(instance)
        if body.share == "project" and body.project_id is None:
            raise RunnerError("VALIDATION_FAILED", "共享给项目时要给 project_id")
        if body.share == "project" and body.project_id not in await self._project_ids(instance):
            raise RunnerError("FORBIDDEN", "只能共享给你参与的项目")
        await self._check_secrets(instance, spec["secrets"])
        # 先复用
        if body.reuse_key and not body.new:
            found = await self._reusable(
                instance, template.id if template else None, spec["image"], body.reuse_key
            )
            if found is not None:
                await self.touch(found)
                run = None
                if command is not None:
                    run = await self.start_run(instance, found, command, body.timeout_seconds)
                elif found.state == "archived":
                    await self.rebuild(found, "复用，自动重建")
                await self.db.commit()
                return found, True, "已复用同一个 reuse_key 的环境", run
        if body.new and not (body.new_reason or "").strip():
            raise RunnerError("VALIDATION_FAILED", "new=true 时要写 new_reason")
        source: RunnerEnvironment | None = None
        if body.from_environment is not None:
            source = await self.env(viewer, body.from_environment)
            if source.state not in ("running", "stopped"):
                raise RunnerError("VALIDATION_FAILED", "要复制的环境已经归档或删除")
        await self._check_quota(instance, host, spec, body.lifetime)
        files = self._files(body.files)
        env_id = uuid.uuid4()
        names = specs.names(env_id)
        record = dict(spec)
        if template is not None:
            record["template_image"] = template.image_ref
            record["image_digest"] = template.image_digest
        env = RunnerEnvironment(
            id=env_id,
            workspace_id=instance.workspace_id,
            host_id=host.id,
            template_id=template.id if template else None,
            template_revision=template.revision if template else None,
            owner_instance_id=instance.id,
            purpose=body.purpose,
            lifetime=body.lifetime,
            reuse_key=body.reuse_key,
            state="creating",
            spec=record,
            share=body.share,
            project_id=body.project_id if body.share == "project" else None,
            container_name=names["container"],
            volume_name=names["volume"],
            network_name=names["network"],
        )
        self.db.add(env)
        await self.db.flush()
        if body.lifetime == "once" and not any(c.kind == "run_finished" for c in conditions):
            conditions.append(specs.Condition("run_finished"))
        for condition in conditions:
            self.db.add(
                RunnerDeleteCondition(
                    environment_id=env.id,
                    kind=condition.kind,
                    ref_id=condition.ref_id,
                    at=condition.at,
                    seconds=condition.seconds,
                    created_by_instance_id=instance.id,
                )
            )
        self.enqueue(
            host,
            "create_env",
            self.create_payload(env, files=files, source=source),
            env=env,
        )
        if template is not None:
            template.use_count += 1
            template.last_used_at = specs.now()
        run = None
        if command is not None:
            run = await self.start_run(instance, env, command, body.timeout_seconds)
        await self.audit.record(
            "runner.environment_created",
            workspace_ids=[instance.workspace_id],
            actor_id=None,
            resource_type="runner_environment",
            resource_id=env.id,
            metadata={
                "by_instance": str(instance.id),
                "purpose": body.purpose,
                "lifetime": body.lifetime,
                "template": template.name if template else None,
                "image": spec["image"],
                "non_default": specs.non_default(spec),
            },
        )
        notes = specs.non_default(spec)
        if body.lifetime == "persistent" or notes:
            detail = "、".join(notes) if notes else "默认设置"
            kind = "常驻" if body.lifetime == "persistent" else ""
            await notifications.notify_admins(
                self.db,
                instance.workspace_id,
                type_="runner.environment_created",
                severity="info",
                title=f"智能体「{instance.name}」建了{kind}环境",
                body=f"{body.purpose}（{spec['image']}，{detail}）",
                link="/runner?tab=environments",
                key=str(env.id),
            )
        await self.db.commit()
        return env, False, self._hint(body), run

    def _hint(self, body: EnvCreateInput) -> str | None:
        if body.lifetime == "once" and body.reuse_key:
            return "一次性环境跑完就删；同一件事要反复做，用 session 能省去每次重建"
        return None

    def _files(self, items: list[Any]) -> list[dict[str, str]]:
        files: list[dict[str, str]] = []
        total = 0
        for item in items:
            try:
                path = specs.clean_relative(item.path, allow_root=False)
            except specs.SpecError as error:
                raise RunnerError("VALIDATION_FAILED", str(error)) from None
            if item.text is not None:
                data = item.text.encode()
            elif item.content_base64 is not None:
                try:
                    data = base64.b64decode(item.content_base64, validate=True)
                except ValueError:
                    raise RunnerError("VALIDATION_FAILED", f"{path} 的 base64 不对") from None
            else:
                raise RunnerError("VALIDATION_FAILED", f"{path} 要给 text 或 content_base64")
            total += len(data)
            if total > specs.FILE_LIMIT:
                raise RunnerError("PAYLOAD_TOO_LARGE", "文件合计最多 20 MB")
            files.append({"path": path, "b64": base64.b64encode(data).decode()})
        return files

    def create_payload(
        self,
        env: RunnerEnvironment,
        *,
        files: list[dict[str, str]] | None = None,
        source: RunnerEnvironment | None = None,
        snapshot: dict[str, Any] | None = None,
        template: RunnerTemplate | None = None,
    ) -> dict[str, Any]:
        spec = env.spec
        image = spec.get("template_image") or spec["image"]
        build = None
        if template is not None and template.spec.get("steps") and spec.get("template_image"):
            # 模板镜像被清理后，runner-agent 按这些信息先重新构建再建环境（重建时用）
            build = {
                "template_id": str(template.id),
                "image": template.spec["image"],
                "steps": list(template.spec.get("steps") or []),
                "tag": spec["template_image"],
            }
        return {
            "env_id": str(env.id),
            "workspace_id": str(env.workspace_id),
            "container": env.container_name,
            "volume": env.volume_name,
            "network": env.network_name,
            "image": image,
            "runtime": spec["runtime"],
            "user": spec["user"],
            "network_mode": spec["network"],
            "domains": spec["domains"],
            "memory_mb": spec["memory_mb"],
            "cpus": spec["cpus"],
            "pids": spec["pids"],
            "env": spec["env"],
            "start_command": spec.get("start_command"),
            "secrets": spec.get("secrets", []),
            "files": files or [],
            "copy_from_volume": source.volume_name if source is not None else None,
            "setup": list(env.setup or []),
            "snapshot": snapshot,
            "template_build": build,
            "lifetime": env.lifetime,
        }

    def runtime_payload(self, env: RunnerEnvironment) -> dict[str, Any]:
        """启动、执行时让 runner-agent 补齐的信息（容器停着就先启动、再注入密钥、登记出口策略）。"""
        payload = self.create_payload(env)
        for key in ("files", "copy_from_volume", "snapshot", "template_build", "setup"):
            payload.pop(key)
        return payload

    async def touch(self, env: RunnerEnvironment) -> None:
        env.last_used_at = specs.now()
        env.use_count += 1

    async def start_run(
        self,
        instance: AgentInstance | None,
        env: RunnerEnvironment,
        command: list[str],
        timeout_seconds: int,
    ) -> RunnerRun:
        if env.state in ("pending_delete", "deleted", "failed"):
            raise RunnerError("ENV_UNAVAILABLE", f"环境现在是 {env.state}，不能执行")
        host = await self.db.get(RunnerHost, env.host_id)
        if host is None:
            raise RunnerError("RUNNER_UNAVAILABLE", "容器服务器已移除")
        extra = 0
        if env.state == "archived":
            # 归档的环境按配置记录重建（恢复快照、重放初始化步骤），exec 排在重建之后
            await self.rebuild(env, "有人使用，自动重建")
            extra = 3600
        run = RunnerRun(
            id=uuid.uuid4(),
            workspace_id=env.workspace_id,
            environment_id=env.id,
            instance_id=instance.id if instance else None,
            command=_redact(specs.command_text(command), []),
            timeout_seconds=timeout_seconds,
        )
        self.db.add(run)
        await self.db.flush()
        payload = self.runtime_payload(env)
        payload.update({"run_id": str(run.id), "cmd": command, "timeout": timeout_seconds})
        self.enqueue(host, "exec", payload, env=env, run=run, seconds=timeout_seconds + 180 + extra)
        env.last_used_at = specs.now()
        return run

    async def exec(self, instance: AgentInstance, body: EnvExecInput) -> RunnerRun:
        viewer = Viewer(instance.workspace_id, instance=instance)
        env = await self.env(viewer, body.environment_id)
        await self.online_host(instance.workspace_id)
        await self.check_exec_rate(instance)
        try:
            command = specs.command_list(body.command)
            workdir = specs.clean_relative(body.workdir or "")
            extra_env = specs.clean_env(body.env)
        except specs.SpecError as error:
            raise RunnerError("VALIDATION_FAILED", str(error)) from None
        run = await self.start_run(instance, env, command, body.timeout_seconds)
        # 工作目录与临时变量随这次运行下发
        pending = await self.db.scalar(select(RunnerCommand).where(RunnerCommand.run_id == run.id))
        if pending is not None:
            payload = dict(pending.payload)
            payload["workdir"] = workdir
            payload["exec_env"] = extra_env
            pending.payload = payload
        await self.touch(env)
        await self.db.commit()
        return run

    async def stop(self, viewer: Viewer, env: RunnerEnvironment, reason: str) -> None:
        if env.state not in ("running", "creating"):
            return
        host = await self.db.get(RunnerHost, env.host_id)
        if host is None:
            return
        self.enqueue(
            host, "stop_env", {"env_id": str(env.id), "container": env.container_name}, env=env
        )
        env.state_reason = reason

    async def delete(self, env: RunnerEnvironment, reason: str, *, snapshot: bool = True) -> None:
        """删容器、网络与数据卷。常驻环境先做快照（保留 30 天，可以从页面恢复）。"""
        if env.state == "deleted":
            return
        host = await self.db.get(RunnerHost, env.host_id)
        archived = self.was_archived(env)
        if (
            snapshot
            and host is not None
            and env.lifetime == "persistent"
            and not archived
            and env.state not in ("failed", "creating")
            and not await self._fresh_snapshot(env)
        ):
            await self.start_snapshot(host, env, then="delete")
            env.state_reason = reason
            return
        if host is not None and not archived:
            self.enqueue(
                host,
                "delete_env",
                {
                    "env_id": str(env.id),
                    "container": env.container_name,
                    "volume": env.volume_name,
                    "network": env.network_name,
                },
                env=env,
            )
        else:
            env.state = "deleted"
            env.deleted_at = specs.now()
            await self._keep_snapshots(env)
        env.state_reason = reason

    @staticmethod
    def was_archived(env: RunnerEnvironment) -> bool:
        """已经归档（容器与数据卷都删了）：归档状态，或者归档以后进入了待删除。"""
        return env.state == "archived" or (
            env.state == "pending_delete" and env.archived_at is not None
        )

    # ------------------------------------------------------------------ 快照、归档与重建（T-0139）

    async def _fresh_snapshot(self, env: RunnerEnvironment) -> bool:
        """有没有比最近一次使用更新、已就绪的快照。"""
        if not env.snapshot_key or env.snapshot_at is None:
            return False
        return env.snapshot_at >= env.last_used_at

    async def latest_snapshot(self, env: RunnerEnvironment) -> RunnerSnapshot | None:
        result: RunnerSnapshot | None = await self.db.scalar(
            select(RunnerSnapshot)
            .where(RunnerSnapshot.environment_id == env.id, RunnerSnapshot.status == "ready")
            .order_by(RunnerSnapshot.created_at.desc())
            .limit(1)
        )
        return result

    async def start_snapshot(self, host: RunnerHost, env: RunnerEnvironment, *, then: str) -> None:
        """常驻环境做快照（环境先停下来，数据一致），完成后接着 archive 或 delete。"""
        from app.domain.runner_snapshots import PART_BYTES, SnapshotStore

        pending = await self.db.scalar(
            select(RunnerCommand.id).where(
                RunnerCommand.environment_id == env.id,
                RunnerCommand.kind == "snapshot",
                RunnerCommand.status.in_(("pending", "sent")),
            )
        )
        if pending is not None:
            return
        if env.state in ("running", "creating"):
            self.enqueue(
                host, "stop_env", {"env_id": str(env.id), "container": env.container_name}, env=env
            )
        snapshot = await SnapshotStore(self.db, self.settings).begin(env)
        await self.db.flush()
        policy = specs.policy_for(host.policy)
        self.enqueue(
            host,
            "snapshot",
            {
                "env_id": str(env.id),
                "snapshot_id": str(snapshot.id),
                "volume": env.volume_name,
                "user": env.spec.get("user", "user"),
                "paths": list(env.spec.get("persist") or []) or ["."],
                "part_bytes": PART_BYTES,
                "max_bytes": int(policy["snapshot_max_gb"] * 1024**3),
                "then": then,
            },
            env=env,
        )

    async def archive(self, env: RunnerEnvironment, reason: str) -> bool:
        """归档：常驻环境先做快照；会话、一次性的直接删容器与数据卷，只留配置记录。"""
        if env.state not in ("running", "stopped") or env.pinned:
            return False
        host = await self.db.get(RunnerHost, env.host_id)
        if host is None:
            return False
        env.state_reason = reason
        if env.lifetime == "persistent" and not await self._fresh_snapshot(env):
            await self.start_snapshot(host, env, then="archive")
            return True
        self.enqueue(
            host,
            "archive_env",
            {
                "env_id": str(env.id),
                "container": env.container_name,
                "volume": env.volume_name,
                "network": env.network_name,
            },
            env=env,
        )
        return True

    async def rebuild(self, env: RunnerEnvironment, reason: str) -> None:
        """按配置记录重建：模板镜像（被清理了就先重建）→ 恢复快照 → 重放初始化步骤 → 启动。"""
        host = await self.online_host(env.workspace_id)
        snapshot_info: dict[str, Any] | None = None
        if env.lifetime == "persistent":
            snapshot = await self.latest_snapshot(env)
            if snapshot is not None:
                snapshot_info = {
                    "id": str(snapshot.id),
                    "entries": list(snapshot.entries),
                    "parts": snapshot.parts,
                }
        template = await self.db.get(RunnerTemplate, env.template_id) if env.template_id else None
        env.state = "creating"
        env.state_reason = reason
        self.enqueue(
            host,
            "create_env",
            self.create_payload(env, snapshot=snapshot_info, template=template),
            env=env,
            seconds=DEADLINES["create_env"] + (DEADLINES["restore"] if snapshot_info else 0),
        )

    async def files(self, instance: AgentInstance, body: EnvFilesInput) -> EnvFilesOutput:
        viewer = Viewer(instance.workspace_id, instance=instance)
        env = await self.env(viewer, body.environment_id)
        host = await self.online_host(instance.workspace_id)
        if env.state == "archived":
            await self.rebuild(env, "读写文件，自动重建")
        elif env.state not in ("running", "stopped"):
            raise RunnerError("ENV_UNAVAILABLE", f"环境现在是 {env.state}，不能读写文件")
        try:
            path = specs.clean_relative(body.path, allow_root=body.action == "list")
        except specs.SpecError as error:
            raise RunnerError("VALIDATION_FAILED", str(error)) from None
        base = {"env_id": str(env.id), "volume": env.volume_name, "path": path}
        if body.action == "write":
            if body.text is not None:
                data = body.text.encode()
            elif body.content_base64 is not None:
                try:
                    data = base64.b64decode(body.content_base64, validate=True)
                except ValueError:
                    raise RunnerError("VALIDATION_FAILED", "content_base64 不对") from None
            else:
                raise RunnerError("VALIDATION_FAILED", "写文件要给 text 或 content_base64")
            if len(data) > specs.FILE_LIMIT:
                raise RunnerError("PAYLOAD_TOO_LARGE", "单个文件最多 20 MB")
            command = self.enqueue(
                host,
                "put_files",
                {**base, "files": [{"path": path, "b64": base64.b64encode(data).decode()}]},
                env=env,
            )
        elif body.action == "read":
            command = self.enqueue(host, "get_files", {**base, "limit": specs.FILE_LIMIT}, env=env)
        else:
            command = self.enqueue(host, "list_files", base, env=env)
        await self.touch(env)
        await self.db.commit()
        done = await self.wait_command(command.id, specs.SYNC_WAIT_SECONDS)
        if done is None:
            raise RunnerError("TIMEOUT", "容器服务器没有及时响应，稍后再试")
        if done.status != "done":
            raise RunnerError("RUNNER_FAILED", done.error or "容器服务器执行失败")
        result = done.result
        if body.action == "write":
            return EnvFilesOutput(written=True, size=int(result.get("size", 0)))
        if body.action == "read":
            raw = base64.b64decode(result.get("b64", ""))
            try:
                return EnvFilesOutput(text=raw.decode(), size=len(raw))
            except UnicodeDecodeError:
                return EnvFilesOutput(content_base64=base64.b64encode(raw).decode(), size=len(raw))
        entries = [
            EnvFileEntry(
                path=str(e.get("path")), kind=e.get("kind", "other"), size=int(e.get("size", 0))
            )
            for e in result.get("entries", [])[:1000]
        ]
        return EnvFilesOutput(entries=entries)

    async def update(self, instance: AgentInstance, body: EnvUpdateInput) -> RunnerEnvironment:
        viewer = Viewer(instance.workspace_id, instance=instance)
        env = await self.env(viewer, body.environment_id)
        if env.owner_instance_id != instance.id:
            raise RunnerError("FORBIDDEN", "只有建这个环境的智能体能改它")
        if body.keep:
            await self.keep(env, "创建者撤销删除")
        if body.purpose is not None:
            env.purpose = body.purpose
        if body.share is not None:
            if body.share == "project":
                if body.project_id is None or body.project_id not in await self._project_ids(
                    instance
                ):
                    raise RunnerError("FORBIDDEN", "只能共享给你参与的项目（给 project_id）")
                env.project_id = body.project_id
            else:
                env.project_id = None
            env.share = body.share
        if body.delete_when is not None:
            try:
                conditions = specs.parse_conditions(body.delete_when)
            except specs.SpecError as error:
                raise RunnerError("VALIDATION_FAILED", str(error)) from None
            old = await self.db.scalars(
                select(RunnerDeleteCondition).where(
                    RunnerDeleteCondition.environment_id == env.id,
                    RunnerDeleteCondition.fired_at.is_(None),
                )
            )
            for row in old:
                await self.db.delete(row)
            for condition in conditions:
                self.db.add(
                    RunnerDeleteCondition(
                        environment_id=env.id,
                        kind=condition.kind,
                        ref_id=condition.ref_id,
                        at=condition.at,
                        seconds=condition.seconds,
                        created_by_instance_id=instance.id,
                    )
                )
        env.version += 1
        await self.db.commit()
        return env

    async def setup(
        self, instance: AgentInstance, env_id: uuid.UUID, steps: list[str]
    ) -> RunnerEnvironment:
        viewer = Viewer(instance.workspace_id, instance=instance)
        env = await self.env(viewer, env_id)
        if env.owner_instance_id != instance.id:
            raise RunnerError("FORBIDDEN", "只有建这个环境的智能体能改它")
        if any(len(s) > 8000 for s in steps):
            raise RunnerError("VALIDATION_FAILED", "每条初始化步骤最多 8000 字")
        env.setup = list(steps)
        env.version += 1
        await self.db.commit()
        return env

    async def stop_by_owner(self, instance: AgentInstance, env_id: uuid.UUID) -> RunnerEnvironment:
        viewer = Viewer(instance.workspace_id, instance=instance)
        env = await self.env(viewer, env_id)
        if env.owner_instance_id != instance.id:
            raise RunnerError("FORBIDDEN", "只有建这个环境的智能体能停它；不用了就 env_release")
        await self.stop(viewer, env, "智能体停止")
        await self.db.commit()
        return env

    async def release(self, instance: AgentInstance, env_id: uuid.UUID) -> RunnerEnvironment:
        """创建者调用触发 released 条件（有这个条件就进入待删除）；别人调用只是不再使用。"""
        viewer = Viewer(instance.workspace_id, instance=instance)
        env = await self.env(viewer, env_id)
        if env.owner_instance_id != instance.id:
            return env
        condition = await self.db.scalar(
            select(RunnerDeleteCondition).where(
                RunnerDeleteCondition.environment_id == env.id,
                RunnerDeleteCondition.kind == "released",
                RunnerDeleteCondition.fired_at.is_(None),
            )
        )
        if condition is not None:
            condition.fired_at = specs.now()
            await self.mark_pending_delete(env, "创建者释放")
        else:
            await self.stop(viewer, env, "创建者释放（没有登记 released 条件，只停止）")
        await self.db.commit()
        return env

    async def mark_pending_delete(self, env: RunnerEnvironment, reason: str) -> None:
        if env.state in ("pending_delete", "deleted"):
            return
        host = await self.db.get(RunnerHost, env.host_id)
        policy = specs.policy_for(host.policy if host else None)
        grace = (
            policy["persistent_grace_hours"] * 3600
            if env.lifetime == "persistent"
            else policy["grace_minutes"] * 60
        )
        if env.lifetime == "once":
            grace = 0
        # 有宽限期才先停止（释放内存）；马上就删的直接删（删除会连容器一起移除）
        if grace > 0 and env.state in ("running", "creating") and host is not None:
            self.enqueue(
                host, "stop_env", {"env_id": str(env.id), "container": env.container_name}, env=env
            )
        env.state = "pending_delete"
        env.state_reason = reason
        env.delete_after = specs.later(grace)
        if env.lifetime == "persistent":
            hours = round(grace / 3600)
            await notifications.notify_admins(
                self.db,
                env.workspace_id,
                type_="runner.environment_pending_delete",
                severity="warning",
                title="常驻环境将被删除",
                body=f"{env.purpose}：{reason}；{hours} 小时后删除"
                "（先做快照，保留 30 天），可以撤销",
                link="/runner?tab=environments",
                key=f"{env.id}:pending_delete",
            )
            if env.owner_instance_id is not None:
                self.db.add(
                    AgentWakeup(
                        id=uuid.uuid4(),
                        workspace_id=env.workspace_id,
                        instance_id=env.owner_instance_id,
                        source="runner",
                        target=None,
                        session_key=f"runner:env:{env.id}",
                        prompt=(
                            f"[工作台] 你的常驻环境 {env.id} 因为“{reason}”进入待删除，"
                            f"{hours} 小时后删除（先做快照）。"
                            "还要用就调用 env_update 并设置 keep=true。"
                        ),
                        run_at=specs.now(),
                    )
                )

    async def keep(self, env: RunnerEnvironment, reason: str) -> None:
        """撤销待删除：回到停止状态，删除条件里已经触发的不再生效。"""
        if env.state != "pending_delete":
            raise RunnerError("VALIDATION_FAILED", "这个环境不在待删除状态")
        env.state = "archived" if env.archived_at is not None else "stopped"
        env.delete_after = None
        env.state_reason = reason

    # ------------------------------------------------------------------ 运行

    def run_out(self, run: RunnerRun) -> RunOut:
        seconds = None
        if run.started_at and run.finished_at:
            seconds = (run.finished_at - run.started_at).total_seconds()
        output = run.output
        truncated = run.output_truncated or len(output) > specs.OUTPUT_RETURN
        return RunOut(
            run_id=run.id,
            environment_id=run.environment_id,
            status=run.status,
            exit_code=run.exit_code,
            untrusted=RunText(output=output[-specs.OUTPUT_RETURN :]),
            output_truncated=truncated,
            seconds=seconds,
            started_at=run.started_at,
            finished_at=run.finished_at,
        )

    async def run_for(self, viewer: Viewer, run_id: uuid.UUID) -> RunnerRun:
        run = await self.db.get(RunnerRun, run_id)
        if run is None or run.workspace_id != viewer.workspace_id:
            raise RunnerError("NOT_FOUND", "没有这次运行")
        env = await self.db.get(RunnerEnvironment, run.environment_id)
        if env is None or not await self.can_use(env, viewer):
            raise RunnerError("NOT_FOUND", "没有这次运行")
        return run

    # ------------------------------------------------------------------ runner-agent 的结果

    async def secrets_for(self, env: RunnerEnvironment) -> list[str]:
        """这个环境注入的密钥原文（只用来脱敏输出）。"""
        from app.domain.runner_hosts import resolve_env_secrets  # 避免循环导入

        values = await resolve_env_secrets(self.db, self.settings, env, record=False)
        return list(values.values())

    async def apply_result(
        self, command: RunnerCommand, status: str, result: dict[str, Any], error: str | None
    ) -> None:
        command.status = status
        command.result = (
            {k: v for k, v in result.items() if k != "b64"}
            if command.kind != "get_files"
            else result
        )
        command.error = (error or "")[:4000] or None
        command.finished_at = specs.now()
        # 锁住环境这一行再改：同一个环境的两条指令结果可能几乎同时到（建环境与一次性命令的结束），
        # 不加锁时后提交的那条会拿着旧状态把前一条写的状态盖掉（例如把“待删除”改回“运行中”）
        env = (
            await self.db.get(
                RunnerEnvironment,
                command.environment_id,
                with_for_update=True,
                populate_existing=True,
            )
            if command.environment_id
            else None
        )
        ok = status == "done"
        kind = command.kind
        if kind == "build_template" and command.template_id is not None:
            template = await self.db.get(RunnerTemplate, command.template_id)
            if (
                template is not None
                and int(command.payload.get("revision", 0)) == template.revision
            ):
                template.build_status = "ready" if ok else "failed"
                template.build_error = None if ok else (error or "构建失败")[:2000]
                template.build_log = _redact(str(result.get("log", ""))[-20000:], [])
                if ok:
                    template.image_ref = str(result.get("image_ref") or command.payload.get("tag"))
                    template.image_digest = result.get("image_digest")
                    template.image_bytes = result.get("image_bytes")
                    template.build_seconds = result.get("seconds")
                    template.built_at = specs.now()
        elif kind in ("create_env", "start_env") and env is not None:
            if ok:
                if env.state in ("creating", "stopped", "running", "archived"):
                    env.state = "running"
                    env.state_reason = None
                env.started_at = specs.now()
                env.archived_at = None  # 重建好了：又有容器与数据卷
                seconds = result.get("seconds")
                if isinstance(seconds, int | float):
                    if kind == "create_env":
                        env.start_seconds = (
                            float(seconds) if env.start_seconds is None else env.start_seconds
                        )
                        if command.payload.get("snapshot"):
                            env.rebuild_seconds = float(seconds)
                    else:
                        env.start_seconds = float(seconds)
                if result.get("image_digest") and not env.spec.get("image_digest"):
                    env.spec = {**env.spec, "image_digest": result["image_digest"]}
            elif env.state in ("creating", "stopped", "archived"):
                env.state = "failed" if kind == "create_env" else env.state
                env.state_reason = (error or "启动失败")[:500]
                await self._fail_pending_runs(env, error or "环境没有启动成功")
        elif kind == "stop_env" and env is not None and ok:
            if env.state in ("running", "creating"):
                env.state = "stopped"
            env.stopped_at = specs.now()
        elif kind == "delete_env" and env is not None and ok:
            env.state = "deleted"
            env.deleted_at = specs.now()
            await self._keep_snapshots(env)
        elif kind == "archive_env" and env is not None and ok:
            env.state = "archived"
            env.archived_at = specs.now()
            # 容器与数据卷都删了：现时读数一并清空（快照大小另记在 snapshot_bytes）
            env.mem_bytes = env.mem_reported_at = None
            env.cpu_used_cores = env.cpu_window_seconds = env.cpu_reported_at = None
        elif kind == "exec" and command.run_id is not None:
            await self._finish_run(command, ok, result, error, env)
        elif kind == "snapshot" and env is not None:
            await self._after_snapshot(env, command, ok, result, error)

    async def _keep_snapshots(self, env: RunnerEnvironment) -> None:
        """环境删除后，它的快照再保留 archive_retention_days 天（可以从页面恢复）。"""
        host = await self.db.get(RunnerHost, env.host_id)
        days = specs.policy_for(host.policy if host else None)["archive_retention_days"]
        for snapshot in await self.db.scalars(
            select(RunnerSnapshot).where(
                RunnerSnapshot.environment_id == env.id, RunnerSnapshot.status == "ready"
            )
        ):
            snapshot.expires_at = specs.later(days * 86400)

    async def _after_snapshot(
        self,
        env: RunnerEnvironment,
        command: RunnerCommand,
        ok: bool,
        result: dict[str, Any],
        error: str | None,
    ) -> None:
        snapshot_id = command.payload.get("snapshot_id")
        snapshot = (
            await self.db.get(RunnerSnapshot, uuid.UUID(str(snapshot_id))) if snapshot_id else None
        )
        then = str(command.payload.get("then") or "")
        if not ok or snapshot is None:
            if snapshot is not None:
                snapshot.status = "failed"
                snapshot.error = (error or "快照失败")[:500]
            # 快照做不出来：不归档、不删除（数据重建不出来），只停着，通知管理员
            if env.state == "pending_delete":
                env.state = "stopped"
                env.delete_after = None
            action = "删除" if then == "delete" else "归档"
            env.state_reason = f"快照失败，没有{action}：{(error or '')[:200]}"
            await notifications.notify_admins(
                self.db,
                env.workspace_id,
                type_="runner.snapshot_failed",
                severity="warning",
                title="常驻环境的快照失败",
                body=f"{env.purpose}：{(error or '')[:200]}；环境保持停止，没有归档或删除",
                link="/runner?tab=environments",
                key=f"{env.id}:snapshot:{command.id}",
            )
            return
        snapshot.status = "ready"
        snapshot.entries = list(result.get("entries") or [])
        snapshot.parts = int(result.get("parts") or 0)
        snapshot.bytes = int(result.get("bytes") or 0)
        snapshot.ready_at = specs.now()
        env.snapshot_key = str(snapshot.id)
        env.snapshot_bytes = snapshot.bytes
        env.snapshot_at = specs.now()
        host = await self.db.get(RunnerHost, env.host_id)
        if host is None:
            return
        target = {
            "env_id": str(env.id),
            "container": env.container_name,
            "volume": env.volume_name,
            "network": env.network_name,
        }
        if then == "archive":
            self.enqueue(host, "archive_env", target, env=env)
        elif then == "delete":
            self.enqueue(host, "delete_env", target, env=env)

    async def _fail_pending_runs(self, env: RunnerEnvironment, reason: str) -> None:
        runs = await self.db.scalars(
            select(RunnerRun).where(
                RunnerRun.environment_id == env.id, RunnerRun.finished_at.is_(None)
            )
        )
        for run in runs:
            run.status = "failed"
            run.output = f"[工作台] {reason[:500]}"
            run.finished_at = specs.now()

    async def _finish_run(
        self,
        command: RunnerCommand,
        ok: bool,
        result: dict[str, Any],
        error: str | None,
        env: RunnerEnvironment | None,
    ) -> None:
        # 锁住这次运行：与工具调用里“等不到就改成结束后唤醒”互斥，避免漏掉唤醒
        run = await self.db.scalar(
            select(RunnerRun)
            .where(RunnerRun.id == command.run_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if run is None or run.finished_at is not None:
            return
        secrets = await self.secrets_for(env) if env is not None else []
        raw = base64.b64decode(result.get("output_b64", "") or b"")
        text = raw[: specs.OUTPUT_LIMIT].decode("utf-8", "replace")
        run.output = _redact(text, secrets)
        run.output_truncated = bool(result.get("truncated")) or len(raw) > specs.OUTPUT_LIMIT
        run.started_at = run.started_at or specs.now() - timedelta(
            seconds=float(result.get("seconds") or 0)
        )
        run.finished_at = specs.now()
        if not ok:
            run.status = "failed"
            run.output = (run.output + f"\n[工作台] {error or '执行失败'}").strip()[
                -specs.OUTPUT_LIMIT :
            ]
        elif result.get("timed_out"):
            run.status = "timeout"
            if env is not None and env.state == "running":
                env.state = "stopped"
                env.stopped_at = specs.now()
                env.state_reason = "运行超时后停止（单个命令无法单独终止）"
        else:
            code = result.get("exit_code")
            run.exit_code = code if isinstance(code, int) else None
            run.status = "succeeded" if run.exit_code == 0 else "failed"
        if env is not None:
            env.last_used_at = specs.now()
            if env.state == "stopped" and not result.get("timed_out"):
                env.state = "running"
        if run.wake and run.instance_id is not None:
            self.db.add(
                AgentWakeup(
                    id=uuid.uuid4(),
                    workspace_id=run.workspace_id,
                    instance_id=run.instance_id,
                    source="runner",
                    target=None,
                    session_key=f"runner:{run.id}",
                    prompt=(
                        f"[工作台] 运行 {run.id} 已结束：{run.status}"
                        + (f"，退出码 {run.exit_code}" if run.exit_code is not None else "")
                        + "。用 env_run 看输出。"
                    ),
                    run_at=specs.now(),
                )
            )
        # 一次性环境：运行结束就删（失败的先停止，保留一会儿方便查看）
        if env is not None:
            await self._after_run(env, run)

    async def _after_run(self, env: RunnerEnvironment, run: RunnerRun) -> None:
        condition = await self.db.scalar(
            select(RunnerDeleteCondition).where(
                RunnerDeleteCondition.environment_id == env.id,
                RunnerDeleteCondition.kind == "run_finished",
                RunnerDeleteCondition.fired_at.is_(None),
            )
        )
        if condition is None:
            return
        condition.fired_at = specs.now()
        if run.status == "succeeded":
            await self.mark_pending_delete(env, "运行结束")
            return
        host = await self.db.get(RunnerHost, env.host_id)
        policy = specs.policy_for(host.policy if host else None)
        await self.mark_pending_delete(env, f"运行{run.status}，保留一会儿方便查看")
        env.delete_after = specs.later(policy["failed_once_keep_minutes"] * 60)

    # ------------------------------------------------------------------ 关掉权限

    async def stop_all_for(self, instance: AgentInstance, reason: str) -> int:
        envs = await self.db.scalars(
            select(RunnerEnvironment).where(
                RunnerEnvironment.owner_instance_id == instance.id,
                RunnerEnvironment.state.in_(ACTIVE_STATES),
            )
        )
        count = 0
        viewer = Viewer(instance.workspace_id, instance=instance)
        for env in envs:
            await self.stop(viewer, env, reason)
            count += 1
        return count
