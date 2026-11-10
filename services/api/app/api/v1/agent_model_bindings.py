"""OpenClaw 实例的主模型绑定与应用（owner）。T-0204 / T-0205。

约定：docs/v2/model-connections/docs/superpowers/specs/2026-11-09-model-connections-contract.md
第 3 节、docs/decisions/2026-11-09-model-connections.md。
绑定是目标；实例确认后才是已应用。实例侧接口（取配置、回报、取 Key）在 app/api/agent_api.py。
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Security, status
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    authenticated,
    client_ip,
    csrf_protected,
    get_app_settings,
    get_db,
    get_redis,
    workspace_owner,
)
from app.core.config import Settings
from app.domain.agent_model_bindings import AgentModelBindingService
from app.domain.policy import WorkspaceAccess
from app.schemas.common import error_responses
from app.schemas.model_connections import (
    AgentModelBindingOut,
    ModelApplyJobOut,
    ModelAssignmentPage,
    ModelBindingApply,
    ModelBindingPut,
    ModelBindingView,
    PickerConnectionPage,
)

router = APIRouter(tags=["model-connections"], dependencies=[Security(authenticated)])


def get_binding_service(
    db: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_app_settings)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> AgentModelBindingService:
    return AgentModelBindingService(db, settings, redis)


Owner = Annotated[WorkspaceAccess, Depends(workspace_owner)]
Service = Annotated[AgentModelBindingService, Depends(get_binding_service)]
_write = [Depends(csrf_protected)]
_read_errors = error_responses(401, 403, 404, 422)
_write_errors = error_responses(401, 403, 404, 409, 422)
PATH = "/agents/instances/{instance_id}/model-binding"


def _ip(request: Request) -> str:
    return client_ip(request, request.app.state.settings)


@router.get(
    "/agents/model-assignments",
    summary="OpenClaw 连接分配（owner）",
    description="每个实例一行：目标连接与模型、已应用的连接与模型、应用状态；"
    "没有原生绑定、仍按旧配置运行的实例 legacy 不为空（不猜连接与模型）。"
    "connection_id 只看以它为目标或已应用的实例。",
    responses=_read_errors,
)
async def list_model_assignments(
    access: Owner,
    service: Service,
    q: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
    connection_id: UUID | None = None,
    cursor: Annotated[str | None, Query(pattern=r"^\d{1,6}$")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> ModelAssignmentPage:
    return await service.assignments(
        access, q=q, connection_id=connection_id, offset=int(cursor or 0), limit=limit
    )


@router.get(
    "/agents/model-connection-options",
    summary="主模型连接的可选项（owner）",
    description="只列启用、配置完整、协议已开放的连接。带 instance_id 时标出是否已授权给这个实例"
    "（未授权的要先在中转站管理里授权）；新建实例不带，选择即授权给即将创建的实例。",
    responses=_read_errors,
)
async def list_model_connection_options(
    access: Owner, service: Service, instance_id: UUID | None = None
) -> PickerConnectionPage:
    return await service.picker(access, instance_id=instance_id)


@router.get(
    PATH,
    summary="实例的主模型绑定（owner）",
    description="binding 为 null 且 apply_state 为 legacy_active：实例仍按旧手工配置运行"
    "（legacy.legacy_refs 是旧授权的引用名）；两者都为 null：还没有配置模型。",
    responses=_read_errors,
)
async def get_model_binding(instance_id: UUID, access: Owner, service: Service) -> ModelBindingView:
    return await service.view(access, instance_id)


@router.put(
    PATH,
    summary="设置实例的主模型（owner）",
    description="第一次 version 传 null，之后必须带当前版本（409 VERSION_CONFLICT）。"
    "连接要启用（409 MODEL_CONNECTION_DISABLED）、配置完整（409 MODEL_CONNECTION_NOT_READY）、"
    "协议已开放（409 MODEL_PROTOCOL_UNSUPPORTED）、已授权给这个实例（409 MODEL_NOT_ALLOWED）、"
    "模型在连接的模型集合里（422 MODEL_NOT_ALLOWED）、Key 可读（409 CREDENTIAL_UNREADABLE）。"
    "保存后是 pending：实例应用并回报后才变成 applied；切换期间旧的已应用连接仍可取 Key。",
    dependencies=_write,
    responses=_write_errors,
)
async def put_model_binding(
    instance_id: UUID,
    request: Request,
    access: Owner,
    service: Service,
    body: ModelBindingPut,
) -> AgentModelBindingOut:
    return await service.put(access, instance_id, body, client_ip=_ip(request))


@router.delete(
    PATH,
    summary="解除实例的主模型绑定（owner）",
    description="目标置空、revoked=true：立即拒绝这个实例后续取模型 Key，并排任务移除受管配置。"
    "已在进程里的 Key 不保证立即停止工作；不删除连接或授权。",
    dependencies=_write,
    responses=_write_errors,
)
async def delete_model_binding(
    instance_id: UUID,
    request: Request,
    access: Owner,
    service: Service,
    version: Annotated[int, Query(ge=1)],
) -> AgentModelBindingOut:
    return await service.remove(access, instance_id, version=version, client_ip=_ip(request))


@router.post(
    f"{PATH}/apply",
    status_code=status.HTTP_202_ACCEPTED,
    summary="（重新）应用当前目标（owner）",
    description="同一修订号复用同一个任务；失败、未知、待手动的重新排队（attempt 加 1）。"
    "实例的 model-config.mjs 每分钟取一次配置并回报；手动接入的实例按接入包 CHECKLIST.md 操作。",
    dependencies=_write,
    responses=_write_errors,
)
async def apply_model_binding(
    instance_id: UUID,
    request: Request,
    access: Owner,
    service: Service,
    body: ModelBindingApply,
) -> ModelApplyJobOut:
    return await service.apply(access, instance_id, body, client_ip=_ip(request))


@router.get(
    f"{PATH}/jobs/{{job_id}}",
    summary="应用任务的状态（owner）",
    description="排队或执行中超过 10 分钟没有结果显示 unknown（不代表失败或成功）。"
    "不含远端原始输出或配置内容。",
    responses=_read_errors,
)
async def get_model_apply_job(
    instance_id: UUID, job_id: UUID, access: Owner, service: Service
) -> ModelApplyJobOut:
    return await service.job(access, instance_id, job_id)
