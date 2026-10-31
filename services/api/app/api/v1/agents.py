"""OpenClaw 实例与密钥库的管理员接口（V2）：只给 owner。

约定见 docs/decisions/2026-10-31-v2-oc1-contract.md 第 3、4 节。实例凭证、客户端证书私钥
只在接入包（zip）里出现一次；密钥只写不读。实现：OC-2（T-0054）。
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Security, status

from app.api.deps import authenticated, csrf_protected, workspace_owner
from app.core.errors import not_implemented
from app.domain.policy import WorkspaceAccess
from app.schemas.agents import (
    AgentInstanceCreate,
    AgentInstanceOut,
    AgentInstancePage,
    AgentInstanceUpdate,
    AgentSecretCreate,
    AgentSecretOut,
    AgentSecretPage,
    AgentSecretRotate,
    AgentSecretUpdate,
    AgentSettingsOut,
    AgentSettingsUpdate,
    BundleRequest,
    SecretAccessPage,
    SecretAccessResult,
    SecretGrantsReplace,
    VersionBody,
)
from app.schemas.common import error_responses

router = APIRouter(tags=["agents"], dependencies=[Security(authenticated)])

Owner = Annotated[WorkspaceAccess, Depends(workspace_owner)]
Cursor = Annotated[str | None, Query(max_length=512, description="上一页返回的 next_cursor")]
Limit = Annotated[int, Query(ge=1, le=100)]
_write = [Depends(csrf_protected)]
_read_errors = error_responses(401, 403, 404, 422)
_write_errors = error_responses(401, 403, 404, 409, 422)
_im_errors = error_responses(401, 403, 404, 409, 422, 503)
_T = "T-0054"

_ZIP = {
    "content": {"application/zip": {"schema": {"type": "string", "format": "binary"}}},
    "description": "接入包：myworkspace.env、openclaw.json 片段、取密钥小程序、启动包装脚本、"
    "定时器、CHECKLIST.md；外网模式另有客户端证书、私钥与隧道配置。"
    "凭证只在这里出现一次（Cache-Control: no-store）",
}


# ---------------------------------------------------------------- 全局总开关
# 固定路径写在 /agents/instances/{instance_id} 之前


@router.get("/agents/settings", summary="实例总开关（owner）", responses=_read_errors)
async def get_agent_settings(access: Owner) -> AgentSettingsOut:
    raise not_implemented(_T)


@router.patch(
    "/agents/settings",
    summary="打开或关闭实例总开关（owner）",
    description="关闭后所有实例的 MCP、取密钥、IM 立即被拒绝，并强制下线 IM；"
    "凭证不吊销，重新打开即恢复。",
    dependencies=_write,
    responses=_im_errors,
)
async def update_agent_settings(access: Owner, body: AgentSettingsUpdate) -> AgentSettingsOut:
    raise not_implemented(_T)


# ---------------------------------------------------------------- 实例


@router.get("/agents/instances", summary="实例列表（owner）", responses=_read_errors)
async def list_agent_instances(
    access: Owner, cursor: Cursor = None, limit: Limit = 50
) -> AgentInstancePage:
    raise not_implemented(_T)


@router.post(
    "/agents/instances",
    status_code=status.HTTP_201_CREATED,
    summary="登记实例（owner）",
    description="同一个事务里写入实例；随后注册 OpenIM 用户并与 owner / admin 互加好友，"
    "失败时 im_ready 为 false，后台重试。不签发凭证：凭证在接入包里。",
    dependencies=_write,
    responses=_write_errors,
)
async def create_agent_instance(access: Owner, body: AgentInstanceCreate) -> AgentInstanceOut:
    raise not_implemented(_T)


@router.get("/agents/instances/{instance_id}", summary="实例详情（owner）", responses=_read_errors)
async def get_agent_instance(access: Owner, instance_id: UUID) -> AgentInstanceOut:
    raise not_implemented(_T)


@router.patch(
    "/agents/instances/{instance_id}",
    summary="修改实例（owner，乐观并发）",
    dependencies=_write,
    responses=_im_errors,
)
async def update_agent_instance(
    access: Owner, instance_id: UUID, body: AgentInstanceUpdate
) -> AgentInstanceOut:
    raise not_implemented(_T)


@router.post(
    "/agents/instances/{instance_id}/disable",
    summary="停用实例（owner）",
    description="凭证立即失效、强制退出 IM、拒绝取密钥。",
    dependencies=_write,
    responses=_im_errors,
)
async def disable_agent_instance(
    access: Owner, instance_id: UUID, body: VersionBody
) -> AgentInstanceOut:
    raise not_implemented(_T)


@router.post(
    "/agents/instances/{instance_id}/enable",
    summary="启用实例（owner）",
    dependencies=_write,
    responses=_write_errors,
)
async def enable_agent_instance(
    access: Owner, instance_id: UUID, body: VersionBody
) -> AgentInstanceOut:
    raise not_implemented(_T)


@router.post(
    "/agents/instances/{instance_id}/bundle",
    summary="生成接入包并签发新凭证（owner）",
    description="每次调用都签发新凭证（外网模式另签发客户端证书）。"
    "previous=keep_24h 时旧凭证再保留 24 小时；同一种凭证同时最多两份有效，"
    "超出时最旧的一份立即吊销。已停用的实例返回 409 AGENT_DISABLED。",
    dependencies=_write,
    responses={200: _ZIP, **_write_errors},
    response_model=None,
)
async def issue_agent_bundle(access: Owner, instance_id: UUID, body: BundleRequest) -> None:
    raise not_implemented(_T)


@router.post(
    "/agents/instances/{instance_id}/credentials/{credential_id}/revoke",
    summary="立即吊销一份凭证（owner）",
    dependencies=_write,
    responses=_write_errors,
)
async def revoke_agent_credential(
    access: Owner, instance_id: UUID, credential_id: UUID, body: VersionBody
) -> AgentInstanceOut:
    raise not_implemented(_T)


@router.put(
    "/agents/instances/{instance_id}/secret-grants",
    summary="设置实例可以取用的密钥（owner，整体替换）",
    dependencies=_write,
    responses=_write_errors,
)
async def replace_agent_secret_grants(
    access: Owner, instance_id: UUID, body: SecretGrantsReplace
) -> AgentInstanceOut:
    raise not_implemented(_T)


# ---------------------------------------------------------------- 密钥库


@router.get("/agents/secrets", summary="密钥列表（owner，不含明文）", responses=_read_errors)
async def list_agent_secrets(
    access: Owner, cursor: Cursor = None, limit: Limit = 50
) -> AgentSecretPage:
    raise not_implemented(_T)


@router.post(
    "/agents/secrets",
    status_code=status.HTTP_201_CREATED,
    summary="新增密钥（owner，只写不读）",
    dependencies=_write,
    responses=_write_errors,
)
async def create_agent_secret(access: Owner, body: AgentSecretCreate) -> AgentSecretOut:
    raise not_implemented(_T)


@router.get(
    "/agents/secrets/accesses",
    summary="取密钥记录（owner）",
    responses=_read_errors,
)
async def list_secret_accesses(
    access: Owner,
    instance_id: UUID | None = None,
    secret_id: UUID | None = None,
    result: SecretAccessResult | None = None,
    cursor: Cursor = None,
    limit: Limit = 50,
) -> SecretAccessPage:
    raise not_implemented(_T)


@router.patch(
    "/agents/secrets/{secret_id}",
    summary="修改密钥的名称与说明（owner）",
    dependencies=_write,
    responses=_write_errors,
)
async def update_agent_secret(
    access: Owner, secret_id: UUID, body: AgentSecretUpdate
) -> AgentSecretOut:
    raise not_implemented(_T)


@router.post(
    "/agents/secrets/{secret_id}/rotate",
    summary="轮换密钥的值（owner）",
    description="新值立即生效，旧值不再下发；实例最迟在下一次 secrets reload（每 10 分钟）时换上。",
    dependencies=_write,
    responses=_write_errors,
)
async def rotate_agent_secret(
    access: Owner, secret_id: UUID, body: AgentSecretRotate
) -> AgentSecretOut:
    raise not_implemented(_T)


@router.delete(
    "/agents/secrets/{secret_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="删除密钥（owner）",
    description="同时删除对它的授权；之后实例取用返回 NOT_FOUND。",
    dependencies=_write,
    responses=_write_errors,
)
async def delete_agent_secret(
    access: Owner, secret_id: UUID, version: Annotated[int, Query(ge=1)]
) -> None:
    raise not_implemented(_T)
