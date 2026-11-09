"""OpenClaw 智能体只读总览（T-0180，接口与迁移约定第 6 节）。

- 只读本地记录：不连 SSH、不调 OpenIM，不解析原始日志猜状态；消息在线（实例 status）、最近心跳
  （last_seen_at）、部署任务、容器运行证据（runtime_installed）分别表达；
- 所在服务器：实际部署（agent_deployments）优先，之后是手动指定 / 按指纹自动识别
  （instance.server_id）；都没有就是 null（未归属）；
- summary 按 server_id / q 统计全部实例（不套用 status 筛选、不受分页影响）：
  deploying 是最近一次任务是进行中的部署；attention 是最近一次部署失败 / 中断、
  或没停用但离线的实例（去重）；
- 一页的实例、凭证、部署记录、最近任务、服务器都是批量查询（不随行数增加查询次数）；
  分页沿用实例列表的偏移游标（app.domain.tasks.encode_cursor / decode_cursor）。
"""

import builtins
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_bundle import project_name
from app.core.config import Settings
from app.domain.agent_servers import AgentServerService, deployment_out
from app.domain.agent_ssh import _job_out
from app.domain.agents import AgentService
from app.domain.policy import WorkspaceAccess
from app.domain.tasks import decode_cursor, encode_cursor
from app.models import AgentDeployment, AgentInstance, AgentSshJob
from app.schemas.agents import (
    AgentOverviewOut,
    AgentOverviewRow,
    AgentOverviewSummary,
    InstanceStatus,
)


def _like(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _latest_jobs(workspace_id: uuid.UUID) -> Any:
    """这个工作空间里每个实例最近一次 SSH 任务（部署或卸载）。"""
    return (
        select(AgentSshJob.instance_id, AgentSshJob.kind, AgentSshJob.status)
        .where(AgentSshJob.workspace_id == workspace_id, AgentSshJob.instance_id.is_not(None))
        .ext(distinct_on(AgentSshJob.instance_id))
        .order_by(AgentSshJob.instance_id, AgentSshJob.started_at.desc())
        .subquery("latest_job")
    )


class AgentOverviewService:
    def __init__(self, db: AsyncSession, settings: Settings) -> None:
        self.db = db
        self.settings = settings
        self.agents = AgentService(db, settings)
        self.servers = AgentServerService(db, settings)

    def _scoped(
        self,
        query: Any,
        access: WorkspaceAccess,
        server_id: uuid.UUID | None,
        q: str | None,
    ) -> Any:
        query = query.outerjoin(
            AgentDeployment, AgentDeployment.instance_id == AgentInstance.id
        ).where(AgentInstance.workspace_id == access.workspace.id)
        if server_id is not None:
            # 实际部署优先，之后是手动指定 / 自动识别的归属
            query = query.where(
                func.coalesce(AgentDeployment.server_id, AgentInstance.server_id) == server_id
            )
        if q:
            query = query.where(AgentInstance.name.ilike(_like(q), escape="\\"))
        return query

    async def _summary(
        self, access: WorkspaceAccess, server_id: uuid.UUID | None, q: str | None
    ) -> AgentOverviewSummary:
        latest = _latest_jobs(access.workspace.id)
        deploying = (latest.c.kind == "install") & (latest.c.status == "running")
        failed = (latest.c.kind == "install") & latest.c.status.in_(("failed", "interrupted"))
        status = AgentInstance.status
        query = self._scoped(
            select(
                func.count(),
                func.count().filter(status == "online"),
                func.count().filter(status == "offline"),
                func.count().filter(status == "pending"),
                func.count().filter(status == "disabled"),
                func.count().filter(deploying),
                func.count().filter(failed | (status == "offline")),
            ).select_from(AgentInstance),
            access,
            server_id,
            q,
        ).outerjoin(latest, latest.c.instance_id == AgentInstance.id)
        row = (await self.db.execute(query)).one()
        return AgentOverviewSummary(
            total=int(row[0]),
            online=int(row[1]),
            offline=int(row[2]),
            pending=int(row[3]),
            disabled=int(row[4]),
            deploying=int(row[5]),
            attention=int(row[6]),
        )

    async def list(
        self,
        access: WorkspaceAccess,
        *,
        server_id: uuid.UUID | None,
        q: str | None,
        status: InstanceStatus | None,
        cursor: str | None,
        limit: int,
    ) -> AgentOverviewOut:
        now = datetime.now(UTC)
        offset = decode_cursor(cursor)
        query = self._scoped(select(AgentInstance), access, server_id, q)
        if status is not None:
            query = query.where(AgentInstance.status == status)
        instances: builtins.list[AgentInstance] = builtins.list(
            (
                await self.db.scalars(
                    query.order_by(AgentInstance.created_at, AgentInstance.id)
                    .offset(offset)
                    .limit(limit + 1)
                )
            ).all()
        )
        more = len(instances) > limit
        instances = instances[:limit]
        summary = await self._summary(access, server_id, q)
        items = await self._rows(instances, now)
        return AgentOverviewOut(
            summary=summary,
            items=items,
            next_cursor=encode_cursor(offset + limit) if more else None,
            generated_at=now,
        )

    async def _rows(
        self, instances: Sequence[AgentInstance], now: datetime
    ) -> builtins.list[AgentOverviewRow]:
        if not instances:
            return []
        ids = [i.id for i in instances]
        outs = await self.agents.instances_out(instances)
        deployments = {
            d.instance_id: d
            for d in (
                await self.db.scalars(
                    select(AgentDeployment).where(AgentDeployment.instance_id.in_(ids))
                )
            ).all()
        }
        jobs = {
            job.instance_id: job
            for job in (
                await self.db.scalars(
                    select(AgentSshJob)
                    .where(AgentSshJob.instance_id.in_(ids))
                    .ext(distinct_on(AgentSshJob.instance_id))
                    .order_by(AgentSshJob.instance_id, AgentSshJob.started_at.desc())
                )
            ).all()
        }
        server_of = {
            i.id: deployments[i.id].server_id if i.id in deployments else i.server_id
            for i in instances
        }
        servers = await self.servers.servers_out(
            [sid for sid in dict.fromkeys(server_of.values()) if sid is not None], now
        )
        rows = []
        for instance, out in zip(instances, outs, strict=True):
            deployment = deployments.get(instance.id)
            job = jobs.get(instance.id)
            sid = server_of[instance.id]
            rows.append(
                AgentOverviewRow(
                    instance=out,
                    server=servers.get(sid) if sid is not None else None,
                    deployment=deployment_out(deployment) if deployment else None,
                    latest_job=_job_out(job) if job else None,
                    deployment_project=project_name(str(instance.id)) if deployment else None,
                )
            )
        return rows
