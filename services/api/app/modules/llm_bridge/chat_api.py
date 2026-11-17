"""大模型聚合聊天页面的接口（docs/v2/LLM_CHAT_PLAN.md 第 4.2 节）。

都挂在 /api/v1/llm_bridge/bridges/{bridge_id}/chat 下，登记在模块的 router 上
（deps.py；宿主自动加启用检查）。
owner 和 admin 都能用；写操作带 CSRF。浏览器不直连 MCP 管理器，也拿不到管理令牌。
"""

from collections.abc import AsyncIterator
from typing import Annotated, Any, Literal
from urllib.parse import quote
from uuid import UUID

from fastapi import Depends, Path, Query, Request, Response, status
from fastapi.sse import EventSourceResponse

from app.api.deps import csrf_protected
from app.core.errors import ApiError
from app.modules.llm_bridge.chat import LlmChatService
from app.modules.llm_bridge.chat_schemas import (
    LlmChatAttachmentOut,
    LlmChatClearedOut,
    LlmChatClearIn,
    LlmChatCompactIn,
    LlmChatContinuedOut,
    LlmChatContinueIn,
    LlmChatConversationOut,
    LlmChatCreatedOut,
    LlmChatCreateIn,
    LlmChatEventOut,
    LlmChatEventsOut,
    LlmChatModelIn,
    LlmChatOptionsOut,
    LlmChatQueueEditIn,
    LlmChatQueueMoveIn,
    LlmChatQueueOut,
    LlmChatRenameIn,
    LlmChatSendIn,
    LlmChatSessionList,
    LlmChatSessionOut,
    LlmChatTurnOut,
)
from app.modules.llm_bridge.deps import Admin, router
from app.modules.llm_bridge.deps import Service as Bridges
from app.modules.llm_bridge.schemas import Platform
from app.schemas.common import error_responses

SessionId = Annotated[str, Path(pattern=r"^bs_[0-9a-f]{20}$")]
TurnId = Annotated[str, Path(pattern=r"^bt_[0-9a-f]{20}$")]
AttachmentId = Annotated[str, Path(pattern=r"^ba_[0-9a-f]{20}$")]
# 和管理器的上限一致；这里先挡一次，超过的不往管理器转
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024
INLINE_TYPES = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"})
_FILE_BODY = {
    "required": True,
    "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}},
}
_FILE_RESPONSE = {
    "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}},
    "description": "文件内容。四种图片内联显示，别的按附件下载",
}
write = [Depends(csrf_protected)]
read_errors = error_responses(401, 403, 404, 503)


def _json_errors(*status_codes: int) -> dict[int | str, dict[str, Any]]:
    """推送接口的错误在开始推送之前返回，是普通的 JSON；不写明的话会被当成 text/event-stream。"""
    errors = error_responses(*status_codes)
    for response in errors.values():
        response.pop("model")
        response["content"] = {
            "application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponse"}}
        }
    return errors


STREAM_MAX_SECONDS = 300  # 一次推送最长这么久；到时间浏览器带着编号再连
write_errors = error_responses(401, 403, 404, 409, 422, 503)
prefix = "/llm_bridge/bridges/{bridge_id}/chat"


@router.get(
    f"{prefix}/sessions",
    summary="聊天：这个 MCP 管理器上的会话（admin）",
    description="q 按标题搜索；sync=true 时先把官方程序里有、管理器没登记的会话登记进来。"
    "每个会话带 owner_kind：me / user / agent / external。",
    responses=read_errors,
)
async def llm_chat_list_sessions(
    access: Admin,
    bridges: Bridges,
    bridge_id: UUID,
    q: Annotated[str, Query(max_length=120)] = "",
    platform: Platform | None = None,
    status_: Annotated[Literal["active", "archived", "all"], Query(alias="status")] = "active",
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    sync: bool = False,
) -> LlmChatSessionList:
    row = await bridges.bridge(access.workspace.id, bridge_id)
    return await LlmChatService(bridges).sessions(
        access, row, q=q, platform=platform, status=status_, limit=limit, offset=offset, sync=sync
    )


