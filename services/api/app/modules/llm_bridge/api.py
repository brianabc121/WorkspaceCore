"""大模型会话桥的页面接口：都挂在 /api/v1/llm_bridge 下，宿主自动加启用检查。

- 查看（会话桥列表、状态、环境、会话、实例权限）：admin 及以上；
- 修改（登记会话桥、登录各平台、环境、设置、实例权限）：只有 owner。
登录、环境、设置、会话都是转给会话桥的管理接口（services/llm-bridge 的 /admin/*）。
"""

from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import Depends, Path, Query, Response, status

from app.api.deps import csrf_protected
from app.modules.llm_bridge.deps import Admin, Owner, Service, router
from app.modules.llm_bridge.schemas import (
    LlmBridgeAccountStateOut,
    LlmBridgeConnectivityOut,
    LlmBridgeEnvironmentIn,
    LlmBridgeEnvironmentList,
    LlmBridgeEnvironmentOut,
    LlmBridgeGrantIn,
    LlmBridgeGrantList,
    LlmBridgeGrantOut,
    LlmBridgeIn,
    LlmBridgeIndexList,
    LlmBridgeList,
    LlmBridgeLocalIn,
    LlmBridgeLocalOut,
    LlmBridgeLoginIn,
    LlmBridgeLoginOut,
    LlmBridgeOut,
    LlmBridgePatch,
    LlmBridgeQuotaList,
    LlmBridgeResetOut,
    LlmBridgeSessionList,
    LlmBridgeSettings,
    LlmBridgeSettingsPatch,
    LlmBridgeStatusOut,
    LlmBridgeTurnList,
)
from app.schemas.common import error_responses

SlotPath = Annotated[Literal["openai", "claude", "workbuddy"], Path(description="账号槽位")]
EnvId = Annotated[str, Path(pattern=r"^be_[0-9a-f]{20}$")]
SessionId = Annotated[str, Path(pattern=r"^bs_[0-9a-f]{20}$")]
_write = [Depends(csrf_protected)]
_read_errors = error_responses(401, 403, 404, 503)
_write_errors = error_responses(401, 403, 404, 409, 422, 503)


# ---- 会话桥登记 ----
@router.get("/llm_bridge/bridges", summary="登记的会话桥（admin）", responses=_read_errors)
async def llm_bridge_list_bridges(access: Admin, service: Service) -> LlmBridgeList:
    rows = await service.bridges(access.workspace.id)
    installs = await service.installs(access.workspace.id)
    return LlmBridgeList(bridges=[service.bridge_out(r, installs.get(r.id)) for r in rows])


@router.post(
    "/llm_bridge/bridges",
    summary="登记一座会话桥，也就是新增一个 MCP 管理器（owner）",
    description="令牌加密保存，不回传。填了 platforms（控制的平台）就先写进会话桥，"
    "同时验证地址和令牌：503 EXTERNAL_UNAVAILABLE / BRIDGE_AUTH_FAILED 时不登记；"
    "409 BRIDGE_OUTDATED：会话桥版本太旧，不支持选择平台。409 NAME_TAKEN：已经有同名的。",
    status_code=status.HTTP_201_CREATED,
    dependencies=_write,
    responses=_write_errors,
)
async def llm_bridge_create_bridge(
    access: Owner, service: Service, body: LlmBridgeIn
) -> LlmBridgeOut:
    return await service.create_bridge(access, body)


@router.get(
    "/llm_bridge/local",
    summary="本机部署：随工作台一起启动的 MCP 管理器能不能添加（admin）",
    description="available 为 false：这个工作台没有随带它。"
    "bridge_id 有值：这个工作空间已经添加了。",
    responses=_read_errors,
)
async def llm_bridge_local_state(access: Admin, service: Service) -> LlmBridgeLocalOut:
    return await service.local_state(access.workspace.id)


@router.post(
    "/llm_bridge/local",
    summary="本机部署：把随工作台启动的 MCP 管理器添加进来（owner）",
    description="不用填地址、令牌，也不登录服务器：地址和令牌由工作台自己掌握，不经过页面。"
    "409 LOCAL_UNAVAILABLE：没有随带；LOCAL_EXISTS：已经添加过；"
    "LOCAL_TAKEN：已经被另一个工作空间添加。503：它还没启动好。",
    status_code=status.HTTP_201_CREATED,
    dependencies=_write,
    responses=_write_errors,
)
async def llm_bridge_add_local(
    access: Owner, service: Service, body: LlmBridgeLocalIn
) -> LlmBridgeOut:
    return await service.add_local(access, body)


