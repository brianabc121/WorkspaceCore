"""中转站管理：模型连接（owner）。T-0201 / T-0203。

约定：docs/v2/model-connections/docs/superpowers/specs/2026-11-09-model-connections-contract.md
第 2 节、docs/decisions/2026-11-09-model-connections.md。
API Key 只写不读：任何响应都不含明文、密文或内部凭据 ID。
固定路径（capabilities / summary / migration）写在 /{connection_id} 之前。
模型同步与检测的接口在 app/api/v1/model_catalog.py。
"""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response, Security, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    authenticated,
    client_ip,
    csrf_protected,
    get_app_settings,
    get_db,
    workspace_owner,
)
from app.core.config import Settings
from app.core.errors import ApiError
from app.domain.credentials import CredentialBox, CredentialError
from app.domain.model_connection_migration import migrate_workspace
from app.domain.model_connections import PAGE_DEFAULT, PAGE_MAX, ModelConnectionService
from app.domain.policy import WorkspaceAccess
from app.schemas.common import error_responses
from app.schemas.model_connections import (
    ConnectionActivityPage,
    ConnectionGrantPage,
    ConnectionModelPage,
    GrantsReplace,
    MigrationClassify,
    MigrationEntryOut,
    MigrationEntryPage,
    MigrationRun,
    MigrationRunOut,
    ModelConnectionCapabilities,
    ModelConnectionCreate,
    ModelConnectionKeyRotate,
    ModelConnectionOut,
    ModelConnectionPage,
    ModelConnectionSummary,
    ModelConnectionUpdate,
    ModelsReplace,
)

router = APIRouter(
    prefix="/model-connections", tags=["model-connections"], dependencies=[Security(authenticated)]
)


def get_model_connection_service(
    db: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> ModelConnectionService:
    return ModelConnectionService(db, settings)


Owner = Annotated[WorkspaceAccess, Depends(workspace_owner)]
Service = Annotated[ModelConnectionService, Depends(get_model_connection_service)]
Cursor = Annotated[str | None, Query(max_length=512, description="上一页返回的 next_cursor")]
Limit = Annotated[int, Query(ge=1, le=PAGE_MAX)]
OptionalIdempotencyKey = Annotated[
    UUID | None,
    Header(
        alias="Idempotency-Key",
        description="可选：同一个键的重复请求返回同一个连接（响应头 Idempotent-Replayed: true），"
        "不会再建凭据；换了请求内容 409 IDEMPOTENCY_KEY_REUSED",
    ),
]
_write = [Depends(csrf_protected)]
_read_errors = error_responses(401, 403, 404, 422)
_write_errors = error_responses(401, 403, 404, 409, 422, 503)


def _ip(request: Request) -> str:
    return client_ip(request, request.app.state.settings)


@router.get(
    "",
    summary="连接列表（owner）",
    description="服务端搜索（名称、URL、模型 ID，按字面、不区分大小写）与筛选，分页之前生效；"
    "total 是符合条件的全部连接数。归类为非模型凭据的导入项不在这里（见 migration/entries）。"
    "游标绑定筛选条件，换条件要从第一页重新加载（旧游标 422）。",
    responses=_read_errors,
)
async def list_model_connections(
    access: Owner,
    service: Service,
    q: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
    connection_type: Annotated[
        Literal["official", "relay"] | None,
        Query(alias="type", description="官方直连 / 第三方中转"),
    ] = None,
    configuration_state: Literal["draft", "ready", "needs_review"] | None = None,
    enabled: bool | None = None,
    check_status: Annotated[
        Literal["passed", "failed", "rate_limited", "unsupported", "unknown", "never"] | None,
        Query(description="最近一次（当前配置修订号的）检测结果；never 表示还没检测过"),
    ] = None,
    cursor: Cursor = None,
    limit: Limit = PAGE_DEFAULT,
) -> ModelConnectionPage:
    return await service.list_connections(
        access,
        q=q,
        connection_type=connection_type,
        configuration_state=configuration_state,
        enabled=enabled,
        check_status=check_status,
        cursor=cursor,
        limit=limit,
    )


@router.get(
    "/capabilities",
    summary="已开放的协议、官方预设与限制（owner）",
    description="只列经核验开放的协议；未核验的协议不出现，页面不显示。",
    responses=_read_errors,
)
async def get_model_connection_capabilities(
    access: Owner, service: Service
) -> ModelConnectionCapabilities:
    return service.capabilities()


@router.get(
    "/summary",
    summary="整个工作空间的连接统计（owner）",
    description="不受分页或筛选影响；check_passed 只计 ready + 启用、15 分钟内对当前配置修订号"
    "检测通过的连接（check_passed_inference 是其中推理检测通过的）。",
    responses=_read_errors,
)
async def get_model_connection_summary(access: Owner, service: Service) -> ModelConnectionSummary:
    return await service.summary(access)


@router.get(
    "/migration/entries",
    summary="旧密钥库导入项（owner）",
    description="待完善 / 待归类 / 已纳管 / 非模型凭据 / 需要复核（Key 不可读）。不含 Key。"
    "导入在页面上执行（POST migration/run），也可以用命令 app.commands.migrate_model_connections。",
    responses=_read_errors,
)
async def list_migration_entries(
    access: Owner,
    service: Service,
    state: Literal[
        "needs_configuration", "needs_classification", "managed", "non_model", "needs_review"
    ]
    | None = None,
    cursor: Cursor = None,
    limit: Limit = PAGE_DEFAULT,
) -> MigrationEntryPage:
    return await service.migration_entries(access, state=state, cursor=cursor, limit=limit)


@router.post(
    "/migration/run",
    summary="在页面上导入旧密钥库（owner）",
    description="与命令 app.commands.migrate_model_connections 相同：只处理当前工作空间；"
    "dry_run 只读预览，apply 按批导入（每批一个事务，可以重复执行，"
    "已导入的不重复、撤销过的授权不恢复），"
    "report 只读对账。不复制密文、不换 ID、不建模型或绑定、不访问外部服务；只返回计数与错误类别。"
    "密钥库没有配置加密密钥时 503。",
    dependencies=_write,
    responses=_write_errors,
)
async def run_model_connection_migration(
    access: Owner,
    db: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_app_settings)],
    body: MigrationRun,
) -> MigrationRunOut:
    try:
        box = CredentialBox.from_settings(settings)
    except CredentialError as error:
        raise ApiError(503, "EXTERNAL_UNAVAILABLE", "密钥库未配置加密密钥") from error
    report = await migrate_workspace(db, settings, box, access.workspace.id, mode=body.mode)
    return MigrationRunOut.model_validate(report.as_dict())


