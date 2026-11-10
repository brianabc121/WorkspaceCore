"""中转站管理：模型同步与连接检测（owner）。T-0202（MC-2）。

约定：docs/v2/model-connections/docs/superpowers/specs/2026-11-09-model-connections-contract.md
第 2 节（POST /{id}/models/sync、POST /{id}/checks）与第 4、5 节。
两个接口都由工作台后端经受控出站客户端访问服务商（浏览器不直连服务商），
Key 只在服务端内存里使用，不出现在任何响应里；结果只有固定类别，不含服务商的原始响应。
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Security
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    authenticated,
    client_ip,
    csrf_protected,
    get_app_settings,
    get_connectors,
    get_db,
    get_redis,
    workspace_owner,
)
from app.core.config import Settings
from app.domain.connectors import ConnectorRuntime
from app.domain.model_catalog import ModelCatalogService
from app.domain.policy import WorkspaceAccess
from app.schemas.common import error_responses
from app.schemas.model_connections import (
    ConnectionCheckOut,
    ConnectionCheckRequest,
    ModelSyncRequest,
    ModelSyncResult,
)

router = APIRouter(
    prefix="/model-connections", tags=["model-connections"], dependencies=[Security(authenticated)]
)


def get_model_catalog_service(
    db: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_app_settings)],
    connectors: Annotated[ConnectorRuntime, Depends(get_connectors)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> ModelCatalogService:
    return ModelCatalogService(db, settings, connectors, redis)


Owner = Annotated[WorkspaceAccess, Depends(workspace_owner)]
Service = Annotated[ModelCatalogService, Depends(get_model_catalog_service)]
_write = [Depends(csrf_protected)]
_probe_errors = error_responses(401, 403, 404, 409, 422, 429, 503)

_LIMITS = (
    "同一连接同一时间只能有一个同步或检测（409 CHECK_IN_PROGRESS）；每个 owner 对每个连接"
    "每分钟最多 5 次同步与检测合计（429 RATE_LIMITED，带 Retry-After）。"
    "停用的连接也可以同步和检测（排查问题用）；"
    "没填协议或地址 409 MODEL_CONFIGURATION_INCOMPLETE，协议未核验开放 409 "
    "MODEL_PROTOCOL_UNSUPPORTED；version 不是当前版本 409 VERSION_CONFLICT。"
)


def _ip(request: Request) -> str:
    return client_ip(request, request.app.state.settings)


@router.post(
    "/{connection_id}/models/sync",
    summary="从服务商同步模型列表（owner）",
    description="向服务商请求一次模型列表（不翻页）。完整结果（不超过 1000 个、响应不超过 2 MiB）"
    "保存为快照：“服务商全部可用模型”范围下模型集合换成快照，但正被智能体使用的模型不会移除"
    "（kept_in_use 列出）；“指定模型”范围只更新快照，不改变选中的模型。"
    "不完整（partial）、失败（failed）或服务商不支持列模型（unsupported）时保留上一次的完整快照"
    "与模型集合（kept_previous=true），reason / retry_at 说明原因；不支持列模型时请用“指定模型”"
    "手工填写。列出的模型不代表能推理（不会标 verified）。同步期间连接配置变了（例如更换了 Key）"
    "时结果作废：sync_state=failed、reason=stale_revision。" + _LIMITS,
    dependencies=_write,
    responses=_probe_errors,
)
async def sync_model_connection_models(
    access: Owner,
    service: Service,
    request: Request,
    connection_id: UUID,
    body: ModelSyncRequest,
) -> ModelSyncResult:
    return await service.sync_models(access, connection_id, body, client_ip=_ip(request))


@router.post(
    "/{connection_id}/checks",
    summary="检测连接（owner）",
    description="metadata：请求服务商的模型列表接口，只证明 Key 与元数据接口可用，不代表任何模型"
    "能推理。inference：用固定的最短提示（ping、最多 16 个输出 token、不流式、不自动重试）"
    "对选定的模型发一次真实推理请求，可能产生少量服务商费用；model_id 必须在连接的模型集合里"
    "（否则 422 MODEL_NOT_ALLOWED），通过后该模型标为 verified。结果只有固定类别："
    "status = passed / failed / rate_limited / unsupported / unknown，reason 见 CheckReason；"
    "限流时 retry_at 取服务商的 Retry-After（没有合法值为 null）。检测期间连接配置变了"
    "（例如更换了 Key）时结果记为 unknown / stale_revision，不覆盖当前配置的 last_check。"
    "检测结果不改变连接的 version。" + _LIMITS,
    dependencies=_write,
    responses=_probe_errors,
)
async def check_model_connection(
    access: Owner,
    service: Service,
    request: Request,
    connection_id: UUID,
    body: ConnectionCheckRequest,
) -> ConnectionCheckOut:
    return await service.check_connection(access, connection_id, body, client_ip=_ip(request))