@router.patch(
    "/llm_bridge/bridges/{bridge_id}",
    summary="修改会话桥的名字、地址、令牌或停用它（owner）",
    dependencies=_write,
    responses=_write_errors,
)
async def llm_bridge_update_bridge(
    access: Owner, service: Service, bridge_id: UUID, body: LlmBridgePatch
) -> LlmBridgeOut:
    return await service.update_bridge(access, bridge_id, body)


@router.delete(
    "/llm_bridge/bridges/{bridge_id}",
    summary="删除会话桥的登记（owner；不影响会话桥容器本身）",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=_write,
    responses=_write_errors,
)
async def llm_bridge_delete_bridge(access: Owner, service: Service, bridge_id: UUID) -> Response:
    await service.delete_bridge(access, bridge_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/llm_bridge/bridges/{bridge_id}/status",
    summary="会话桥的状态：各平台的登录情况、通道、排队、设置（admin）",
    description="连不上会话桥时 reachable 为 false，error 说明原因。",
    responses=_read_errors,
)
async def llm_bridge_bridge_status(
    access: Admin, service: Service, bridge_id: UUID
) -> LlmBridgeStatusOut:
    return await service.status(await service.bridge(access.workspace.id, bridge_id))


@router.get(
    "/llm_bridge/bridges/{bridge_id}/connectivity",
    summary="MCP 管理器所在的服务器到各平台能不能用、延迟多少（admin）",
    description="由管理器去访问各平台的地址（不带凭据）：ok 可用；blocked 连得上但被平台拒绝"
    "（这台服务器的 IP 或地区不被允许）；unreachable 连不上。结果一分钟内复用，"
    "refresh=true 时重新测。旧版本的管理器 supported 为 false。",
    responses=_read_errors,
)
async def llm_bridge_bridge_connectivity(
    access: Admin,
    service: Service,
    bridge_id: UUID,
    refresh: Annotated[bool, Query(description="重新测一次")] = False,
) -> LlmBridgeConnectivityOut:
    row = await service.bridge(access.workspace.id, bridge_id)
    return await service.connectivity(row, refresh=refresh)


@router.get(
    "/llm_bridge/bridges/{bridge_id}/quota",
    summary="各平台的额度：每个窗口已用多少、什么时候重置；是否因为额度暂停着（admin）",
    description="平台给的是已用百分比和重置时间，不是剩余 token 数。"
    "refresh=true 时让管理器向能随时查的平台（Codex）实查一次；"
    "Claude 只在跑回合时顺带报出来，显示的是最近一次看到的；"
    "CodeBuddy 的官方程序不给额度和重置时间（reports_quota=false），只有管理器自己记的用量。",
    responses=_read_errors,
)
async def llm_bridge_bridge_quota(
    access: Admin,
    service: Service,
    bridge_id: UUID,
    refresh: Annotated[bool, Query(description="向平台实查一次")] = False,
) -> LlmBridgeQuotaList:
    return await service.quota(
        await service.bridge(access.workspace.id, bridge_id), refresh=refresh
    )


@router.put(
    "/llm_bridge/bridges/{bridge_id}/settings",
    summary="修改会话桥的设置：控制的平台、并发、时限、每小时发送次数等（owner）",
    dependencies=_write,
    responses=_write_errors,
)
async def llm_bridge_update_settings(
    access: Owner, service: Service, bridge_id: UUID, body: LlmBridgeSettingsPatch
) -> LlmBridgeSettings:
    row = await service.bridge(access.workspace.id, bridge_id)
    values = body.model_dump(exclude_none=True)
    data = await service.admin_write(
        access, row, "settings_updated", "PUT", "/settings", values, values
    )
    return service.settings_out(data)


# ---- 各平台的登录 ----
@router.post(
    "/llm_bridge/bridges/{bridge_id}/accounts/{slot}/login",
    summary="登录一个平台（owner）",
    description="device_code：返回网址和验证码，用户在自己的浏览器里完成，之后轮询登录状态；"
    "api_key / oauth_token：填的值保存在会话桥的数据卷里，不保存在工作台；"
    "existing：使用已经在容器里完成的登录。403：Claude 用订阅登录前要先在设置里确认风险。",
    dependencies=_write,
    responses=_write_errors,
)
async def llm_bridge_login(
    access: Owner, service: Service, bridge_id: UUID, slot: SlotPath, body: LlmBridgeLoginIn
) -> LlmBridgeLoginOut:
    row = await service.bridge(access.workspace.id, bridge_id)
    data = await service.admin_write(
        access,
        row,
        "account_login",
        "POST",
        f"/accounts/{slot}/login",
        body.model_dump(exclude_none=True),
        {"slot": slot, "method": body.method},
    )
    return LlmBridgeLoginOut(**data)


