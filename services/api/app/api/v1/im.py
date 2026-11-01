"""管理员端 IM（V2）：后端中转 OpenIM，浏览器不直连。owner / admin 可用。

约定见 docs/decisions/2026-10-31-v2-oc1-contract.md 第 5 节。会话、消息、置顶、免打扰属于
“当前用户 + 工作空间”。实现：OC-3（T-0055）。
"""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Path, Query, Request, Response, Security, status
from fastapi.responses import JSONResponse, StreamingResponse

from app.api.deps import (
    authenticated,
    client_ip,
    csrf_protected,
    get_im_group_service,
    get_im_service,
    workspace_admin,
)
from app.core.errors import ApiError
from app.domain.im_groups import ImGroupService
from app.domain.im_service import ImService
from app.domain.policy import WorkspaceAccess
from app.schemas.common import error_responses
from app.schemas.im import (
    IMAGE_TYPES,
    MAX_IMAGE_BYTES,
    ConversationId,
    GroupId,
    ImBadges,
    ImContactGroupCreate,
    ImContactGroupList,
    ImContactGroupMembersReplace,
    ImContactGroupOut,
    ImContactGroupUpdate,
    ImContactList,
    ImConversationOpen,
    ImConversationOut,
    ImConversationPage,
    ImConversationUpdate,
    ImGroupCreate,
    ImGroupMembersAdd,
    ImGroupOut,
    ImGroupPolicyOut,
    ImGroupPolicyUpdate,
    ImGroupUpdate,
    ImMessageOut,
    ImMessagePage,
    ImMessagePinList,
    ImMessagePinOut,
    ImMessageSend,
    ImPinCreate,
    ImReadRequest,
    ImStatus,
    ImUploadOut,
    ImUserId,
    ServerMsgId,
)

router = APIRouter(tags=["im"], dependencies=[Security(authenticated)])

Admin = Annotated[WorkspaceAccess, Depends(workspace_admin)]
Service = Annotated[ImService, Depends(get_im_service)]
Groups = Annotated[ImGroupService, Depends(get_im_group_service)]
Cursor = Annotated[str | None, Query(max_length=512, description="上一页返回的 next_cursor")]
Limit = Annotated[int, Query(ge=1, le=100)]
Conversation = Annotated[ConversationId, Path()]
Group = Annotated[GroupId, Path()]
IdempotencyKey = Annotated[
    UUID,
    Header(
        alias="Idempotency-Key",
        description="每次用户提交生成一个新的 UUID；网络重试时复用同一个",
    ),
]
_write = [Depends(csrf_protected)]
_read_errors = error_responses(401, 403, 404, 422)
_local_write_errors = error_responses(401, 403, 404, 409, 422)
_im_write_errors = error_responses(401, 403, 404, 409, 422, 503)
_send_errors = error_responses(401, 403, 404, 409, 413, 422, 429, 503, 504)
_T = "T-0055"

_IMAGE_BODY = {
    "required": True,
    "content": {mime: {"schema": {"type": "string", "format": "binary"}} for mime in IMAGE_TYPES},
}
_IMAGE_RESPONSE = {
    "content": {mime: {"schema": {"type": "string", "format": "binary"}} for mime in IMAGE_TYPES},
    "description": "图片内容（Cache-Control: private）",
}
_EXPORT_RESPONSE = {
    "content": {"application/x-ndjson": {"schema": {"type": "string"}}},
    "description": "每行一条消息（ImMessageOut 的 JSON），按序号升序",
}


# ---------------------------------------------------------------- 状态


@router.get("/im/status", summary="IM 状态（owner / admin）", responses=_read_errors)
async def get_im_status(access: Admin, service: Service) -> ImStatus:
    return await service.status(access)


@router.get(
    "/im/badges",
    summary="顶栏角标：IM 未读数与待审批数（owner / admin）",
    responses=_read_errors,
)
async def get_im_badges(access: Admin, service: Service) -> ImBadges:
    return await service.badges(access)


# ---------------------------------------------------------------- 会话


