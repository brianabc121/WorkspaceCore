"""运行服务器的管理员功能（T-0136 ~ T-0138；页面在 T-0140，docs/v2/RUNNER_PLAN.md 第 11 节）。

只有 owner / admin（路由层 workspace_admin）；所有查询都按工作空间过滤。
- 运行服务器：添加、下载接入包（换新凭证）、移除、回收策略；
- 总览、环境列表与操作（启动、停止、删除、保护）、模板（新建、修改、停用、重新构建）、运行记录；
- 实例卡片上的“运行环境权限”：开关、限额；关掉时停止它所有运行中的环境。
"""

import base64
import binascii
import hashlib
import json
import math
import uuid
from datetime import datetime
from typing import Any, Literal
from urllib.parse import urlsplit

from redis.asyncio import Redis
from sqlalchemy import Text, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import runner_bundle
from app.core.config import Settings
from app.core.errors import ApiError
from app.domain import notifications, runner_metrics, runner_reclaim
from app.domain import runner_specs as specs
from app.domain.audit import AuditTrail
from app.domain.policy import WorkspaceAccess, not_found
from app.domain.runner import Runner, RunnerError, Viewer, spec_out
from app.domain.runner_capabilities import ContainerApprovalRequired
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
        "RUNNER_HOST_AMBIGUOUS": 409,
        "PLACEMENT_CONFLICT": 409,
        "NO_ELIGIBLE_HOST": 409,
        "NO_ELIGIBLE_SERVER": 409,
        "TARGET_UNAVAILABLE": 409,
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
    async def _host(self, access: WorkspaceAccess, host_id: uuid.UUID | None = None) -> RunnerHost:
        try:
            host = await self.runner.host(access.workspace.id, host_id)
        except RunnerError as error:
            raise _error(error) from None
        if host is None:
            raise ApiError(404, "NOT_FOUND", "还没有添加容器服务器")
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

    async def bundle(
        self, access: WorkspaceAccess, host_id: uuid.UUID | None = None
    ) -> tuple[str, bytes]:
        """接入包：每次下载都换新凭证（旧的立即失效）。"""
        host = await self._host(access, host_id)
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
            select(AgentSshTarget.host).where(AgentSshTarget.workspace_id == workspace_id)
        )
        names.extend(str(t) for t in targets if t)
        return sorted(set(names))

    async def remove_host(
        self, access: WorkspaceAccess, version: int, host_id: uuid.UUID | None = None
    ) -> None:
        host = await self._host(access, host_id)
        await self.hosts.remove(host, access.user_id, version)
        await self.db.commit()

    async def set_policy(
        self, access: WorkspaceAccess, body: RunnerPolicyPut, host_id: uuid.UUID | None = None
    ) -> RunnerHostOut:
        host = await self._host(access, host_id)
        await self.hosts.set_policy(host, body.policy, body.version, access.user_id)
        await self.db.commit()
        await self.db.refresh(host)
        return host_out(host)

    # ---------------------------------------------------------------- 总览
    async def overview(self, access: WorkspaceAccess) -> RunnerOverview:
        workspace_id = access.workspace.id
        hosts = await self.runner.hosts(workspace_id)
        host = hosts[0] if len(hosts) == 1 else None
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
            hosts=[host_out(item) for item in hosts],
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
        *,
        host: RunnerHost | None = None,
        now: datetime | None = None,
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
            metrics=runner_metrics.environment_metrics(env, host, now=now or specs.now()),
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
        hosts = {host.id: host for host in await self.runner.hosts(access.workspace.id)}
        ranked = {}
        for host_id, host in hosts.items():
            live = [e for e in envs if e.host_id == host_id and e.state in ("running", "stopped")]
            if live:
                ranked.update(
                    await runner_reclaim.scores(self.db, live, specs.policy_for(host.policy))
                )
        now = specs.now()
        return [
            await self._env_admin_out(
                e,
                viewer,
                names,
                templates,
                conditions,
                specs.policy_for(hosts[e.host_id].policy if e.host_id in hosts else None),
                ranked.get(e.id),
                host=hosts.get(e.host_id),
                now=now,
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
        q: str | None = None,
        sort: Literal["last_used", "cpu", "memory"] = "last_used",
        cursor: str | None = None,
        limit: int = 500,
        host_id: uuid.UUID | None = None,
    ) -> RunnerEnvAdminList:
        """环境列表（T-0185 加服务端搜索、排序与分页）。

        只读本地记录，不连 Docker / SSH。total 是同样筛选条件（不含分页）下的全部匹配数；
        游标绑定工作空间、全部筛选、排序与每页条数，换了任何一项旧游标返回 422。
        按 CPU / 内存排序只看实时（与 metrics 的 ok 同一个判断）的读数，过期、未知、未运行排在最后；
        同分按 UUID 升序，翻页时不重不漏（读数在两次请求之间变化时不承诺冻结的快照）。
        """
        workspace_id = access.workspace.id
        where: list[Any] = [RunnerEnvironment.workspace_id == workspace_id]
        if host_id is not None:
            where.append(RunnerEnvironment.host_id == host_id)
        if state:
            where.append(RunnerEnvironment.state == state)
        elif not include_deleted:
            where.append(RunnerEnvironment.state != "deleted")
        if instance_id:
            where.append(RunnerEnvironment.owner_instance_id == instance_id)
        if lifetime:
            where.append(RunnerEnvironment.lifetime == lifetime)
        text = (q or "").strip()
        if text:
            pattern = f"%{_escape_like(text)}%"
            where.append(
                or_(
                    RunnerEnvironment.purpose.ilike(pattern, escape="\\"),
                    cast(RunnerEnvironment.id, Text).ilike(pattern, escape="\\"),
                )
            )
        key = _scope_key(
            workspace_id, state, instance_id, lifetime, include_deleted, text, sort, limit, host_id
        )
        offset = _decode_cursor(cursor, key)
        total = int(
            await self.db.scalar(select(func.count()).select_from(RunnerEnvironment).where(*where))
            or 0
        )
        hosts = await self.runner.hosts(workspace_id)
        now = specs.now()
        if sort == "cpu":
            keys = [runner_metrics.cpu_sort_key(host, now) for host in hosts]
            order = [func.coalesce(*keys, None).desc().nulls_last()]
        elif sort == "memory":
            keys = [runner_metrics.memory_sort_key(host, now) for host in hosts]
            order = [func.coalesce(*keys, None).desc().nulls_last()]
        else:
            order = [RunnerEnvironment.last_used_at.desc()]
        envs = list(
            await self.db.scalars(
                select(RunnerEnvironment)
                .where(*where)
                .order_by(*order, RunnerEnvironment.id.asc())
                .offset(offset)
                .limit(limit)
            )
        )
        end = offset + len(envs)
        return RunnerEnvAdminList(
            items=await self._outs(access, envs),
            total=total,
            next_cursor=_encode_cursor(end, key) if envs and end < total else None,
        )

    async def environment(
        self, access: WorkspaceAccess, environment_id: uuid.UUID
    ) -> RunnerEnvAdminOut:
        """一个环境的详情（T-0185）：不在当前列表筛选里也能按 ID 读；跨工作空间、不存在都是 404。"""
        env = await self.db.scalar(
            select(RunnerEnvironment).where(
                RunnerEnvironment.id == environment_id,
                RunnerEnvironment.workspace_id == access.workspace.id,
            )
        )
        if env is None:
            raise not_found()
        return (await self._outs(access, [env]))[0]

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
                if env.state not in ("stopped", "running"):
                    raise ApiError(409, "ENV_UNAVAILABLE", f"环境现在是 {env.state}，不能启动")
                await self.runner.start(env)
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
        except ContainerApprovalRequired as error:
            await self.db.commit()
            raise ApiError(
                409, error.code, error.message, details={"approval_id": str(error.approval_id)}
            ) from None
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
            host = await self.runner.online_host(access.workspace.id, template.build_host_id)
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
                    body="它可以在容器服务器上建、用、停、删自己的容器环境（例外八）",
                    link="/agents",
                    key=f"{instance.id}:{instance.version}",
                )
        await self.db.commit()
        await self.db.refresh(instance)
        return await self._runner_out(instance)


# ---------------------------------------------------------------- 环境列表的游标（T-0185）


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _bad_cursor() -> ApiError:
    return ApiError(
        422,
        "VALIDATION_FAILED",
        "请求参数校验失败",
        details={
            "errors": [{"loc": ["query", "cursor"], "msg": "游标无效", "type": "value_error"}]
        },
    )


def _scope_key(*parts: object) -> str:
    """游标绑定的范围：工作空间、全部筛选、排序与每页条数；换了任何一项旧游标都不能用。"""
    raw = "\x1f".join("" if part is None else str(part) for part in parts)
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def _encode_cursor(offset: int, key: str) -> str:
    raw = json.dumps({"o": offset, "k": key}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(cursor: str | None, key: str) -> int:
    if not cursor:
        return 0
    try:
        data = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
    except (ValueError, binascii.Error):
        raise _bad_cursor() from None
    if not isinstance(data, dict) or data.get("k") != key:
        raise _bad_cursor()
    offset = data.get("o")
    if not isinstance(offset, int) or isinstance(offset, bool) or not 0 <= offset <= 1_000_000:
        raise _bad_cursor()
    return offset
