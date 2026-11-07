"""运行服务器的管理员功能（T-0136 ~ T-0138；页面在 T-0140，docs/v2/RUNNER_PLAN.md 第 11 节）。

只有 owner / admin（路由层 workspace_admin）；所有查询都按工作空间过滤。
- 运行服务器：添加、下载接入包（换新凭证）、移除、回收策略；
- 总览、环境列表与操作（启动、停止、删除、保护）、模板（新建、修改、停用、重新构建）、运行记录；
- 实例卡片上的“运行环境权限”：开关、限额；关掉时停止它所有运行中的环境。
"""

import math
import uuid
from typing import Any
from urllib.parse import urlsplit

from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import runner_bundle
from app.core.config import Settings
from app.core.errors import ApiError
from app.domain import notifications, runner_reclaim
from app.domain import runner_specs as specs
from app.domain.audit import AuditTrail
from app.domain.policy import WorkspaceAccess, not_found
from app.domain.runner import Runner, RunnerError, Viewer, spec_out
from app.domain.runner_hosts import RunnerHosts, host_out
from app.models import (
    AgentInstance,
    AgentSecretGrant,
    AgentSshTarget,
    RunnerEnvironment,
    RunnerHost,
    RunnerRun,
    RunnerTemplate,
)
from app.schemas.runner import (
    AgentRunnerOut,
    AgentRunnerPut,
    RunnerEnvAction,
    RunnerEnvAdminList,
    RunnerEnvAdminOut,
    RunnerHostCreate,
    RunnerHostOut,
    RunnerInstanceUsage,
    RunnerOverview,
    RunnerPolicyPut,
    RunnerRunAdminOut,
    RunnerRunList,
    RunnerTemplateAdminOut,
    RunnerTemplateAdminSave,
    RunnerTemplateList,
    RunnerTemplateStatus,
)


def _error(error: RunnerError) -> ApiError:
    status = {
        "NOT_FOUND": 404,
        "VERSION_CONFLICT": 409,
        "FORBIDDEN": 403,
        "RUNNER_UNAVAILABLE": 409,
        "TEMPLATE_NOT_READY": 409,
    }.get(error.code, 422)
    return ApiError(status, error.code, error.message)


