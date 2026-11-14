"""大模型聚合聊天页面的业务逻辑（docs/v2/LLM_CHAT_PLAN.md 第 4 节）。

- 人也是一个调用方：工作台代表登录的人调用 MCP 管理器（会话桥），调用方编号是 `user:<用户 id>`，
  权限范围 write、account、takeover。会话的归属、限额、串行、幂等、密钥拦截照常由会话桥负责。
- 读（列表、内容、进度）走会话桥的管理接口；写（新建、发送、停止、归档）走它的工具，带着调用方。
- 等会话桥回答之前先提交事务：长轮询最多挂 25 秒，不占着数据库连接。
- 审计只记动作和会话编号，不记消息正文。
"""

from __future__ import annotations

import uuid
from typing import Any
from urllib.parse import urlencode

from app.core.errors import ApiError
from app.domain.policy import WorkspaceAccess
from app.integrations.base import ConnectorError
from app.models import User
from app.modules.llm_bridge.chat_schemas import (
    LlmChatConversationOut,
    LlmChatCreatedOut,
    LlmChatCreateIn,
    LlmChatEventsOut,
    LlmChatMessageOut,
    LlmChatModelOut,
    LlmChatOptionsOut,
    LlmChatSendIn,
    LlmChatSessionList,
    LlmChatSessionOut,
    LlmChatTurnOut,
    OwnerKind,
)
from app.modules.llm_bridge.client import BridgeCaller, BridgeCallError, BridgeClient
from app.modules.llm_bridge.models import LlmBridge
from app.modules.llm_bridge.schemas import LlmBridgeEnvironmentOut
from app.modules.llm_bridge.service import LlmBridgeService, from_bridge, unavailable

SCOPES = ("write", "account", "takeover")
# 没有 takeover：发到别人的会话时会话桥按“没有这个会话”回答，据此要求页面先确认
OWN_SCOPES = ("write", "account")
USER_PREFIX = "user:"


def caller_id(user_id: uuid.UUID | None) -> str:
    return f"{USER_PREFIX}{user_id}"


def owner_kind(session: dict[str, Any], me: str) -> OwnerKind:
    owner = session.get("owner_id")
    if owner == me:
        return "me"
    if isinstance(owner, str) and owner.startswith(USER_PREFIX):
        return "user"
    if session.get("origin") == "external" or not owner:
        return "external"
    return "agent"


def session_out(session: dict[str, Any], me: str) -> LlmChatSessionOut:
    environment = session.get("environment")
    return LlmChatSessionOut(
        session_id=session["session_id"],
        platform=session["platform"],
        mode=session["mode"],
        model=session.get("model"),
        title=session.get("title") or "",
        status=session["status"],
        origin=session["origin"],
        owner=session.get("owner"),
        owner_kind=owner_kind(session, me),
        busy=bool(session.get("busy")),
        turns=int(session.get("turns") or 0),
        created_at=session["created_at"],
        updated_at=session["updated_at"],
        environment=LlmBridgeEnvironmentOut(**environment) if environment else None,
    )


def _turn_out(data: dict[str, Any]) -> LlmChatTurnOut:
    return LlmChatTurnOut(
        turn_id=data["turn_id"],
        session_id=data["session_id"],
        status=data["status"],
        ahead_in_session=data.get("ahead_in_session"),
    )