@router.get(
    f"{prefix}/sessions/{{session_id}}",
    summary="聊天：一个会话的信息和消息（admin）",
    description="消息里的回答带所在回合的状态、过程（中间说的话、命令、改动的文件）和失败原因；"
    "进行中的回合给出已经得到的部分。",
    responses=read_errors,
)
async def llm_chat_get_session(
    access: Admin, bridges: Bridges, bridge_id: UUID, session_id: SessionId
) -> LlmChatConversationOut:
    row = await bridges.bridge(access.workspace.id, bridge_id)
    return await LlmChatService(bridges).conversation(access, row, session_id)


@router.get(
    f"{prefix}/sessions/{{session_id}}/events",
    summary="聊天：会话在某个事件之后的新事件（admin，长轮询）",
    description="没有新事件且还有回合没做完时，最多等 wait_seconds 秒再返回。",
    responses=read_errors,
)
async def llm_chat_session_events(
    access: Admin,
    bridges: Bridges,
    bridge_id: UUID,
    session_id: SessionId,
    after: Annotated[int, Query(ge=0)] = 0,
    wait_seconds: Annotated[float, Query(ge=0, le=25)] = 0,
) -> LlmChatEventsOut:
    row = await bridges.bridge(access.workspace.id, bridge_id)
    return await LlmChatService(bridges).events(row, session_id, after, wait_seconds)


