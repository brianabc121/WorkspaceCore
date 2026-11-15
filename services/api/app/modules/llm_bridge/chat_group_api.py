"""多模型同问的接口（docs/v2/LLM_CHAT_PLAN.md 第 4.3 节）。

都挂在 /api/v1/llm_bridge/chat/groups 下，登记在模块的 router 上（deps.py；宿主自动加启用检查）。
owner 和 admin 都能用；写操作带 CSRF。组里每个模型的内容和进度用单会话的接口读（chat_api.py）。
"""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import Depends, Query, status

from app.api.deps import csrf_protected
from app.modules.llm_bridge.chat_groups import LlmChatGroupService
from app.modules.llm_bridge.chat_schemas import (
    LlmChatGroupCreateIn,
    LlmChatGroupList,
    LlmChatGroupOut,
    LlmChatGroupSendIn,
    LlmChatGroupSentOut,
)
from app.modules.llm_bridge.deps import Admin, router
from app.modules.llm_bridge.deps import Service as Bridges
from app.schemas.common import error_responses

write = [Depends(csrf_protected)]
read_errors = error_responses(401, 403, 404)
write_errors = error_responses(401, 403, 404, 409, 422, 503)
prefix = "/llm_bridge/chat/groups"


@router.get(
    prefix,
    summary="聊天：多模型同问的分组（admin）",
    description="按最后活动排序；每一组带它的成员（哪个 MCP 管理器、平台、模型、会话）。",
    responses=read_errors,
)
async def llm_chat_list_groups(
    access: Admin,
    bridges: Bridges,
    status_: Annotated[Literal["active", "archived", "all"], Query(alias="status")] = "active",
) -> LlmChatGroupList:
    return await LlmChatGroupService(bridges).list(access, status_)


@router.post(
    prefix,
    summary="聊天：把同一个问题同时发给几个模型（admin）",
    description="每个目标各开一个会话并发出问题，可以跨 MCP 管理器；某个目标失败只影响它自己，"
    "原因在这个成员的 error 里。一个会话都没建成时 409。"
    "用同一个 request_id 再建一次，得到的是同一组。",
    status_code=status.HTTP_201_CREATED,
    dependencies=write,
    responses=write_errors,
)
async def llm_chat_create_group(
    access: Admin, bridges: Bridges, body: LlmChatGroupCreateIn
) -> LlmChatGroupSentOut:
    return await LlmChatGroupService(bridges).create(access, body)


@router.get(
    f"{prefix}/{{group_id}}",
    summary="聊天：一组同问和它的成员（admin）",
    responses=read_errors,
)
async def llm_chat_get_group(access: Admin, bridges: Bridges, group_id: UUID) -> LlmChatGroupOut:
    return await LlmChatGroupService(bridges).get(access, group_id)


@router.post(
    f"{prefix}/{{group_id}}/messages",
    summary="聊天：向一组里的每个模型继续发（admin）",
    description="立刻返回每个会话的回合，不等回答。发不出去的（管理器停用、会话没建成）给出原因。"
    "409 TAKEOVER_REQUIRED：不是自己建的一组，要带 takeover=true。",
    dependencies=write,
    responses=write_errors,
)
async def llm_chat_send_group(
    access: Admin, bridges: Bridges, group_id: UUID, body: LlmChatGroupSendIn
) -> LlmChatGroupSentOut:
    return await LlmChatGroupService(bridges).send(access, group_id, body)


@router.post(
    f"{prefix}/{{group_id}}/archive",
    summary="聊天：归档一组同问（admin）",
    description="只是不再把它们并排显示；每个模型的会话不受影响，还能单独继续。",
    dependencies=write,
    responses=write_errors,
)
async def llm_chat_archive_group(
    access: Admin, bridges: Bridges, group_id: UUID
) -> LlmChatGroupOut:
    return await LlmChatGroupService(bridges).archive(access, group_id)