class RunnerAdmin:
    def __init__(self, db: AsyncSession, settings: Settings, redis: Redis | None = None) -> None:
        self.db = db
        self.settings = settings
        self.redis = redis
        self.audit = AuditTrail(db, settings)
        self.runner = Runner(db, settings, redis)
        self.hosts = RunnerHosts(db, settings, redis)

    # ---------------------------------------------------------------- 运行服务器
    async def _host(self, access: WorkspaceAccess) -> RunnerHost:
        host = await self.runner.host(access.workspace.id)
        if host is None:
            raise ApiError(404, "NOT_FOUND", "还没有添加运行服务器")
        return host

    async def create_host(self, access: WorkspaceAccess, body: RunnerHostCreate) -> RunnerHostOut:
        configured = (
            self.settings.agent_public_url
            if body.ingress == "public"
            else self.settings.agent_intranet_url
        )
        if not configured:
            raise ApiError(
                409,
                "AGENT_INGRESS_NOT_CONFIGURED",
                "这个网络模式的接入入口还没有配置",
                details={"ingress": body.ingress},
            )
        host, _token = await self.hosts.create(access.workspace.id, body, access.user_id)
        await self.db.commit()
        await self.db.refresh(host)
        return host_out(host)

    async def bundle(self, access: WorkspaceAccess) -> tuple[str, bytes]:
        """接入包：每次下载都换新凭证（旧的立即失效）。"""
        host = await self._host(access)
        return await self.bundle_for(host, access.user_id)

    async def bundle_for(self, host: RunnerHost, actor_id: uuid.UUID | None) -> tuple[str, bytes]:
        """签发新凭证并生成接入包（页面下载与 SSH 自动部署共用）；提交事务。"""
        url = (
            self.settings.agent_public_url
            if host.ingress == "public"
            else self.settings.agent_intranet_url
        )
        if not url:
            raise ApiError(409, "AGENT_INGRESS_NOT_CONFIGURED", "接入入口还没有配置")
        token, cert = await self.hosts.reissue(host, actor_id)
        ca = cert[2] if cert else await self.hosts.server_ca()
        data = runner_bundle.build(
            runner_bundle.RunnerBundleInput(
                host_name=host.name,
                url=url.rstrip("/"),
                token=token,
                block=await self._block_list(host.workspace_id),
                ca_pem=ca,
                client_cert_pem=cert[0] if cert else None,
                client_key_pem=cert[1] if cert else None,
                # 外网入口没有域名时，接入包把入口主机名映射到工作台地址（与 OpenClaw 接入包一致）
                address=(self.settings.agent_public_address or None)
                if host.ingress == "public"
                else None,
            )
        )
        await self.db.commit()
        return f"myworkspace-runner-{host.id.hex[:8]}.zip", data

    async def _block_list(self, workspace_id: uuid.UUID) -> list[str]:
        """出口代理永远不放行的地址：工作台的入口、OpenClaw 服务器（私有网段本来就不放行）。"""
        names: list[str] = []
        for url in (
            self.settings.agent_intranet_url,
            self.settings.agent_public_url,
            self.settings.public_base_url,
        ):
            host = urlsplit(url).hostname if url else None
            if host:
                names.append(host)
        if self.settings.agent_public_address:
            names.append(self.settings.agent_public_address)
        targets = await self.db.scalars(
            select(AgentSshTarget.host)
            .join(AgentInstance, AgentInstance.id == AgentSshTarget.instance_id)
            .where(AgentInstance.workspace_id == workspace_id)
        )
        names.extend(str(t) for t in targets if t)
        return sorted(set(names))

    async def remove_host(self, access: WorkspaceAccess, version: int) -> None:
        host = await self._host(access)
        await self.hosts.remove(host, access.user_id, version)
        await self.db.commit()

    async def set_policy(self, access: WorkspaceAccess, body: RunnerPolicyPut) -> RunnerHostOut:
        host = await self._host(access)
        await self.hosts.set_policy(host, body.policy, body.version, access.user_id)
        await self.db.commit()
        await self.db.refresh(host)
        return host_out(host)

    # ---------------------------------------------------------------- 总览
    async def overview(self, access: WorkspaceAccess) -> RunnerOverview:
        workspace_id = access.workspace.id
        host = await self.runner.host(workspace_id)
        counts = {
            state: int(count)
            for state, count in await self.db.execute(
                select(RunnerEnvironment.state, func.count())
                .where(RunnerEnvironment.workspace_id == workspace_id)
                .group_by(RunnerEnvironment.state)
            )
        }
        instances = list(
            await self.db.scalars(
                select(AgentInstance)
                .where(AgentInstance.workspace_id == workspace_id)
                .order_by(AgentInstance.name)
            )
        )
        active = list(
            await self.db.execute(
                select(
                    RunnerEnvironment.owner_instance_id,
                    RunnerEnvironment.lifetime,
                    RunnerEnvironment.spec,
                ).where(
                    RunnerEnvironment.workspace_id == workspace_id,
                    RunnerEnvironment.state.in_(("creating", "running")),
                )
            )
        )
        usage: list[RunnerInstanceUsage] = []
        for instance in instances:
            mine = [row for row in active if row[0] == instance.id]
            if not instance.runner_enabled and not mine:
                continue
            usage.append(
                RunnerInstanceUsage(
                    instance_id=instance.id,
                    name=instance.name,
                    enabled=instance.runner_enabled,
                    running=len(mine),
                    persistent=sum(1 for row in mine if row[1] == "persistent"),
                    memory_mb=sum(int(row[2].get("memory_mb", 0)) for row in mine),
                    cpus=sum(float(row[2].get("cpus", 0)) for row in mine),
                    limits=specs.limits_for(instance.runner_limits),
                )
            )
        recent_rows = await self.db.scalars(
            select(RunnerEnvironment)
            .where(
                RunnerEnvironment.workspace_id == workspace_id,
                RunnerEnvironment.state.in_(("stopped", "pending_delete", "deleted", "failed")),
                RunnerEnvironment.state_reason.is_not(None),
            )
            .order_by(RunnerEnvironment.last_used_at.desc())
            .limit(20)
        )
        recent: list[dict[str, Any]] = [
            {
                "environment_id": str(env.id),
                "purpose": env.purpose,
                "state": env.state,
                "reason": env.state_reason,
                "at": (env.deleted_at or env.stopped_at or env.last_used_at).isoformat(),
            }
            for env in recent_rows
        ]
        return RunnerOverview(
            host=host_out(host) if host else None,
            environments=counts,
            instances=usage,
            recent=recent,
        )

    # ---------------------------------------------------------------- 环境
    async def _env_admin_out(
        self,
        env: RunnerEnvironment,
        viewer: Viewer,
        names: dict[uuid.UUID, str],
        templates: dict[uuid.UUID, str],
        conditions: dict[uuid.UUID, list[str]],
        policy: dict[str, float],
        score: float | None = None,
    ) -> RunnerEnvAdminOut:
        base = await self.runner.env_out(
            env, viewer, names=names, templates=templates, conditions=conditions, policy=policy
        )
        return RunnerEnvAdminOut(
            **base.model_dump(),
            owner_instance_id=env.owner_instance_id,
            template_id=env.template_id,
            spec=spec_out(env.spec),
            mem_bytes=env.mem_bytes,
            disk_bytes=env.disk_bytes,
            egress=dict(env.egress or {}),
            retention_score=None if score is None or math.isinf(score) else score,
            retention_infinite=score is not None and math.isinf(score),
            snapshot_at=env.snapshot_at,
            snapshot_bytes=env.snapshot_bytes,
            started_at=env.started_at,
            stopped_at=env.stopped_at,
            archived_at=env.archived_at,
            deleted_at=env.deleted_at,
            delete_after=env.delete_after,
            version=env.version,
        )

    async def _outs(
        self, access: WorkspaceAccess, envs: list[RunnerEnvironment]
    ) -> list[RunnerEnvAdminOut]:
        viewer = Viewer(access.workspace.id, user_id=access.user_id)
        names = await self.runner.owner_names(
            {e.owner_instance_id for e in envs if e.owner_instance_id}
        )
        template_ids = {e.template_id for e in envs if e.template_id}
        templates = {
            t.id: t.name
            for t in await self.db.scalars(
                select(RunnerTemplate).where(RunnerTemplate.id.in_(template_ids))
            )
        }
        conditions = await self.runner.conditions([e.id for e in envs])
        host = await self.runner.host(access.workspace.id)
        policy = specs.policy_for(host.policy if host else None)
        live = [e for e in envs if e.state in ("running", "stopped")]
        ranked = await runner_reclaim.scores(self.db, live, policy) if live else {}
        return [
            await self._env_admin_out(
                e, viewer, names, templates, conditions, policy, ranked.get(e.id)
            )
            for e in envs
        ]

    async def environments(
        self,
        access: WorkspaceAccess,
        *,
        state: str | None,
        instance_id: uuid.UUID | None,
        lifetime: str | None,
        include_deleted: bool,
    ) -> RunnerEnvAdminList:
        stmt = select(RunnerEnvironment).where(
            RunnerEnvironment.workspace_id == access.workspace.id
        )
        if state:
            stmt = stmt.where(RunnerEnvironment.state == state)
        elif not include_deleted:
            stmt = stmt.where(RunnerEnvironment.state != "deleted")
        if instance_id:
            stmt = stmt.where(RunnerEnvironment.owner_instance_id == instance_id)
        if lifetime:
            stmt = stmt.where(RunnerEnvironment.lifetime == lifetime)
        envs = list(
            await self.db.scalars(stmt.order_by(RunnerEnvironment.last_used_at.desc()).limit(500))
        )
        return RunnerEnvAdminList(items=await self._outs(access, envs))

    async def env_action(
        self, access: WorkspaceAccess, env_id: uuid.UUID, body: RunnerEnvAction
    ) -> RunnerEnvAdminOut:
        env = await self.db.scalar(
            select(RunnerEnvironment).where(
                RunnerEnvironment.id == env_id,
                RunnerEnvironment.workspace_id == access.workspace.id,
            )
        )
        if env is None:
            raise not_found()
        if env.version != body.version:
            raise ApiError(409, "VERSION_CONFLICT", "环境已被修改，请刷新后重试")
        viewer = Viewer(access.workspace.id, user_id=access.user_id)
        reason = "管理员操作"
        try:
            if body.action == "stop":
                await self.runner.stop(viewer, env, "管理员停止")
            elif body.action == "start":
                host = await self.runner.online_host(access.workspace.id)
                if env.state not in ("stopped", "running"):
                    raise ApiError(409, "ENV_UNAVAILABLE", f"环境现在是 {env.state}，不能启动")
                self.runner.enqueue(host, "start_env", self.runner.runtime_payload(env), env=env)
                env.last_used_at = specs.now()
            elif body.action == "delete":
                await self.runner.mark_pending_delete(env, "管理员删除")
                env.delete_after = specs.now()
            elif body.action == "archive":
                if not await self.runner.archive(env, "管理员归档"):
                    raise ApiError(
                        409, "ENV_UNAVAILABLE", f"环境现在是 {env.state}（或被保护），不能归档"
                    )
            elif body.action == "keep":
                await self.runner.keep(env, "管理员撤销删除")
            elif body.action == "restore":
                await self._restore(env)
            elif body.action in ("pin", "unpin"):
                env.pinned = body.action == "pin"
        except RunnerError as error:
            raise _error(error) from None
        env.version += 1
        await self.audit.record(
            f"runner.environment_{body.action}",
            workspace_ids=[access.workspace.id],
            actor_id=access.user_id,
            resource_type="runner_environment",
            resource_id=env.id,
            metadata={"purpose": env.purpose, "reason": reason},
        )
        await self.db.commit()
        await self.db.refresh(env)
        return (await self._outs(access, [env]))[0]

    async def _restore(self, env: RunnerEnvironment) -> None:
        """恢复：归档的按配置记录重建；已删除的常驻环境在快照保留期内也能恢复。"""
        if env.state == "archived":
            await self.runner.rebuild(env, "管理员恢复")
            return
        if env.state == "deleted" and env.lifetime == "persistent":
            if await self.runner.latest_snapshot(env) is None:
                raise ApiError(409, "SNAPSHOT_NOT_FOUND", "这个环境没有可用的快照了")
            env.deleted_at = None
            env.state = "archived"
            await self.runner.rebuild(env, "管理员从快照恢复")
            return
        raise ApiError(409, "ENV_UNAVAILABLE", f"环境现在是 {env.state}，不需要恢复")

    # ---------------------------------------------------------------- 模板
    async def _template_out(
        self,
        template: RunnerTemplate,
        viewer: Viewer,
        owners: dict[uuid.UUID, str] | None = None,
    ) -> RunnerTemplateAdminOut:
        base = await self.runner.template_out(template, viewer, owners)
        return RunnerTemplateAdminOut(
            **base.model_dump(), version=template.version, build_log=template.build_log or None
        )

    async def templates(self, access: WorkspaceAccess) -> RunnerTemplateList:
        viewer = Viewer(access.workspace.id, user_id=access.user_id)
        rows = await self.runner.templates(access.workspace.id)
        owners = await self.runner.owner_names(
            {t.owner_instance_id for t in rows if t.owner_instance_id}
        )
        return RunnerTemplateList(items=[await self._template_out(t, viewer, owners) for t in rows])

    async def save_template(
        self, access: WorkspaceAccess, body: RunnerTemplateAdminSave
    ) -> RunnerTemplateAdminOut:
        viewer = Viewer(access.workspace.id, user_id=access.user_id)
        try:
            template, _created = await self.runner.save_template(
                viewer,
                name=body.name,
                description=body.description,
                spec=body.spec.model_dump(),
                version=body.version,
            )
        except RunnerError as error:
            raise _error(error) from None
        await self.db.commit()
        await self.db.refresh(template)
        return await self._template_out(template, viewer)

    async def _template(self, access: WorkspaceAccess, template_id: uuid.UUID) -> RunnerTemplate:
        template = await self.db.scalar(
            select(RunnerTemplate).where(
                RunnerTemplate.id == template_id,
                RunnerTemplate.workspace_id == access.workspace.id,
            )
        )
        if template is None:
            raise not_found()
        return template

    async def set_template_status(
        self, access: WorkspaceAccess, template_id: uuid.UUID, body: RunnerTemplateStatus
    ) -> RunnerTemplateAdminOut:
        template = await self._template(access, template_id)
        if template.version != body.version:
            raise ApiError(409, "VERSION_CONFLICT", "模板已被修改，请刷新后重试")
        template.status = body.status
        template.version += 1
        await self.audit.record(
            "runner.template_status",
            workspace_ids=[access.workspace.id],
            actor_id=access.user_id,
            resource_type="runner_template",
            resource_id=template.id,
            metadata={"name": template.name, "status": body.status},
        )
        await self.db.commit()
        await self.db.refresh(template)
        return await self._template_out(
            template, Viewer(access.workspace.id, user_id=access.user_id)
        )

    async def rebuild_template(
        self, access: WorkspaceAccess, template_id: uuid.UUID
    ) -> RunnerTemplateAdminOut:
        template = await self._template(access, template_id)
        try:
            host = await self.runner.online_host(access.workspace.id)
        except RunnerError as error:
            raise _error(error) from None
        template.build_status = "building"
        template.build_error = None
        template.version += 1
        self.runner.enqueue(
            host,
            "build_template",
            {
                "template_id": str(template.id),
                "revision": template.revision,
                "image": template.spec["image"],
                "steps": template.spec.get("steps", []),
                "tag": f"myworkspace/tpl-{specs.short(template.id)}:{template.revision}",
            },
            template=template,
        )
        await self.audit.record(
            "runner.template_rebuild",
            workspace_ids=[access.workspace.id],
            actor_id=access.user_id,
            resource_type="runner_template",
            resource_id=template.id,
            metadata={"name": template.name, "revision": template.revision},
        )
        await self.db.commit()
        await self.db.refresh(template)
        return await self._template_out(
            template, Viewer(access.workspace.id, user_id=access.user_id)
        )

    # ---------------------------------------------------------------- 运行记录
    def _run_admin_out(
        self, run: RunnerRun, names: dict[uuid.UUID, str], *, full: bool
    ) -> RunnerRunAdminOut:
        base = self.runner.run_out(run)
        data = base.model_dump()
        if full:
            data["untrusted"] = {"output": run.output}
        return RunnerRunAdminOut(
            **data,
            instance=names.get(run.instance_id) if run.instance_id else None,
            command=run.command,
            timeout_seconds=run.timeout_seconds,
        )

    async def runs(
        self,
        access: WorkspaceAccess,
        *,
        environment_id: uuid.UUID | None,
        instance_id: uuid.UUID | None,
        limit: int,
    ) -> RunnerRunList:
        stmt = select(RunnerRun).where(RunnerRun.workspace_id == access.workspace.id)
        if environment_id:
            stmt = stmt.where(RunnerRun.environment_id == environment_id)
        if instance_id:
            stmt = stmt.where(RunnerRun.instance_id == instance_id)
        rows = list(await self.db.scalars(stmt.order_by(RunnerRun.created_at.desc()).limit(limit)))
        names = await self.runner.owner_names({r.instance_id for r in rows if r.instance_id})
        return RunnerRunList(items=[self._run_admin_out(r, names, full=False) for r in rows])

    async def run(self, access: WorkspaceAccess, run_id: uuid.UUID) -> RunnerRunAdminOut:
        run = await self.db.scalar(
            select(RunnerRun).where(
                RunnerRun.id == run_id, RunnerRun.workspace_id == access.workspace.id
            )
        )
        if run is None:
            raise not_found()
        names = await self.runner.owner_names({run.instance_id} if run.instance_id else set())
        return self._run_admin_out(run, names, full=True)

    # ---------------------------------------------------------------- 实例开关
    async def _instance(self, access: WorkspaceAccess, instance_id: uuid.UUID) -> AgentInstance:
        instance = await self.db.scalar(
            select(AgentInstance).where(
                AgentInstance.id == instance_id,
                AgentInstance.workspace_id == access.workspace.id,
            )
        )
        if instance is None:
            raise not_found()
        return instance

    async def _runner_out(self, instance: AgentInstance) -> AgentRunnerOut:
        secrets = sorted(
            await self.db.scalars(
                select(AgentSecretGrant.ref_id).where(AgentSecretGrant.instance_id == instance.id)
            )
        )
        return AgentRunnerOut(
            enabled=instance.runner_enabled,
            limits=specs.limits_for(instance.runner_limits),
            secrets=list(secrets),
            enabled_at=instance.runner_at,
            version=instance.version,
        )

    async def get_instance_runner(
        self, access: WorkspaceAccess, instance_id: uuid.UUID
    ) -> AgentRunnerOut:
        return await self._runner_out(await self._instance(access, instance_id))

    async def set_instance_runner(
        self,
        access: WorkspaceAccess,
        instance_id: uuid.UUID,
        body: AgentRunnerPut,
        *,
        client_ip: str,
    ) -> AgentRunnerOut:
        """运行环境权限（例外八）：写审计；打开时通知管理员；关掉时停止它所有运行中的环境。"""
        instance = await self._instance(access, instance_id)
        if instance.version != body.version:
            raise ApiError(
                409,
                "VERSION_CONFLICT",
                "内容已被其他操作修改，请刷新后重试",
                details={"current_version": instance.version},
            )
        unknown = [k for k in body.limits if k not in specs.DEFAULT_LIMITS]
        if unknown:
            raise ApiError(422, "VALIDATION_FAILED", f"不认识的限额：{', '.join(unknown[:5])}")
        for key, value in body.limits.items():
            if value < 0 or value > 1_000_000:
                raise ApiError(422, "VALIDATION_FAILED", f"{key} 超出范围")
        if body.enabled and instance.scope != "read_write":
            raise ApiError(
                422, "VALIDATION_FAILED", "这个实例的权限范围是只读，打开运行环境权限也用不了"
            )
        limits = {k: float(v) for k, v in body.limits.items()}
        newly = body.enabled and not instance.runner_enabled
        disabled = not body.enabled and instance.runner_enabled
        changed = newly or disabled or dict(instance.runner_limits or {}) != limits
        stopped = 0
        if changed:
            instance.runner_enabled = body.enabled
            instance.runner_limits = limits
            if newly:
                instance.runner_by = access.user_id
                instance.runner_at = specs.now()
            elif disabled:
                instance.runner_by = None
                instance.runner_at = None
                stopped = await self.runner.stop_all_for(instance, "管理员关掉了运行环境权限")
            instance.version += 1
            await self.audit.record(
                "agents.instance.runner_updated",
                workspace_ids=[access.workspace.id],
                actor_id=access.user_id,
                resource_type="agent_instance",
                resource_id=instance.id,
                metadata={"enabled": body.enabled, "limits": limits, "stopped": stopped},
                client_ip=client_ip,
            )
            if newly:
                await notifications.notify_admins(
                    self.db,
                    access.workspace.id,
                    type_="runner.instance_enabled",
                    severity="warning",
                    title=f"智能体「{instance.name}」获得了运行环境权限",
                    body="它可以在运行服务器上建、用、停、删自己的容器环境（例外八）",
                    link="/agents",
                    key=f"{instance.id}:{instance.version}",
                )
        await self.db.commit()
        await self.db.refresh(instance)
        return await self._runner_out(instance)