@router.get(
    "/llm_bridge/bridges/{bridge_id}/accounts/{slot}/login",
    summary="登录进行到哪一步（admin）",
    responses=_read_errors,
)
async def llm_bridge_login_state(
    access: Admin, service: Service, bridge_id: UUID, slot: SlotPath
) -> LlmBridgeLoginOut:
    row = await service.bridge(access.workspace.id, bridge_id)
    data = (await service.admin(row, "GET", f"/accounts/{slot}/login")).get("login")
    return LlmBridgeLoginOut(**data) if data else LlmBridgeLoginOut(state="none")


@router.post(
    "/llm_bridge/bridges/{bridge_id}/accounts/{slot}/refresh",
    summary="重新检查一个平台的登录状态（admin）",
    dependencies=_write,
    responses=_write_errors,
)
async def llm_bridge_refresh_account(
    access: Admin, service: Service, bridge_id: UUID, slot: SlotPath
) -> LlmBridgeAccountStateOut:
    row = await service.bridge(access.workspace.id, bridge_id)
    return LlmBridgeAccountStateOut(**await service.admin(row, "POST", f"/accounts/{slot}/refresh"))


@router.post(
    "/llm_bridge/bridges/{bridge_id}/accounts/{slot}/logout",
    summary="退出一个平台的登录（owner）",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=_write,
    responses=_write_errors,
)
async def llm_bridge_logout(
    access: Owner, service: Service, bridge_id: UUID, slot: SlotPath
) -> Response:
    row = await service.bridge(access.workspace.id, bridge_id)
    await service.admin_write(
        access, row, "account_logout", "POST", f"/accounts/{slot}/logout", None, {"slot": slot}
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/llm_bridge/bridges/{bridge_id}/accounts/{slot}/reset",
    summary="更换账号：归档这个平台的全部会话登记，忘掉登记的账号（owner）",
    dependencies=_write,
    responses=_write_errors,
)
async def llm_bridge_reset_account(
    access: Owner, service: Service, bridge_id: UUID, slot: SlotPath
) -> LlmBridgeResetOut:
    row = await service.bridge(access.workspace.id, bridge_id)
    data = await service.admin_write(
        access, row, "account_reset", "POST", f"/accounts/{slot}/reset", None, {"slot": slot}
    )
    return LlmBridgeResetOut(**data)


# ---- 环境 ----
@router.get(
    "/llm_bridge/bridges/{bridge_id}/environments",
    summary="开发环境列表（admin）",
    responses=_read_errors,
)
async def llm_bridge_list_environments(
    access: Admin, service: Service, bridge_id: UUID
) -> LlmBridgeEnvironmentList:
    row = await service.bridge(access.workspace.id, bridge_id)
    return LlmBridgeEnvironmentList(**await service.admin(row, "GET", "/environments"))


@router.post(
    "/llm_bridge/bridges/{bridge_id}/environments",
    summary="新增开发环境（owner）",
    status_code=status.HTTP_201_CREATED,
    dependencies=_write,
    responses=_write_errors,
)
async def llm_bridge_create_environment(
    access: Owner, service: Service, bridge_id: UUID, body: LlmBridgeEnvironmentIn
) -> LlmBridgeEnvironmentOut:
    row = await service.bridge(access.workspace.id, bridge_id)
    meta = {"platform": body.platform, "name": body.name, "sandbox": body.sandbox}
    data = await service.admin_write(
        access, row, "environment_created", "POST", "/environments", body.model_dump(), meta
    )
    return LlmBridgeEnvironmentOut(**data)


@router.put(
    "/llm_bridge/bridges/{bridge_id}/environments/{env_id}",
    summary="修改开发环境（owner；不能改所属的平台）",
    dependencies=_write,
    responses=_write_errors,
)
async def llm_bridge_update_environment(
    access: Owner, service: Service, bridge_id: UUID, env_id: EnvId, body: LlmBridgeEnvironmentIn
) -> LlmBridgeEnvironmentOut:
    row = await service.bridge(access.workspace.id, bridge_id)
    meta = {"platform": body.platform, "name": body.name, "sandbox": body.sandbox}
    data = await service.admin_write(
        access,
        row,
        "environment_updated",
        "PUT",
        f"/environments/{env_id}",
        body.model_dump(),
        meta,
    )
    return LlmBridgeEnvironmentOut(**data)


