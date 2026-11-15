"""多模型同问（docs/v2/LLM_CHAT_PLAN.md 第 4.3 节）：同一个问题同时发给几个“管理器 + 平台 + 模型”。

- 一组同问可以跨 MCP 管理器，所以分组登记在工作台；每个成员是某座会话桥上的一个普通会话，
  内容和进度仍然用单会话的接口读（chat_api.py），这里只管“建一组、向一组继续发、列出、查看、归档”。
- 建会话和发送照常走会话桥的工具（密钥拦截、限额、账号一致性、排队都在会话桥里），几个目标同时进行；
  某个目标失败（没登录、限额）只影响它自己这一栏。
- 调会话桥之前先提交事务，不占着数据库连接；审计只记动作，不记问题的内容。
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from app.core.errors import ApiError
from app.domain.policy import WorkspaceAccess
from app.modules.llm_bridge.chat import OWN_SCOPES, SCOPES, LlmChatService
from app.modules.llm_bridge.chat_schemas import (
    LlmChatGroupCreateIn,
    LlmChatGroupList,
    LlmChatGroupMemberOut,
    LlmChatGroupOut,
    LlmChatGroupResult,
    LlmChatGroupSendIn,
    LlmChatGroupSentOut,
    LlmChatTargetIn,
    LlmChatTurnOut,
)
from app.modules.llm_bridge.client import BridgeCaller, BridgeClient
from app.modules.llm_bridge.models import LlmBridge, LlmChatGroup, LlmChatGroupMember
from app.modules.llm_bridge.service import LlmBridgeService

LIST_LIMIT = 100


@dataclass
class _Started:
    """一个目标的结果：会话建成了没有、第一条发出去了没有。"""

    session_id: str | None = None
    model: str | None = None
    turn: LlmChatTurnOut | None = None
    error: str = ""


def _turn(data: dict[str, Any]) -> LlmChatTurnOut:
    return LlmChatTurnOut(
        turn_id=data["turn_id"],
        session_id=data["session_id"],
        status=data["status"],
        ahead_in_session=data.get("ahead_in_session"),
    )


class LlmChatGroupService:
    def __init__(self, bridges: LlmBridgeService) -> None:
        self.bridges = bridges
        self.db = bridges.db
        self.chat = LlmChatService(bridges)

    # ---- 读 ----
    async def _group(self, access: WorkspaceAccess, group_id: uuid.UUID) -> LlmChatGroup:
        row = await self.db.scalar(
            select(LlmChatGroup).where(
                LlmChatGroup.id == group_id, LlmChatGroup.workspace_id == access.workspace.id
            )
        )
        if row is None:
            raise ApiError(404, "NOT_FOUND", "没有这一组同问")
        return row

    async def _members(
        self, group_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, list[LlmChatGroupMember]]:
        found: dict[uuid.UUID, list[LlmChatGroupMember]] = {gid: [] for gid in group_ids}
        if group_ids:
            rows = await self.db.scalars(
                select(LlmChatGroupMember)
                .where(LlmChatGroupMember.group_id.in_(group_ids))
                .order_by(LlmChatGroupMember.group_id, LlmChatGroupMember.position)
            )
            for member in rows:
                found[member.group_id].append(member)
        return found

    def _out(
        self,
        access: WorkspaceAccess,
        group: LlmChatGroup,
        members: list[LlmChatGroupMember],
        bridges: dict[uuid.UUID, LlmBridge],
    ) -> LlmChatGroupOut:
        mine = access.user_id is not None and group.created_by == access.user_id
        kind: Literal["me", "user"] = "me" if mine else "user"
        return LlmChatGroupOut(
            id=group.id,
            title=group.title,
            status=group.status,
            owner=group.created_by_name or None,
            owner_kind=kind,
            members=[
                LlmChatGroupMemberOut(
                    position=m.position,
                    bridge_id=m.bridge_id,
                    # 管理器改过名字就显示现在的；登记被删除后显示当时的
                    bridge_name=bridges[m.bridge_id].name
                    if m.bridge_id in bridges
                    else m.bridge_name,
                    bridge_enabled=m.bridge_id in bridges and bridges[m.bridge_id].enabled,
                    platform=m.platform,
                    mode=m.mode,
                    model=m.model,
                    session_id=m.session_id,
                    error=m.error,
                )
                for m in members
            ],
            created_at=group.created_at,
            updated_at=group.updated_at,
        )

    async def _bridges(self, access: WorkspaceAccess) -> dict[uuid.UUID, LlmBridge]:
        return {row.id: row for row in await self.bridges.bridges(access.workspace.id)}

    async def list(self, access: WorkspaceAccess, status: str) -> LlmChatGroupList:
        query = select(LlmChatGroup).where(LlmChatGroup.workspace_id == access.workspace.id)
        if status != "all":
            query = query.where(LlmChatGroup.status == status)
        groups = list(
            (
                await self.db.scalars(
                    query.order_by(LlmChatGroup.updated_at.desc(), LlmChatGroup.id).limit(
                        LIST_LIMIT
                    )
                )
            ).all()
        )
        members = await self._members([g.id for g in groups])
        bridges = await self._bridges(access)
        return LlmChatGroupList(
            groups=[self._out(access, g, members[g.id], bridges) for g in groups]
        )

    async def get(self, access: WorkspaceAccess, group_id: uuid.UUID) -> LlmChatGroupOut:
        group = await self._group(access, group_id)
        members = (await self._members([group.id]))[group.id]
        return self._out(access, group, members, await self._bridges(access))

    # ---- 写 ----
    async def _start(
        self,
        client: BridgeClient,
        caller: BridgeCaller,
        target: LlmChatTargetIn,
        title: str,
        text: str,
        request_id: str,
    ) -> _Started:
        arguments = target.model_dump(exclude_none=True, exclude={"bridge_id"}) | {"title": title}
        try:
            created = await self.chat._tool(client, caller, "llm_session_create", arguments)
        except ApiError as error:
            return _Started(error=error.message)
        session_id = str(created["session_id"])
        started = _Started(session_id=session_id, model=created.get("model") or target.model)
        try:
            started.turn = _turn(
                await self.chat._tool(
                    client,
                    caller,
                    "llm_send",
                    {
                        "session_id": session_id,
                        "text": text,
                        # 会话桥按“调用方 + 幂等号”去重：带上会话，几个目标互不相干
                        "request_id": f"{request_id}.{session_id[-8:]}",
                        "wait_seconds": 0,
                    },
                )
            )
        except ApiError as error:
            started.error = f"会话建好了，但问题没有发出去：{error.message}"
        return started

    async def create(
        self, access: WorkspaceAccess, body: LlmChatGroupCreateIn
    ) -> LlmChatGroupSentOut:
        caller = await self.chat._caller(access)
        workspace_id, user_id, user_name = access.workspace.id, access.user_id, caller.label
        existing = await self.db.scalar(
            select(LlmChatGroup).where(
                LlmChatGroup.workspace_id == workspace_id,
                LlmChatGroup.created_by == user_id,
                LlmChatGroup.request_id == body.request_id,
            )
        )
        if existing is not None:
            # 重试：这一组已经建好了，原样给出（回合用单会话的接口读）
            return LlmChatGroupSentOut(group=await self.get(access, existing.id), results=[])
        prepared: list[tuple[LlmChatTargetIn, BridgeClient, uuid.UUID, str]] = []
        for target in body.targets:
            row = await self.bridges.bridge(workspace_id, target.bridge_id)
            self.chat._writable(row)
            prepared.append((target, await self.bridges.client(row), row.id, row.name))
        title = " ".join((body.title or body.text).split())[:120]
        await self.db.commit()

        started = await asyncio.gather(
            *(
                self._start(client, caller, target, title, body.text, body.request_id)
                for target, client, _, _ in prepared
            )
        )
        if not any(s.session_id for s in started):
            raise ApiError(409, "CONFLICT", f"一个会话都没有建成：{started[0].error}")

        group = LlmChatGroup(
            workspace_id=workspace_id,
            title=title,
            created_by=user_id,
            created_by_name=user_name,
            request_id=body.request_id,
        )
        self.db.add(group)
        try:
            await self.db.flush()
        except IntegrityError as error:
            # 同一个幂等号的两次请求同时到：另一次已经建好了这一组
            await self.db.rollback()
            raise ApiError(409, "CONFLICT", "这一组正在建立，稍后在列表里查看") from error
        for position, ((target, _, bridge_id, bridge_name), result) in enumerate(
            zip(prepared, started, strict=True)
        ):
            self.db.add(
                LlmChatGroupMember(
                    group_id=group.id,
                    position=position,
                    bridge_id=bridge_id,
                    bridge_name=bridge_name,
                    platform=target.platform,
                    mode=target.mode,
                    model=result.model,
                    session_id=result.session_id,
                    error=result.error,
                )
            )
        group_id = group.id
        await self.bridges.audit(
            access,
            "chat_group_created",
            group_id,
            {
                "group": str(group_id),
                "targets": len(prepared),
                "sessions": [s.session_id for s in started if s.session_id],
            },
        )
        await self.db.commit()
        return LlmChatGroupSentOut(
            group=await self.get(access, group_id),
            results=[
                LlmChatGroupResult(position=position, turn=result.turn, error=result.error)
                for position, result in enumerate(started)
            ],
        )

    async def _send_one(
        self,
        client: BridgeClient,
        caller: BridgeCaller,
        position: int,
        session_id: str,
        body: LlmChatGroupSendIn,
    ) -> LlmChatGroupResult:
        try:
            data = await self.chat._tool(
                client,
                caller,
                "llm_send",
                {
                    "session_id": session_id,
                    "text": body.text,
                    "request_id": f"{body.request_id}.{session_id[-8:]}",
                    "wait_seconds": 0,
                },
            )
        except ApiError as error:
            return LlmChatGroupResult(position=position, error=error.message)
        return LlmChatGroupResult(position=position, turn=_turn(data))

    async def send(
        self, access: WorkspaceAccess, group_id: uuid.UUID, body: LlmChatGroupSendIn
    ) -> LlmChatGroupSentOut:
        group = await self._group(access, group_id)
        if group.status != "active":
            raise ApiError(409, "CONFLICT", "这一组同问已归档，不能再发送")
        if group.created_by != access.user_id and not body.takeover:
            raise ApiError(
                409,
                "TAKEOVER_REQUIRED",
                "这一组不是你建的：确认后再发送（问题会进入每个模型原来的会话）",
            )
        caller = await self.chat._caller(access, SCOPES if body.takeover else OWN_SCOPES)
        bridges = await self._bridges(access)
        members = (await self._members([group.id]))[group.id]
        sendable: list[tuple[int, str, BridgeClient]] = []
        results: dict[int, LlmChatGroupResult] = {}
        for member in members:
            row = bridges.get(member.bridge_id) if member.bridge_id else None
            if member.session_id is None:
                reason = member.error or "这个模型的会话没有建成"
            elif row is None:
                reason = "这个 MCP 管理器的登记已删除"
            elif not row.enabled:
                reason = "这个 MCP 管理器已停用"
            else:
                sendable.append(
                    (member.position, member.session_id, await self.bridges.client(row))
                )
                continue
            results[member.position] = LlmChatGroupResult(position=member.position, error=reason)
        await self.db.commit()

        for result in await asyncio.gather(
            *(
                self._send_one(client, caller, position, session_id, body)
                for position, session_id, client in sendable
            )
        ):
            results[result.position] = result

        # 列表按最后活动排序
        await self.db.execute(
            update(LlmChatGroup).where(LlmChatGroup.id == group_id).values(updated_at=func.now())
        )
        await self.bridges.audit(
            access,
            "chat_group_takeover_sent" if body.takeover else "chat_group_sent",
            group_id,
            {
                "group": str(group_id),
                "turns": [r.turn.turn_id for r in results.values() if r.turn],
            },
        )
        await self.db.commit()
        return LlmChatGroupSentOut(
            group=await self.get(access, group_id),
            results=[results[position] for position in sorted(results)],
        )

    async def archive(self, access: WorkspaceAccess, group_id: uuid.UUID) -> LlmChatGroupOut:
        group = await self._group(access, group_id)
        if group.status != "archived":
            group.status = "archived"
            await self.bridges.audit(
                access, "chat_group_archived", group_id, {"group": str(group_id)}
            )
            await self.db.commit()
        return await self.get(access, group_id)
