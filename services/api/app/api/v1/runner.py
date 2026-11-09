"""运行服务器的管理员接口（T-0136 ~ T-0138；页面 T-0140，docs/v2/RUNNER_PLAN.md 第 11 节）。

全部只给 owner / admin；按当前工作空间过滤。实例卡片上的“运行环境权限”也在这里
（`/agents/instances/{id}/runner`，docs/AGENTS.md 第 8 节例外八）。
"""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response, Security, status
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    authenticated,
    client_ip,
    csrf_protected,
    get_app_settings,
    get_db,
    get_redis,
    workspace_admin,
    workspace_owner,
)
from app.core.config import Settings
from app.domain.policy import WorkspaceAccess
from app.domain.runner_admin import RunnerAdmin
from app.domain.runner_ssh import RunnerSshService
from app.schemas.agents import (
    AgentSshConfirm,
    AgentSshInstall,
    AgentSshRevoke,
    AgentSshState,
    AgentSshTargetSet,
)
from app.schemas.common import error_responses
from app.schemas.runner import (
    AgentRunnerOut,
    AgentRunnerPut,
    RunnerEnvAction,
    RunnerEnvAdminList,
    RunnerEnvAdminOut,
    RunnerHostCreate,
    RunnerHostOut,
    RunnerOverview,
    RunnerPolicyPut,
    RunnerRunAdminOut,
    RunnerRunList,
    RunnerSshUninstall,
    RunnerTemplateAdminOut,
    RunnerTemplateAdminSave,
    RunnerTemplateList,
    RunnerTemplateStatus,
)

router = APIRouter(tags=["runner"], dependencies=[Security(authenticated)])

Admin = Annotated[WorkspaceAccess, Depends(workspace_admin)]
Owner = Annotated[WorkspaceAccess, Depends(workspace_owner)]
_write = [Depends(csrf_protected)]
_read_errors = error_responses(401, 403, 404, 422)
_write_errors = error_responses(401, 403, 404, 409, 422)
_ZIP = {
    "content": {"application/zip": {"schema": {"type": "string", "format": "binary"}}},
    "description": "运行服务器接入包：runner.env（凭证，只出现这一次）、compose.yaml、"
    "runner-agent 代码、安装脚本（Docker 29.9.1、gVisor 20261019.0）、"
    "CA 与（外网）客户端证书（Cache-Control: no-store）",
}


def get_runner_admin(
    db: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_app_settings)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> RunnerAdmin:
    return RunnerAdmin(db, settings, redis)


Service = Annotated[RunnerAdmin, Depends(get_runner_admin)]


def get_runner_ssh(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_app_settings)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> RunnerSshService:
    # 部署任务与 OpenClaw 的 SSH 自动部署共用 API 进程里的任务管理（lifespan 里创建）
    return RunnerSshService(db, settings, redis, getattr(request.app.state, "ssh_jobs", None))


SshService = Annotated[RunnerSshService, Depends(get_runner_ssh)]


def _ip(request: Request) -> str:
    return client_ip(request, request.app.state.settings)


@router.get("/runner/overview", summary="运行服务器总览（admin）", responses=_read_errors)
async def runner_overview(access: Admin, service: Service) -> RunnerOverview:
    return await service.overview(access)


# ---------------------------------------------------------------- 运行服务器


@router.post(
    "/runner/host",
    summary="添加运行服务器（admin，V1 每个工作空间一台）",
    description="只建记录；凭证在下载接入包（或 SSH 自动部署）时签发。"
    "已经有一台时返回 409 RUNNER_EXISTS。",
    status_code=status.HTTP_201_CREATED,
    dependencies=_write,
    responses=_write_errors,
)
async def create_runner_host(
    access: Admin, service: Service, body: RunnerHostCreate
) -> RunnerHostOut:
    return await service.create_host(access, body)