@router.post(
    "/migration/entries/{entry_id}/classify",
    summary="把导入项归类为模型连接或非模型凭据（owner）",
    description="保留同一个映射、同一条凭据与原授权，不新建、不恢复已撤销的访问、不改启停；"
    "有原生绑定或进行中的应用任务时不能改判为非模型（409 MODEL_BINDING_CONFLICT）。",
    dependencies=_write,
    responses=_write_errors,
)
async def classify_migration_entry(
    access: Owner, service: Service, request: Request, entry_id: UUID, body: MigrationClassify
) -> MigrationEntryOut:
    return await service.classify(access, entry_id, body, client_ip=_ip(request))


@router.get("/{connection_id}", summary="一个连接（owner）", responses=_read_errors)
async def get_model_connection(
    access: Owner, service: Service, connection_id: UUID
) -> ModelConnectionOut:
    return await service.get(access, connection_id)


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="新建连接（owner）",
    description="元数据与加密凭据在同一个事务里创建。save_as_draft=false 时必须配置完整（ready）"
    "并默认启用；草稿默认停用。URL 只接受 HTTPS 协议基地址（不含用户名密码、查询参数、片段），"
    "不接受自定义请求头。未开放的协议 422 MODEL_PROTOCOL_UNSUPPORTED；官方预设配第三方主机 "
    "422 MODEL_OFFICIAL_HOST_MISMATCH；同名 409 MODEL_CONNECTION_EXISTS。",
    dependencies=_write,
    responses=_write_errors,
)
async def create_model_connection(
    access: Owner,
    service: Service,
    request: Request,
    response: Response,
    body: ModelConnectionCreate,
    idempotency_key: OptionalIdempotencyKey = None,
) -> ModelConnectionOut:
    out, replayed = await service.create(
        access,
        body,
        idempotency_key=idempotency_key,
        path=request.url.path,
        client_ip=_ip(request),
    )
    if replayed:
        response.headers["Idempotent-Replayed"] = "true"
    return out


@router.patch(
    "/{connection_id}",
    summary="修改连接（owner）",
    description="省略的字段不变；不含 Key（用 rotate-key）。已配置完整、且有授权或绑定的连接"
    "改 URL / 协议返回 409 CONNECTION_ENDPOINT_IN_USE（复制为新连接再切换）；"
    "旧导入项第一次从空补齐例外。停用会影响实例时先 409 CONNECTION_IN_USE（details.impact），"
    "确认后带 confirm_impact=true 再提交。",
    dependencies=_write,
    responses=_write_errors,
)
async def update_model_connection(
    access: Owner,
    service: Service,
    request: Request,
    connection_id: UUID,
    body: ModelConnectionUpdate,
) -> ModelConnectionOut:
    return await service.update(access, connection_id, body, client_ip=_ip(request))


