"""大模型聚合聊天页面的接口（docs/v2/LLM_CHAT_PLAN.md 第 4.2 节）。

都挂在 /api/v1/llm_bridge/bridges/{bridge_id}/chat 下，登记在模块的 router 上
（deps.py；宿主自动加启用检查）。
owner 和 admin 都能用；写操作带 CSRF。浏览器不直连 MCP 管理器，也拿不到管理令牌。
"""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import Depends, Path, Query, status

from app.api.deps import csrf_protected
from app.modules.llm_bridge.chat import LlmChatService
from app.modules.llm_bridge.chat_schemas import (
    LlmChatConversationOut,
    LlmChatCreatedOut,
    LlmChatCreateIn,
    LlmChatEventsOut,
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
write = [Depends(csrf_protected)]
read_errors = error_responses(401, 403, 404, 503)
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