@router.get("/im/conversations", summary="会话列表（owner / admin）", responses=_read_errors)
async def list_im_conversations(
    access: Admin,
    kind: Literal["single", "group", "notice"] | None = None,
    contact_group_id: Annotated[UUID | None, Query(description="只看这个分组里的实例")] = None,
    q: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
    cursor: Cursor = None,
    limit: Limit = 50,
    *,
    service: Service,
) -> ImConversationPage:
    return await service.list_conversations(
        access, kind=kind, contact_group_id=contact_group_id, q=q, cursor=cursor, limit=limit
    )


@router.post(
    "/im/conversations",
    summary="打开与实例的单聊（owner / admin）",
    description="会话已存在时直接返回；实例的 IM 未就绪时返回 409 IM_NOT_READY。",
    dependencies=_write,
    responses=_im_write_errors,
)
async def open_im_conversation(
    access: Admin, service: Service, body: ImConversationOpen
) -> ImConversationOut:
    return await service.open_conversation(access, body.instance_id)


@router.patch(
    "/im/conversations/{conversation_id}",
    summary="会话置顶 / 免打扰（owner / admin）",
    description="同步到 OpenIM 的 isPinned 与 recvMsgOpt；OpenIM 不可用时返回 503，本地不改。",
    dependencies=_write,
    responses=_im_write_errors,
)
async def update_im_conversation(
    access: Admin, service: Service, conversation_id: Conversation, body: ImConversationUpdate
) -> ImConversationOut:
    return await service.update_conversation(access, conversation_id, body)


@router.post(
    "/im/conversations/{conversation_id}/read",
    summary="标记已读到某个序号（owner / admin）",
    dependencies=_write,
    responses=_read_errors,
)
async def mark_im_conversation_read(
    access: Admin, service: Service, conversation_id: Conversation, body: ImReadRequest
) -> ImConversationOut:
    return await service.mark_read(access, conversation_id, body.seq)


@router.get(
    "/im/conversations/{conversation_id}/messages",
    summary="消息历史（owner / admin）",
    responses=_read_errors,
)
async def list_im_messages(
    access: Admin,
    service: Service,
    conversation_id: Conversation,
    cursor: Cursor = None,
    limit: Limit = 50,
) -> ImMessagePage:
    return await service.list_messages(access, conversation_id, cursor=cursor, limit=limit)


@router.post(
    "/im/conversations/{conversation_id}/messages",
    status_code=status.HTTP_202_ACCEPTED,
    summary="发送消息（owner / admin，幂等）",
    description="以当前用户的 IM 账号发出。结果未知时返回 504 RESULT_UNKNOWN，不会自动重发："
    "用同一个 Idempotency-Key 重发以查询结果。",
    response_model=ImMessageOut,
    dependencies=_write,
    responses=_send_errors,
)
async def send_im_message(
    request: Request,
    access: Admin,
    service: Service,
    conversation_id: Conversation,
    body: ImMessageSend,
    idempotency_key: IdempotencyKey,
) -> Response:
    result = await service.send_message(
        access, conversation_id, body, idempotency_key, path=request.url.path
    )
    headers = {"Idempotent-Replayed": "true"} if result.replayed else None
    return JSONResponse(result.body, status_code=result.status_code, headers=headers)


@router.get(
    "/im/conversations/{conversation_id}/pins",
    summary="会话里置顶的消息（owner / admin）",
    responses=_read_errors,
)
async def list_im_pins(
    access: Admin, service: Service, conversation_id: Conversation
) -> ImMessagePinList:
    return await service.list_pins(access, conversation_id)


@router.post(
    "/im/conversations/{conversation_id}/pins",
    status_code=status.HTTP_201_CREATED,
    summary="置顶一条消息（owner / admin）",
    description="每个会话最多 10 条，超出返回 409 PIN_LIMIT_REACHED；已置顶时直接返回。",
    dependencies=_write,
    responses=_local_write_errors,
)
async def pin_im_message(
    access: Admin, service: Service, conversation_id: Conversation, body: ImPinCreate
) -> ImMessagePinOut:
    return await service.pin(access, conversation_id, body.server_msg_id)