@router.post(
    "/{connection_id}/rotate-key",
    summary="更换 Key（owner）",
    description="版本加 1，旧值不再下发、不留密文；检测结果失效。不会去服务商创建或撤销 Key。"
    "不能与地址修改合在一次请求里。",
    dependencies=_write,
    responses=_write_errors,
)
async def rotate_model_connection_key(
    access: Owner,
    service: Service,
    request: Request,
    connection_id: UUID,
    body: ModelConnectionKeyRotate,
) -> ModelConnectionOut:
    return await service.rotate_key(access, connection_id, body, client_ip=_ip(request))


@router.delete(
    "/{connection_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="删除连接（owner）",
    description="有授权、绑定、进行中的应用任务，或旧配置 / 运行环境仍引用这条凭据时 409 "
    "CONNECTION_IN_USE（details 列出影响）；不会顺带删除或停掉智能体。",
    dependencies=_write,
    responses=_write_errors,
)
async def delete_model_connection(
    access: Owner,
    service: Service,
    request: Request,
    connection_id: UUID,
    version: Annotated[int, Query(ge=1)],
) -> Response:
    await service.delete(access, connection_id, version=version, client_ip=_ip(request))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/{connection_id}/models",
    summary="连接的模型集合（owner）",
    description="明确的模型 ID（原样大小写），独立分页；synced_at / sync_state 是最近一次同步。",
    responses=_read_errors,
)
async def list_model_connection_models(
    access: Owner,
    service: Service,
    connection_id: UUID,
    cursor: Cursor = None,
    limit: Limit = PAGE_DEFAULT,
) -> ConnectionModelPage:
    return await service.list_models(access, connection_id, cursor=cursor, limit=limit)


@router.put(
    "/{connection_id}/models",
    summary="整体替换模型集合（owner）",
    description="selected：给出明确的模型 ID（至少一个）；all_available：使用最近一次完整同步的"
    "模型列表（没有完整同步时 409 MODEL_SYNC_REQUIRED）。要移除的模型正被智能体使用时 409 "
    "MODEL_IN_USE（details 列出实例）。",
    dependencies=_write,
    responses=_write_errors,
)
async def replace_model_connection_models(
    access: Owner,
    service: Service,
    request: Request,
    connection_id: UUID,
    body: ModelsReplace,
) -> ModelConnectionOut:
    return await service.replace_models(access, connection_id, body, client_ip=_ip(request))


@router.get(
    "/{connection_id}/grants",
    summary="连接授权给了哪些实例（owner）",
    responses=_read_errors,
)
async def list_model_connection_grants(
    access: Owner,
    service: Service,
    connection_id: UUID,
    cursor: Cursor = None,
    limit: Limit = PAGE_DEFAULT,
) -> ConnectionGrantPage:
    return await service.list_grants(access, connection_id, cursor=cursor, limit=limit)


@router.put(
    "/{connection_id}/grants",
    summary="整体替换连接授权（owner）",
    description="跨工作空间或不存在的实例 404；撤销正以它为主模型的实例返回 409 GRANT_IN_USE"
    "（先切换或解除主模型）。撤销时同一事务撤掉这条凭据对该实例的旧授权（含运行环境的引用）。",
    dependencies=_write,
    responses=_write_errors,
)
async def replace_model_connection_grants(
    access: Owner,
    service: Service,
    request: Request,
    connection_id: UUID,
    body: GrantsReplace,
) -> ModelConnectionOut:
    return await service.replace_grants(access, connection_id, body, client_ip=_ip(request))


@router.get(
    "/{connection_id}/activity",
    summary="连接的活动记录（owner）",
    description="审计事件（创建 / 配置 / 更换 Key / 授权 / 应用）与取用记录按时间倒序合并；"
    "脱敏投影，不含 IP、Key 或远端响应。",
    responses=_read_errors,
)
async def list_model_connection_activity(
    access: Owner,
    service: Service,
    connection_id: UUID,
    cursor: Cursor = None,
    limit: Limit = PAGE_DEFAULT,
    instance_id: UUID | None = None,
    result: Literal["ok", "not_found", "rate_limited"] | None = None,
) -> ConnectionActivityPage:
    return await service.activity(
        access,
        connection_id,
        cursor=cursor,
        limit=limit,
        instance_id=instance_id,
        result=result,
    )