@router.delete(
    "/llm_bridge/bridges/{bridge_id}/environments/{env_id}",
    summary="删除开发环境（owner）",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=_write,
    responses=_write_errors,
)
async def llm_bridge_delete_environment(
    access: Owner, service: Service, bridge_id: UUID, env_id: EnvId
) -> Response:
    row = await service.bridge(access.workspace.id, bridge_id)
    await service.admin_write(
        access,
        row,
        "environment_deleted",
        "DELETE",
        f"/environments/{env_id}",
        None,
        {"id": env_id},
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---- 会话与回合（只读） ----
@router.get(
    "/llm_bridge/bridges/{bridge_id}/sessions",
    summary="会话桥上的会话（admin）",
    description="sync=true 时先把官方程序里有、会话桥没登记的会话登记进来。",
    responses=_read_errors,
)
async def llm_bridge_list_sessions(
    access: Admin,
    service: Service,
    bridge_id: UUID,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    sync: bool = False,
) -> LlmBridgeSessionList:
    row = await service.bridge(access.workspace.id, bridge_id)
    flag = "true" if sync else "false"
    data: dict[str, Any] = await service.admin(
        row, "GET", f"/sessions?limit={limit}&offset={offset}&sync={flag}"
    )
    return LlmBridgeSessionList(**data)


@router.get(
    "/llm_bridge/bridges/{bridge_id}/sessions/{session_id}/turns",
    summary="一个会话的回合（admin；最新的在前）",
    responses=_read_errors,
)
async def llm_bridge_list_turns(
    access: Admin, service: Service, bridge_id: UUID, session_id: SessionId
) -> LlmBridgeTurnList:
    row = await service.bridge(access.workspace.id, bridge_id)
    return LlmBridgeTurnList(**await service.admin(row, "GET", f"/sessions/{session_id}/turns"))


# ---- 会话索引 ----
@router.get(
    "/llm_bridge/sessions",
    summary="所有 MCP 管理器上的会话（索引，不含对话内容；admin）",
    description="按最后活动倒序。数据来自工作台的会话索引，后台每分钟同步一次；"
    "sync=true 时先同步再读（连不上的管理器列在 sync_failed 里，仍给出已有的记录）。"
    "对话内容不在这里：用聊天页的接口按管理器和会话读。",
    responses=_read_errors,
)
async def llm_bridge_index(
    access: Admin,
    service: Service,
    bridge_id: Annotated[UUID | None, Query(description="只看这个 MCP 管理器")] = None,
    owner_kind: Annotated[
        Literal["agent", "user", "external"] | None,
        Query(description="谁建的：智能体 / 人 / 官方程序里已有"),
    ] = None,
    sync: Annotated[bool, Query(description="先同步一次")] = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> LlmBridgeIndexList:
    workspace_id = access.workspace.id
    failed = await service.sync_index(workspace_id) if sync else []
    items, next_offset = await service.index(
        workspace_id, bridge_id=bridge_id, owner_kind=owner_kind, limit=limit, offset=offset
    )
    return LlmBridgeIndexList(sessions=items, next_offset=next_offset, sync_failed=failed)


# ---- 按实例的权限 ----
@router.get(
    "/llm_bridge/grants",
    summary="每个实例的大模型会话权限（admin）",
    responses=_read_errors,
)
async def llm_bridge_list_grants(access: Admin, service: Service) -> LlmBridgeGrantList:
    return LlmBridgeGrantList(grants=await service.grants(access.workspace.id))


@router.put(
    "/llm_bridge/grants/{instance_id}",
    summary="设置一个实例的大模型会话权限（owner）",
    description="enabled 关掉时其余权限一并收回；platforms 为空表示所有平台。",
    dependencies=_write,
    responses=_write_errors,
)
async def llm_bridge_set_grant(
    access: Owner, service: Service, instance_id: UUID, body: LlmBridgeGrantIn
) -> LlmBridgeGrantOut:
    return await service.set_grant(access, instance_id, body)


# ---- 聊天页面（docs/v2/LLM_CHAT_PLAN.md）：接口写在 chat_api.py，登记在同一个 router 上 ----
from app.modules.llm_bridge import chat_api as _chat_api  # noqa: E402, F401
from app.modules.llm_bridge import chat_group_api as _chat_group_api  # noqa: E402, F401
from app.modules.llm_bridge import deploy_api as _deploy_api  # noqa: E402, F401

__all__ = ["router"]