@router.get(
    f"{prefix}/sessions/{{session_id}}/export",
    summary="聊天：把一个会话导出成 Markdown（admin）",
    description="提问、回答、每回合的状态和用时；过程和附件的内容不在里面。导出记一条审计。",
    response_class=Response,
    responses={
        200: {"content": {"text/markdown": {"schema": {"type": "string"}}}},
        **read_errors,
    },
)
async def llm_chat_export(
    access: Admin, bridges: Bridges, bridge_id: UUID, session_id: SessionId
) -> Response:
    row = await bridges.bridge(access.workspace.id, bridge_id)
    name, content = await LlmChatService(bridges).export(access, row, session_id)
    return Response(
        content=content.encode(),
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get(
    f"{prefix}/sessions/{{session_id}}/stream",
    summary="聊天：会话的新事件，逐条推送（admin，SSE）",
    description="和 .../events 是同一份数据，只是不用反复请求：每条事件 `event: chat`，"
    "data 是 LlmChatEventOut，id 是事件编号。会话里没有回合在跑、或者推送满 5 分钟时发一条 "
    "`event: end`（data 里有 busy 和 last_id）并结束；busy 为 true 时带着 last_id 再连。"
    "页面用带请求头的 fetch 读这个流（EventSource 带不了 X-Workspace-Id）。"
    "推送用不了时（代理不支持、旧版本）页面退回 .../events 的长轮询。",
    response_class=EventSourceResponse,
    responses=_json_errors(401, 403, 404, 422, 503),
)
async def llm_chat_session_stream(
    request: Request,
    access: Admin,
    bridges: Bridges,
    bridge_id: UUID,
    session_id: SessionId,
    after: Annotated[int, Query(ge=0)] = 0,
) -> AsyncIterator[LlmChatEventOut]:
    # 实际产出的是 ServerSentEvent；注解写 LlmChatEventOut，接口说明里才有 data 的结构
    row = await bridges.bridge(access.workspace.id, bridge_id)
    events = LlmChatService(bridges).stream(
        row, session_id, after, max_seconds=STREAM_MAX_SECONDS, disconnected=request.is_disconnected
    )
    async for item in events:
        yield item  # type: ignore[misc]


@router.get(
    f"{prefix}/options",
    summary="聊天：新建会话时可选的模型和开发环境（admin）",
    description="读不到模型（例如还没登录）时 models 为空、models_error 说明原因。",
    responses=read_errors,
)
async def llm_chat_options(
    access: Admin,
    bridges: Bridges,
    bridge_id: UUID,
    platform: Platform,
    mode: Literal["chat", "dev"] = "chat",
) -> LlmChatOptionsOut:
    row = await bridges.bridge(access.workspace.id, bridge_id)
    return await LlmChatService(bridges).options(access, row, platform, mode)


@router.post(
    f"{prefix}/sessions",
    summary="聊天：新建会话，可以带第一条消息（admin）",
    description="409 BRIDGE_DISABLED：管理器已停用。会话建好但第一条消息没发出去时，"
    "send_error 说明原因。",
    status_code=status.HTTP_201_CREATED,
    dependencies=write,
    responses=write_errors,
)
async def llm_chat_create_session(
    access: Admin, bridges: Bridges, bridge_id: UUID, body: LlmChatCreateIn
) -> LlmChatCreatedOut:
    row = await bridges.bridge(access.workspace.id, bridge_id)
    return await LlmChatService(bridges).create(access, row, body)


@router.post(
    f"{prefix}/sessions/{{session_id}}/messages",
    summary="聊天：向会话发一条消息（admin）",
    description="立刻返回回合（排队中 / 进行中），不等回答；回答用会话内容和事件接口读。"
    "409 TAKEOVER_REQUIRED：不是自己建的会话，要带 takeover=true。",
    dependencies=write,
    responses=write_errors,
)
async def llm_chat_send(
    access: Admin, bridges: Bridges, bridge_id: UUID, session_id: SessionId, body: LlmChatSendIn
) -> LlmChatTurnOut:
    row = await bridges.bridge(access.workspace.id, bridge_id)
    return await LlmChatService(bridges).send(access, row, session_id, body)


@router.post(
    f"{prefix}/turns/{{turn_id}}/cancel",
    summary="聊天：停止一个排队中或进行中的回合（admin）",
    dependencies=write,
    responses=write_errors,
)
async def llm_chat_cancel_turn(
    access: Admin, bridges: Bridges, bridge_id: UUID, turn_id: TurnId
) -> LlmChatTurnOut:
    row = await bridges.bridge(access.workspace.id, bridge_id)
    return await LlmChatService(bridges).cancel(access, row, turn_id)


@router.post(
    f"{prefix}/turns/{{turn_id}}/resume",
    summary="聊天：被额度打断的回合，现在就试着继续（admin）",
    description="不等重置时间；“自动继续”关着或自动续的次数用完时也用它。"
    "不是被额度打断的、已经续跑过的 409。",
    dependencies=write,
    responses=write_errors,
)
async def llm_chat_resume_now(
    access: Admin, bridges: Bridges, bridge_id: UUID, turn_id: TurnId
) -> LlmChatTurnOut:
    row = await bridges.bridge(access.workspace.id, bridge_id)
    return await LlmChatService(bridges).resume(access, row, turn_id, now=True)


@router.delete(
    f"{prefix}/turns/{{turn_id}}/resume",
    summary="聊天：被额度打断的回合，不要自动继续（admin）",
    dependencies=write,
    responses=write_errors,
)
async def llm_chat_resume_cancel(
    access: Admin, bridges: Bridges, bridge_id: UUID, turn_id: TurnId
) -> LlmChatTurnOut:
    row = await bridges.bridge(access.workspace.id, bridge_id)
    return await LlmChatService(bridges).resume(access, row, turn_id, now=False)


@router.get(
    f"{prefix}/sessions/{{session_id}}/queue",
    summary="聊天：一个会话的消息队列（admin）",
    description="正在处理的那一条和排队中的各条，按将要处理的顺序。会话在处理时再发的消息进队列，前面的做完才发给模型。",
    responses=read_errors,
)
async def llm_chat_queue(
    access: Admin, bridges: Bridges, bridge_id: UUID, session_id: SessionId
) -> LlmChatQueueOut:
    row = await bridges.bridge(access.workspace.id, bridge_id)
    return await LlmChatService(bridges).queue(access, row, session_id)


@router.patch(
    f"{prefix}/turns/{{turn_id}}",
    summary="聊天：修改排队中的一条消息（admin）",
    description="只能改还没开始处理的；已经开始的 409。",
    dependencies=write,
    responses=write_errors,
)
async def llm_chat_edit_queued(
    access: Admin, bridges: Bridges, bridge_id: UUID, turn_id: TurnId, body: LlmChatQueueEditIn
) -> LlmChatQueueOut:
    row = await bridges.bridge(access.workspace.id, bridge_id)
    return await LlmChatService(bridges).change_queued(access, row, turn_id, text=body.text)


@router.post(
    f"{prefix}/turns/{{turn_id}}/move",
    summary="聊天：把排队中的一条消息调到队首或队尾（admin）",
    dependencies=write,
    responses=write_errors,
)
async def llm_chat_move_queued(
    access: Admin, bridges: Bridges, bridge_id: UUID, turn_id: TurnId, body: LlmChatQueueMoveIn
) -> LlmChatQueueOut:
    row = await bridges.bridge(access.workspace.id, bridge_id)
    return await LlmChatService(bridges).change_queued(access, row, turn_id, to=body.to)


@router.put(
    f"{prefix}/sessions/{{session_id}}/model",
    summary="聊天：换这个会话之后用的模型（admin）",
    description="从下一回合起生效，已经在跑的不受影响。只有 Claude / CodeBuddy 的会话能换；"
    "Codex 的会话 422 NOT_SUPPORTED（要换就新建会话）。",
    dependencies=write,
    responses=write_errors,
)
async def llm_chat_set_model(
    access: Admin, bridges: Bridges, bridge_id: UUID, session_id: SessionId, body: LlmChatModelIn
) -> LlmChatSessionOut:
    row = await bridges.bridge(access.workspace.id, bridge_id)
    return await LlmChatService(bridges).set_model(access, row, session_id, body.model)


@router.post(
    f"{prefix}/sessions/{{session_id}}/clear",
    summary="聊天：清空会话的上下文（admin）",
    description="之后的消息从头开始：MCP 管理器给这个会话换一个新的官方会话，之前的记录、附件、"
    "开发会话工作目录里的文件都保留，会话内容里在清空的位置有一条 role=context_cleared 的分隔。"
    "有消息在排队或进行中时 409 CONFLICT；已归档 409；官方程序里已有的会话 422 NOT_SUPPORTED；"
    "不是自己建的会话要带 takeover=true，否则 409 TAKEOVER_REQUIRED；"
    "上次清空之后还没发过消息时什么也不做（cleared=false）。",
    dependencies=write,
    responses=write_errors,
)
async def llm_chat_clear_context(
    access: Admin, bridges: Bridges, bridge_id: UUID, session_id: SessionId, body: LlmChatClearIn
) -> LlmChatClearedOut:
    row = await bridges.bridge(access.workspace.id, bridge_id)
    return await LlmChatService(bridges).clear_context(
        access, row, session_id, takeover=body.takeover
    )


@router.post(
    f"{prefix}/sessions/{{session_id}}/compact",
    summary="聊天：手动压缩会话的上下文（admin）",
    description="排一个回合（kind=compact），轮到时让平台把之前的内容压成摘要"
    "（docs/v2/LLM_BRIDGE_MEMORY_PLAN.md 4.2）。instructions 是要保留什么（Claude 会用）。"
    "回合结果的 summary.compacted 说明平台有没有真的压缩。不是自己建的会话要带 takeover=true。",
    dependencies=write,
    responses=write_errors,
)
async def llm_chat_compact(
    access: Admin,
    bridges: Bridges,
    bridge_id: UUID,
    session_id: SessionId,
    body: LlmChatCompactIn,
) -> LlmChatTurnOut:
    row = await bridges.bridge(access.workspace.id, bridge_id)
    return await LlmChatService(bridges).compact(access, row, session_id, body)


@router.post(
    f"{prefix}/sessions/{{session_id}}/continue",
    summary="聊天：带着摘要另开会话（admin）",
    description="复用清空上下文：先在老的上下文里要一份交接摘要（kind=summary），"
    "再清空上下文、把摘要作为新上下文的第一条发出（kind=handoff）。两个回合排在会话队列里；"
    "老的上下文没写出摘要时用管理器的记录排一份。同时整理一次会话纪要。"
    "上次清空之后还没发过消息 409；不是自己建的会话要带 takeover=true。",
    dependencies=write,
    responses=write_errors,
)
async def llm_chat_continue(
    access: Admin,
    bridges: Bridges,
    bridge_id: UUID,
    session_id: SessionId,
    body: LlmChatContinueIn,
) -> LlmChatContinuedOut:
    row = await bridges.bridge(access.workspace.id, bridge_id)
    return await LlmChatService(bridges).continue_with_summary(access, row, session_id, body)


@router.patch(
    f"{prefix}/sessions/{{session_id}}",
    summary="聊天：改会话标题（admin）",
    dependencies=write,
    responses=write_errors,
)
async def llm_chat_rename_session(
    access: Admin, bridges: Bridges, bridge_id: UUID, session_id: SessionId, body: LlmChatRenameIn
) -> LlmChatSessionOut:
    row = await bridges.bridge(access.workspace.id, bridge_id)
    return await LlmChatService(bridges).rename(access, row, session_id, body.title)


@router.post(
    f"{prefix}/sessions/{{session_id}}/archive",
    summary="聊天：归档会话（admin；之后不能再发送）",
    dependencies=write,
    responses=write_errors,
)
async def llm_chat_archive_session(
    access: Admin, bridges: Bridges, bridge_id: UUID, session_id: SessionId
) -> LlmChatSessionOut:
    row = await bridges.bridge(access.workspace.id, bridge_id)
    return await LlmChatService(bridges).archive(access, row, session_id)


@router.post(
    f"{prefix}/sessions/{{session_id}}/attachments",
    status_code=status.HTTP_201_CREATED,
    summary="聊天：上传一个要随下一条消息发出去的图片 / 文件（admin）",
    description="请求体就是文件内容，文件名在查询参数 name 里。单个最多 20 MB；"
    "图片（PNG / JPEG / GIF / WebP）在哪种会话里都能带，别的文件只能在开发会话里带。"
    "返回的 id 在发送时放进 attachments。文件存在管理器那里。",
    dependencies=write,
    responses=error_responses(401, 403, 404, 409, 413, 422, 503),
    openapi_extra={"requestBody": _FILE_BODY},
)
async def llm_chat_upload(
    request: Request,
    access: Admin,
    bridges: Bridges,
    bridge_id: UUID,
    session_id: SessionId,
    name: Annotated[str, Query(min_length=1, max_length=255, description="文件名")],
) -> LlmChatAttachmentOut:
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_ATTACHMENT_BYTES:
        raise _too_large()
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_ATTACHMENT_BYTES:
            raise _too_large()
    row = await bridges.bridge(access.workspace.id, bridge_id)
    return await LlmChatService(bridges).upload(access, row, session_id, name, bytes(body))


def _too_large() -> ApiError:
    return ApiError(413, "PAYLOAD_TOO_LARGE", "文件太大（单个最多 20 MB）")


@router.delete(
    f"{prefix}/sessions/{{session_id}}/attachments/{{attachment_id}}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="聊天：去掉一个上传了还没发出去的附件（admin）",
    dependencies=write,
    responses=write_errors,
)
async def llm_chat_discard_upload(
    access: Admin,
    bridges: Bridges,
    bridge_id: UUID,
    session_id: SessionId,
    attachment_id: AttachmentId,
) -> Response:
    row = await bridges.bridge(access.workspace.id, bridge_id)
    await LlmChatService(bridges).discard(row, session_id, attachment_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    f"{prefix}/sessions/{{session_id}}/attachments/{{attachment_id}}",
    summary="聊天：下载一个附件（admin）",
    response_class=Response,
    responses={200: _FILE_RESPONSE, **read_errors},
)
async def llm_chat_download(
    access: Admin,
    bridges: Bridges,
    bridge_id: UUID,
    session_id: SessionId,
    attachment_id: AttachmentId,
) -> Response:
    row = await bridges.bridge(access.workspace.id, bridge_id)
    bridge_name = row.name
    content, media_type, name = await LlmChatService(bridges).download(
        row, session_id, attachment_id
    )
    # 文件是别人（或模型）给的：只有四种图片内联，别的一律下载；不许浏览器猜类型、不许执行
    inline = media_type in INLINE_TYPES
    if not inline:  # 下载文件记一条审计；页面上自动显示的图片缩略图不记
        await bridges.audit(
            access,
            "chat_attachment_downloaded",
            bridge_id,
            {"session": session_id, "bridge": bridge_name, "attachment": attachment_id},
        )
        await bridges.db.commit()
    return Response(
        content=content,
        media_type=media_type if inline else "application/octet-stream",
        headers={
            "Content-Disposition": f"{'inline' if inline else 'attachment'}; "
            f"filename*=UTF-8''{quote(name)}",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "Cache-Control": "private, no-store",
        },
    )


@router.post(
    f"{prefix}/sessions/{{session_id}}/reimport",
    summary="聊天：官方程序里已有的会话，重新导入一次内容（admin）",
    description="第一次打开时已经导入过；官方程序里又有了新内容时用。不是这类会话 409。",
    dependencies=write,
    responses=write_errors,
)
async def llm_chat_reimport(
    access: Admin, bridges: Bridges, bridge_id: UUID, session_id: SessionId
) -> LlmChatConversationOut:
    row = await bridges.bridge(access.workspace.id, bridge_id)
    return await LlmChatService(bridges).reimport(access, row, session_id)