class LlmChatService:
    def __init__(self, bridges: LlmBridgeService) -> None:
        self.bridges = bridges
        self.db = bridges.db

    async def _caller(
        self, access: WorkspaceAccess, scopes: tuple[str, ...] = SCOPES
    ) -> BridgeCaller:
        user = await self.db.get(User, access.user_id) if access.user_id else None
        if user is None:
            raise ApiError(403, "FORBIDDEN_ROLE", "只有登录的管理员能使用聊天")
        return BridgeCaller(caller_id(user.id), (user.display_name or user.email)[:80], scopes)

    def _writable(self, row: LlmBridge) -> None:
        if not row.enabled:
            raise ApiError(
                409, "BRIDGE_DISABLED", "这个 MCP 管理器已停用：可以查看，不能新建会话和发送"
            )

    async def _admin(self, client: BridgeClient, method: str, path: str, body: Any = None) -> Any:
        try:
            return await client.admin(method, path, body)
        except BridgeCallError as error:
            raise from_bridge(error) from error
        except ConnectorError as error:
            raise unavailable(error) from error

    async def _tool(
        self, client: BridgeClient, caller: BridgeCaller, name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        try:
            data = await client.tool(caller, name, arguments)
        except BridgeCallError as error:
            raise from_bridge(error) from error
        except ConnectorError as error:
            if error.kind == "TIMEOUT_UNKNOWN":
                raise ApiError(
                    504,
                    "EXTERNAL_TIMEOUT",
                    "MCP 管理器没有在时限内回答；用同一个 request_id 重试不会重复发送",
                ) from error
            raise unavailable(error) from error
        if not isinstance(data, dict):
            raise ApiError(502, "EXTERNAL_UNAVAILABLE", "MCP 管理器返回了无法识别的结果")
        return data

    # ---- 读 ----
    async def sessions(
        self,
        access: WorkspaceAccess,
        row: LlmBridge,
        *,
        q: str,
        platform: str | None,
        status: str,
        limit: int,
        offset: int,
        sync: bool,
    ) -> LlmChatSessionList:
        me, client = caller_id(access.user_id), self.bridges.client(row)
        await self.db.commit()
        params = {"limit": limit, "offset": offset, "status": status, "q": q}
        if platform:
            params["platform"] = platform
        if sync:
            params["sync"] = "true"
        data = await self._admin(client, "GET", f"/sessions?{urlencode(params)}")
        return LlmChatSessionList(
            sessions=[session_out(s, me) for s in data.get("sessions", [])],
            next_offset=data.get("next_offset"),
            sync_failed=str(data.get("sync_failed") or ""),
        )

    async def conversation(
        self, access: WorkspaceAccess, row: LlmBridge, session_id: str
    ) -> LlmChatConversationOut:
        me, client = caller_id(access.user_id), self.bridges.client(row)
        await self.db.commit()
        data = await self._admin(client, "GET", f"/sessions/{session_id}")
        messages = []
        for item in data.get("messages", []):
            is_user = item.get("role") == "user"
            messages.append(
                LlmChatMessageOut(
                    role=str(item.get("role")),
                    turn_id=item.get("turn_id"),
                    text=str(item.get("text") or ""),
                    at=item.get("at"),
                    by=item.get("by") if is_user else None,
                    mine=(item.get("by_id") == me) if is_user and item.get("by_id") else None,
                    status=item.get("status"),
                    steps=item.get("steps") or [],
                    failure=item.get("failure"),
                    ahead_in_session=item.get("ahead_in_session"),
                )
            )
        return LlmChatConversationOut(
            session=session_out(data["session"], me),
            messages=messages,
            last_event_id=int(data.get("last_event_id") or 0),
        )

    async def events(
        self, row: LlmBridge, session_id: str, after: int, wait_seconds: float
    ) -> LlmChatEventsOut:
        client = self.bridges.client(row)
        await self.db.commit()
        query = urlencode({"after": after, "wait_seconds": wait_seconds})
        data = await self._admin(client, "GET", f"/sessions/{session_id}/events?{query}")
        return LlmChatEventsOut(**data)

    async def options(
        self, access: WorkspaceAccess, row: LlmBridge, platform: str, mode: str
    ) -> LlmChatOptionsOut:
        caller, client = await self._caller(access), self.bridges.client(row)
        await self.db.commit()
        channel = {"platform": platform, "mode": mode}
        models: list[LlmChatModelOut] = []
        models_error = ""
        try:
            listed = await self._tool(client, caller, "llm_models", channel)
            models = [LlmChatModelOut(**m) for m in listed.get("models", [])]
        except ApiError as error:
            # 读不到模型（还没登录、平台暂时不可用）不算页面出错：照样可以用默认模型新建
            models_error = error.message
        environments: list[LlmBridgeEnvironmentOut] = []
        if mode != "chat":
            listed = await self._tool(client, caller, "llm_environments", channel)
            environments = [LlmBridgeEnvironmentOut(**e) for e in listed.get("environments", [])]
        return LlmChatOptionsOut(
            models=models, models_error=models_error, environments=environments
        )

    # ---- 写 ----
    async def create(
        self, access: WorkspaceAccess, row: LlmBridge, body: LlmChatCreateIn
    ) -> LlmChatCreatedOut:
        self._writable(row)
        caller, client = await self._caller(access), self.bridges.client(row)
        me, bridge_id, bridge_name = caller.instance_id, row.id, row.name
        await self.db.commit()
        arguments = body.model_dump(exclude_none=True, exclude={"text", "request_id"})
        created = await self._tool(client, caller, "llm_session_create", arguments)
        session_id = str(created["session_id"])
        await self.bridges.audit(
            access,
            "chat_session_created",
            bridge_id,
            {
                "bridge": bridge_name,
                "session": session_id,
                "platform": body.platform,
                "mode": body.mode,
                "model": created.get("model"),
            },
        )
        await self.db.commit()
        turn: LlmChatTurnOut | None = None
        send_error = ""
        if body.text is not None and body.request_id is not None:
            try:
                turn = _turn_out(
                    await self._tool(
                        client,
                        caller,
                        "llm_send",
                        {
                            "session_id": session_id,
                            "text": body.text,
                            "request_id": body.request_id,
                            "wait_seconds": 0,
                        },
                    )
                )
            except ApiError as error:
                # 会话已经建好：把原因告诉页面，人可以在会话里重发
                send_error = error.message
            else:
                await self.bridges.audit(
                    access,
                    "chat_message_sent",
                    bridge_id,
                    {"bridge": bridge_name, "session": session_id, "turn": turn.turn_id},
                )
                await self.db.commit()
        view = await self._admin(client, "GET", f"/sessions/{session_id}")
        return LlmChatCreatedOut(
            session=session_out(view["session"], me), turn=turn, send_error=send_error
        )

    async def send(
        self, access: WorkspaceAccess, row: LlmBridge, session_id: str, body: LlmChatSendIn
    ) -> LlmChatTurnOut:
        self._writable(row)
        caller = await self._caller(access, SCOPES if body.takeover else OWN_SCOPES)
        client, bridge_id, bridge_name = self.bridges.client(row), row.id, row.name
        await self.db.commit()
        arguments = {
            "session_id": session_id,
            "text": body.text,
            "request_id": body.request_id,
            "wait_seconds": 0,
        }
        try:
            data = await self._tool(client, caller, "llm_send", arguments)
        except ApiError as error:
            if error.code == "NOT_FOUND" and not body.takeover:
                # 会话在、但不是自己建的：要求页面先让人确认
                await self._admin(client, "GET", f"/sessions/{session_id}/events?after=0")
                raise ApiError(
                    409,
                    "TAKEOVER_REQUIRED",
                    "这不是你建的会话：确认后再发送（消息会进入同一个会话）",
                ) from error
            raise
        await self.bridges.audit(
            access,
            "chat_takeover_sent" if body.takeover else "chat_message_sent",
            bridge_id,
            {"bridge": bridge_name, "session": session_id, "turn": data.get("turn_id")},
        )
        await self.db.commit()
        return _turn_out(data)

    async def cancel(self, access: WorkspaceAccess, row: LlmBridge, turn_id: str) -> LlmChatTurnOut:
        caller, client = await self._caller(access), self.bridges.client(row)
        bridge_id, bridge_name = row.id, row.name
        await self.db.commit()
        data = await self._tool(client, caller, "llm_turn_cancel", {"turn_id": turn_id})
        await self.bridges.audit(
            access,
            "chat_turn_cancelled",
            bridge_id,
            {"bridge": bridge_name, "session": data.get("session_id"), "turn": turn_id},
        )
        await self.db.commit()
        return _turn_out(data)

    async def rename(
        self, access: WorkspaceAccess, row: LlmBridge, session_id: str, title: str
    ) -> LlmChatSessionOut:
        me, client = caller_id(access.user_id), self.bridges.client(row)
        bridge_id, bridge_name = row.id, row.name
        await self.db.commit()
        data = await self._admin(client, "PATCH", f"/sessions/{session_id}", {"title": title})
        await self.bridges.audit(
            access,
            "chat_session_renamed",
            bridge_id,
            {"bridge": bridge_name, "session": session_id},
        )
        await self.db.commit()
        return session_out(data, me)

    async def archive(
        self, access: WorkspaceAccess, row: LlmBridge, session_id: str
    ) -> LlmChatSessionOut:
        caller, client = await self._caller(access), self.bridges.client(row)
        me, bridge_id, bridge_name = caller.instance_id, row.id, row.name
        await self.db.commit()
        await self._tool(client, caller, "llm_session_archive", {"session_id": session_id})
        await self.bridges.audit(
            access,
            "chat_session_archived",
            bridge_id,
            {"bridge": bridge_name, "session": session_id},
        )
        await self.db.commit()
        view = await self._admin(client, "GET", f"/sessions/{session_id}")
        return session_out(view["session"], me)