@router.post(
    "/runner/host/bundle",
    summary="下载运行服务器接入包并签发新凭证（admin）",
    description="每次调用都签发新凭证（外网入口另签发客户端证书），旧凭证立即失效。",
    dependencies=_write,
    responses={200: _ZIP, **_write_errors},
    response_model=None,
)
async def runner_host_bundle(access: Admin, service: Service) -> Response:
    filename, data = await service.bundle(access)
    return Response(
        content=data,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@router.delete(
    "/runner/host",
    summary="移除运行服务器（admin，乐观并发）",
    description="还有没删除的环境时返回 409 RUNNER_HAS_ENVIRONMENTS。运行服务器上的容器不受影响。",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=_write,
    responses=_write_errors,
)
async def delete_runner_host(
    access: Admin, service: Service, version: Annotated[int, Query(ge=1)]
) -> Response:
    await service.remove_host(access, version)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put(
    "/runner/host/policy",
    summary="设置回收策略（admin，乐观并发）",
    description="覆盖默认的回收时长与水位（docs/v2/RUNNER_PLAN.md 第 6 节）；不认识的键返回 422。",
    dependencies=_write,
    responses=_write_errors,
)
async def put_runner_policy(
    access: Admin, service: Service, body: RunnerPolicyPut
) -> RunnerHostOut:
    return await service.set_policy(access, body)


# ---------------------------------------------------------------- 环境


@router.get("/runner/environments", summary="环境列表（admin）", responses=_read_errors)
async def list_runner_environments(
    access: Admin,
    service: Service,
    state: Literal[
        "creating", "running", "stopped", "archived", "pending_delete", "deleted", "failed"
    ]
    | None = None,
    instance_id: UUID | None = None,
    lifetime: Literal["once", "session", "persistent"] | None = None,
    include_deleted: bool = False,
    q: Annotated[
        str | None,
        Query(
            min_length=1,
            max_length=100,
            description="按用途或环境 ID 包含查询（% 与 _ 按字面匹配）",
        ),
    ] = None,
    sort: Annotated[
        Literal["last_used", "cpu", "memory"],
        Query(
            description="last_used：最近使用在前；cpu / memory：实时读数高的在前，"
            "过期、未知、未运行的排在最后；同分按 ID"
        ),
    ] = "last_used",
    cursor: Annotated[str | None, Query(max_length=512, description="上一页的 next_cursor")] = None,
    limit: Annotated[
        int, Query(ge=1, le=500, description="每页条数；缺省 500 与旧页面兼容，新页面传 50")
    ] = 500,
) -> RunnerEnvAdminList:
    return await service.environments(
        access,
        state=state,
        instance_id=instance_id,
        lifetime=lifetime,
        include_deleted=include_deleted,
        q=q,
        sort=sort,
        cursor=cursor,
        limit=limit,
    )


@router.get(
    "/runner/environments/{environment_id}",
    summary="一个环境的详情（admin）",
    description="不在列表筛选里也能按 ID 读；只读本地记录（含最新资源读数与新鲜度），"
    "不连 Docker / SSH。其他工作空间的环境与不存在的环境都返回 404。",
    responses=_read_errors,
)
async def get_runner_environment(
    access: Admin, service: Service, environment_id: UUID
) -> RunnerEnvAdminOut:
    return await service.environment(access, environment_id)


@router.post(
    "/runner/environments/{environment_id}/actions",
    summary="操作环境（admin，乐观并发）",
    description="start / stop / archive（常驻先做快照）/ delete（立即进入待删除，下一轮回收时删除；"
    "常驻先做快照）/ keep（撤销待删除）/ restore（归档的重建、已删除的常驻从快照恢复）/ "
    "pin / unpin（保护：不被自动回收）。",
    dependencies=_write,
    responses=_write_errors,
)
async def runner_environment_action(
    access: Admin, service: Service, environment_id: UUID, body: RunnerEnvAction
) -> RunnerEnvAdminOut:
    return await service.env_action(access, environment_id, body)


# ---------------------------------------------------------------- 模板


@router.get("/runner/templates", summary="模板列表（admin）", responses=_read_errors)
async def list_runner_templates(access: Admin, service: Service) -> RunnerTemplateList:
    return await service.templates(access)


@router.put(
    "/runner/templates",
    summary="新建或修改模板（admin）",
    description="按名称保存；规格变了版本号加一并在运行服务器上重新构建。"
    "修改时带 version（乐观并发）。",
    dependencies=_write,
    responses=_write_errors,
)
async def save_runner_template(
    access: Admin, service: Service, body: RunnerTemplateAdminSave
) -> RunnerTemplateAdminOut:
    return await service.save_template(access, body)


@router.put(
    "/runner/templates/{template_id}/status",
    summary="停用或启用模板（admin，乐观并发）",
    dependencies=_write,
    responses=_write_errors,
)
async def put_runner_template_status(
    access: Admin, service: Service, template_id: UUID, body: RunnerTemplateStatus
) -> RunnerTemplateAdminOut:
    return await service.set_template_status(access, template_id, body)


@router.post(
    "/runner/templates/{template_id}/rebuild",
    summary="重新构建模板（admin）",
    dependencies=_write,
    responses=_write_errors,
)
async def rebuild_runner_template(
    access: Admin, service: Service, template_id: UUID
) -> RunnerTemplateAdminOut:
    return await service.rebuild_template(access, template_id)


# ---------------------------------------------------------------- 运行记录


@router.get("/runner/runs", summary="运行记录（admin）", responses=_read_errors)
async def list_runner_runs(
    access: Admin,
    service: Service,
    environment_id: UUID | None = None,
    instance_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> RunnerRunList:
    return await service.runs(
        access, environment_id=environment_id, instance_id=instance_id, limit=limit
    )


@router.get(
    "/runner/runs/{run_id}",
    summary="一次运行的完整输出（admin，最多 1 MB，已脱敏）",
    responses=_read_errors,
)
async def get_runner_run(access: Admin, service: Service, run_id: UUID) -> RunnerRunAdminOut:
    return await service.run(access, run_id)


# ---------------------------------------------------------------- 实例卡片


@router.get(
    "/agents/instances/{instance_id}/runner",
    summary="实例的运行环境权限（admin）",
    responses=_read_errors,
)
async def get_agent_runner(access: Admin, service: Service, instance_id: UUID) -> AgentRunnerOut:
    return await service.get_instance_runner(access, instance_id)


@router.put(
    "/agents/instances/{instance_id}/runner",
    summary="打开或关闭实例的运行环境权限（admin，乐观并发）",
    description="打开后这个实例能用 runner_* / env_* 在运行服务器上建、用、停、删自己的容器环境"
    "（直接执行，docs/AGENTS.md 第 8 节例外八；默认关闭）。limits 覆盖默认限额"
    "（envs、persistent、memory_mb、cpus、disk_gb、execs_per_hour、run_hours_per_day）。"
    "每次都写审计；打开时通知管理员；关掉时停止它所有运行中的环境。",
    dependencies=_write,
    responses=_write_errors,
)
async def put_agent_runner(
    access: Admin, request: Request, service: Service, instance_id: UUID, body: AgentRunnerPut
) -> AgentRunnerOut:
    return await service.set_instance_runner(
        access, instance_id, body, client_ip=client_ip(request, request.app.state.settings)
    )


# ---------------------------------------------------------------- SSH 自动部署（T-0136）

_ssh_errors = error_responses(401, 403, 404, 409, 422, 429, 503)


@router.get(
    "/runner/host/ssh", summary="运行服务器的 SSH 自动部署（owner）", responses=_read_errors
)
async def get_runner_ssh_state(access: Owner, service: SshService) -> AgentSshState:
    return await service.state(access)


@router.put(
    "/runner/host/ssh",
    summary="填写或更换运行服务器的 SSH 地址，读取主机公钥（owner）",
    description="与 OpenClaw 实例的 SSH 自动部署相同：只读取主机公钥、不登录，返回 SHA256 指纹；"
    "owner 在服务器上核对后调用 /ssh/confirm。每个用户每分钟最多探测 10 次。",
    dependencies=_write,
    responses=_ssh_errors,
)
async def set_runner_ssh(
    access: Owner, request: Request, service: SshService, body: AgentSshTargetSet
) -> AgentSshState:
    return await service.set_target(access, body, client_ip=_ip(request))


@router.post(
    "/runner/host/ssh/confirm",
    summary="确认主机公钥指纹（owner）",
    dependencies=_write,
    responses=_write_errors,
)
async def confirm_runner_ssh(
    access: Owner, request: Request, service: SshService, body: AgentSshConfirm
) -> AgentSshState:
    return await service.confirm(access, body, client_ip=_ip(request))


@router.post(
    "/runner/host/ssh/install",
    summary="部署或重新部署运行服务器（owner）",
    description="上传接入包到 ~/myworkspace-runner（签发新凭证，旧的立即失效），"
    "以 root 运行 install.sh（Docker 29.9.1、gVisor release-20261019.0、runner-agent），"
    "等 runner-agent 连上工作台。"
    "不是 root 账号时用 sudo，要填登录密码（只用这一次，不保存）。返回 202，进度看 GET /ssh。",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=_write,
    responses=_ssh_errors,
)
async def install_runner_ssh(
    access: Owner, request: Request, service: SshService, body: AgentSshInstall
) -> AgentSshState:
    return await service.install(access, body, client_ip=_ip(request))


@router.post(
    "/runner/host/ssh/uninstall",
    summary="卸载运行服务器上的 runner-agent（owner）",
    description="停止 runner-agent，可选删除接入包目录；"
    "还有没删除的环境时 409 RUNNER_HAS_ENVIRONMENTS。Docker 与 gVisor 保留。返回 202。",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=_write,
    responses=_ssh_errors,
)
async def uninstall_runner_ssh(
    access: Owner, request: Request, service: SshService, body: RunnerSshUninstall
) -> AgentSshState:
    return await service.uninstall(access, body, client_ip=_ip(request))


@router.post(
    "/runner/host/ssh/revoke",
    summary="吊销工作台的专用密钥（owner）",
    dependencies=_write,
    responses=_ssh_errors,
)
async def revoke_runner_ssh(
    access: Owner, request: Request, service: SshService, body: AgentSshRevoke
) -> AgentSshState:
    return await service.revoke(access, body, client_ip=_ip(request))


@router.delete(
    "/runner/host/ssh",
    summary="删除运行服务器的 SSH 地址（owner）",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=_write,
    responses=_write_errors,
)
async def delete_runner_ssh(
    access: Owner,
    request: Request,
    service: SshService,
    version: Annotated[int, Query(ge=1)],
) -> Response:
    await service.remove(access, version=version, client_ip=_ip(request))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