@router.delete(
    "/im/conversations/{conversation_id}/pins/{server_msg_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="取消置顶（owner / admin）",
    dependencies=_write,
    responses=_read_errors,
)
async def unpin_im_message(
    access: Admin,
    service: Service,
    conversation_id: Conversation,
    server_msg_id: Annotated[ServerMsgId, Path()],
) -> None:
    await service.unpin(access, conversation_id, server_msg_id)


@router.get(
    "/im/conversations/{conversation_id}/export",
    summary="导出会话（owner / admin）",
    responses={200: _EXPORT_RESPONSE, **_read_errors},
    response_model=None,
)
async def export_im_conversation(
    request: Request, access: Admin, service: Service, conversation_id: Conversation
) -> StreamingResponse:
    rows = await service.export(
        access, conversation_id, client_ip=client_ip(request, request.app.state.settings)
    )
    filename = f"{conversation_id}.ndjson"
    return StreamingResponse(
        rows,
        media_type="application/x-ndjson",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


# ---------------------------------------------------------------- 图片


@router.post(
    "/im/uploads",
    status_code=status.HTTP_201_CREATED,
    summary="上传图片（owner / admin）",
    description=f"请求体就是图片本身；只接受 {', '.join(IMAGE_TYPES)}，"
    f"不超过 {MAX_IMAGE_BYTES // (1024 * 1024)} MiB；服务端按内容核对类型，不只看 Content-Type。",
    dependencies=_write,
    responses=error_responses(401, 403, 404, 413, 422, 503),
    openapi_extra={"requestBody": _IMAGE_BODY},
)
async def upload_im_image(request: Request, access: Admin, service: Service) -> ImUploadOut:
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_IMAGE_BYTES:
        raise _too_large()
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_IMAGE_BYTES:
            raise _too_large()
    return await service.upload(access, bytes(body))


def _too_large() -> ApiError:
    return ApiError(
        413,
        "PAYLOAD_TOO_LARGE",
        f"图片不能超过 {MAX_IMAGE_BYTES // (1024 * 1024)} MiB",
    )


@router.get(
    "/im/media/{media_id}",
    summary="显示 OpenIM 对象存储里的图片（owner / admin）",
    responses={200: _IMAGE_RESPONSE, **_read_errors},
    response_model=None,
)
async def get_im_media(access: Admin, service: Service, media_id: UUID) -> Response:
    data, mime = await service.media(access, media_id)
    return Response(
        content=data,
        media_type=mime,
        headers={
            "Cache-Control": "private, max-age=300",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "Content-Disposition": "inline",
        },
    )


# ---------------------------------------------------------------- 群


@router.post(
    "/im/groups",
    status_code=status.HTTP_201_CREATED,
    summary="建群（owner / admin，幂等）",
    description="群主是当前用户；成员只能是同一工作空间的实例与 owner / admin。",
    response_model=ImGroupOut,
    dependencies=_write,
    responses=error_responses(401, 403, 404, 409, 422, 503, 504),
)
async def create_im_group(
    request: Request,
    access: Admin,
    groups: Groups,
    body: ImGroupCreate,
    idempotency_key: IdempotencyKey,
) -> Response:
    status_code, out, replayed = await groups.create_group(
        access, body, idempotency_key, path=request.url.path
    )
    headers = {"Idempotent-Replayed": "true"} if replayed else None
    return JSONResponse(out, status_code=status_code, headers=headers)


@router.get("/im/groups/{group_id}", summary="群详情（owner / admin）", responses=_read_errors)
async def get_im_group(access: Admin, groups: Groups, group_id: Group) -> ImGroupOut:
    return await groups.get_group(access, group_id)


@router.patch(
    "/im/groups/{group_id}",
    summary="改群名或群公告（owner / admin）",
    dependencies=_write,
    responses=_im_write_errors,
)
async def update_im_group(
    access: Admin, groups: Groups, group_id: Group, body: ImGroupUpdate
) -> ImGroupOut:
    return await groups.update_group(access, group_id, body)


@router.delete(
    "/im/groups/{group_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="解散群（群主）",
    dependencies=_write,
    responses=_im_write_errors,
)
async def dismiss_im_group(access: Admin, groups: Groups, group_id: Group) -> None:
    await groups.dismiss_group(access, group_id)


@router.post(
    "/im/groups/{group_id}/members",
    summary="邀请成员（owner / admin）",
    dependencies=_write,
    responses=_im_write_errors,
)
async def add_im_group_members(
    access: Admin, groups: Groups, group_id: Group, body: ImGroupMembersAdd
) -> ImGroupOut:
    return await groups.add_members(access, group_id, body)


@router.delete(
    "/im/groups/{group_id}/members/{im_user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="移出成员（owner / admin）",
    description="不能移出群主。",
    dependencies=_write,
    responses=_im_write_errors,
)
async def remove_im_group_member(
    access: Admin, groups: Groups, group_id: Group, im_user_id: Annotated[ImUserId, Path()]
) -> None:
    await groups.remove_member(access, group_id, im_user_id)


@router.put(
    "/im/groups/{group_id}/policy",
    summary="群的防刷屏设置（owner / admin，乐观并发）",
    dependencies=_write,
    responses=_local_write_errors,
)
async def update_im_group_policy(
    access: Admin, groups: Groups, group_id: Group, body: ImGroupPolicyUpdate
) -> ImGroupPolicyOut:
    return await groups.update_policy(access, group_id, body)


# ---------------------------------------------------------------- 通讯录与分组


@router.get(
    "/im/contacts",
    summary="通讯录：本工作空间的全部实例（owner / admin）",
    description="没有删除好友：实例一直在通讯录里，停用的显示为 disabled。",
    responses=_read_errors,
)
async def list_im_contacts(
    access: Admin,
    contact_group_id: UUID | None = None,
    q: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
    *,
    groups: Groups,
) -> ImContactList:
    return await groups.list_contacts(access, contact_group_id=contact_group_id, q=q)


@router.get("/im/contact-groups", summary="分组列表（owner / admin）", responses=_read_errors)
async def list_im_contact_groups(access: Admin, groups: Groups) -> ImContactGroupList:
    return await groups.list_contact_groups(access)


@router.post(
    "/im/contact-groups",
    status_code=status.HTTP_201_CREATED,
    summary="新建分组（owner / admin）",
    dependencies=_write,
    responses=_local_write_errors,
)
async def create_im_contact_group(
    access: Admin, groups: Groups, body: ImContactGroupCreate
) -> ImContactGroupOut:
    return await groups.create_contact_group(access, body)


@router.patch(
    "/im/contact-groups/{contact_group_id}",
    summary="改分组名或排序（owner / admin）",
    dependencies=_write,
    responses=_local_write_errors,
)
async def update_im_contact_group(
    access: Admin, groups: Groups, contact_group_id: UUID, body: ImContactGroupUpdate
) -> ImContactGroupOut:
    return await groups.update_contact_group(access, contact_group_id, body)


@router.delete(
    "/im/contact-groups/{contact_group_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="删除分组（owner / admin）",
    description="只删除分组，不影响实例和好友关系。",
    dependencies=_write,
    responses=_local_write_errors,
)
async def delete_im_contact_group(
    access: Admin, groups: Groups, contact_group_id: UUID, version: Annotated[int, Query(ge=1)]
) -> None:
    await groups.delete_contact_group(access, contact_group_id, version)


@router.put(
    "/im/contact-groups/{contact_group_id}/members",
    summary="设置分组里的实例（owner / admin，整体替换）",
    dependencies=_write,
    responses=_local_write_errors,
)
async def replace_im_contact_group_members(
    access: Admin, groups: Groups, contact_group_id: UUID, body: ImContactGroupMembersReplace
) -> ImContactGroupOut:
    return await groups.replace_contact_group_members(access, contact_group_id, body)
