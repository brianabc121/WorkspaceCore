"""OpenIM REST 连接器（V2 OC-3，契约 docs/decisions/2026-10-31-v2-oc1-contract.md 第 5、7、10 节）。

只在服务器端用 imAdmin 身份调用；浏览器不直连 OpenIM（AGENTS.md 第 8 节 V2 补充规则）。
接口与字段在 open-im-server v3.8.3-patch.18 / openimsdk/protocol v0.0.73-alpha.19 上核对过
（`internal/api/router.go` 的路由、各请求类型的 JSON 字段）。

- **管理员令牌**：`/auth/get_admin_token` 换取，缓存在 Redis（到期前 1 小时刷新）；
  遇到令牌类错误（1501 ~ 1507）清掉缓存重取一次。令牌有效期由 OpenIM 的
  `tokenPolicy.expire` 决定（`get_user_token` 不能按次指定有效期）。
- **应答**：OpenIM 总是 HTTP 200，靠 `errCode` 区分结果（`pkg/common/servererrs/code.go`），
  这里翻译成 ConnectorError：
  - 1001 → VALIDATION_FAILED；1002 与令牌类 → AUTH_FAILED；
  - 1003、1102（已注册）、1202、1304（已是好友）→ CONFLICT；
  - 1004、1101（用户不存在）、1201、1203、1204（群不存在 / 不在群里 / 已解散）→ NOT_FOUND；
  - 其余 → UNAVAILABLE。错误里只带 errCode，不带对方的错误详情。
- **超时**：所有接口都是 POST，按写操作处理（不自动重试；读超时归为 TIMEOUT_UNKNOWN）。
  只读的查询由调用方在失败后自己决定是否重试（投影有对账兜底）。
- **签发用户令牌会踢掉同一用户同一平台的旧连接**（OC-0 F1）：只在实例启动时由
  `/agent-api/v1/im-token` 调用。
- **导入好友前先查关系**（OC-0 F2）：已是好友时再导入仍会触发回调和一条“已添加好友”通知。
"""

import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from app.integrations.base import ConnectorError, ConnectorErrorKind
from app.integrations.http import ConnectorHttp

# OpenIM 平台号：实例按 Web（5）登录（OC-0 核验）；工作台代发也用 5
PLATFORM_WEB = 5
# 会话类型（sdkws.MsgData.sessionType）
SESSION_SINGLE = 1
SESSION_GROUP = 3
# 消息类型
CONTENT_TEXT = 101
CONTENT_PICTURE = 102
CONTENT_AT_TEXT = 106
# 群类型：工作群（groupType=2）
GROUP_TYPE_WORK = 2

_TOKEN_ERRORS = frozenset(range(1501, 1508))
_ERROR_KINDS: dict[int, ConnectorErrorKind] = {
    1001: "VALIDATION_FAILED",
    1002: "AUTH_FAILED",
    1003: "CONFLICT",
    1004: "NOT_FOUND",
    1101: "NOT_FOUND",  # UserIDNotFoundError
    1102: "CONFLICT",  # RegisteredAlreadyError
    1201: "NOT_FOUND",  # GroupIDNotFoundError
    1202: "CONFLICT",  # GroupIDExisted
    1203: "NOT_FOUND",  # NotInGroupYetError
    1204: "NOT_FOUND",  # DismissedAlreadyError
    1304: "CONFLICT",  # RelationshipAlreadyError
}
_TOKEN_CACHE_KEY = "openim:admin_token"  # noqa: S105 - Redis 键名，不是密钥
_REFRESH_MARGIN_SECONDS = 3600


class TokenCache(Protocol):
    """Redis 的子集（便于测试替换）；三个方法都返回可等待的结果。"""

    def get(self, name: str) -> Any: ...

    def set(self, name: str, value: str, ex: int | None = None) -> Any: ...

    def delete(self, *names: str) -> Any: ...


@dataclass(frozen=True)
class SentMessage:
    server_msg_id: str
    client_msg_id: str
    send_time_ms: int


@dataclass(frozen=True)
class UserToken:
    token: str
    expires_in: int


class OpenIMError(ConnectorError):
    """OpenIM 返回了非 0 的 errCode。"""

    def __init__(self, kind: ConnectorErrorKind, err_code: int, path: str) -> None:
        super().__init__(
            kind, f"OpenIM {path} 返回 errCode {err_code}", reason=f"openim_{err_code}"
        )
        self.err_code = err_code


@dataclass
class OpenIMClient:
    http: ConnectorHttp
    base_url: str
    secret: str
    admin_user_id: str = "imAdmin"
    cache: TokenCache | None = None

    # ----------------------------------------------------------------- 基础

    async def call(
        self, path: str, body: Mapping[str, Any], *, admin: bool = True
    ) -> dict[str, Any]:
        """调用一个 REST 接口，返回 `data`（没有时为 {}）。管理员令牌失效时重取一次。"""
        for attempt in range(2):
            headers = {"operationID": uuid.uuid4().hex}
            if admin:
                headers["token"] = await self._admin_token(force=attempt > 0)
            response = await self.http.request(
                "POST",
                self.base_url + path,
                headers=headers,
                json=dict(body),
                content_type="application/json",
            )
            if response.status_code != 200:
                raise ConnectorError(
                    "UNAVAILABLE",
                    f"OpenIM {path} 返回 HTTP {response.status_code}",
                    status=response.status_code,
                    reason="openim_http",
                )
            try:
                payload = response.json()
                err_code = int(payload.get("errCode", -1))
            except (ValueError, TypeError, AttributeError, json.JSONDecodeError):
                raise ConnectorError(
                    "UNAVAILABLE", "OpenIM 应答无法解析", reason="invalid_response"
                ) from None
            if err_code == 0:
                data = payload.get("data")
                return data if isinstance(data, dict) else {}
            if admin and err_code in _TOKEN_ERRORS and attempt == 0:
                if self.cache is not None:
                    await self.cache.delete(_TOKEN_CACHE_KEY)
                continue
            kind = (
                "AUTH_FAILED"
                if err_code in _TOKEN_ERRORS
                else _ERROR_KINDS.get(err_code, "UNAVAILABLE")
            )
            raise OpenIMError(kind, err_code, path)
        raise ConnectorError("AUTH_FAILED", "OpenIM 管理员令牌无效", reason="openim_admin_token")

    async def _admin_token(self, *, force: bool = False) -> str:
        if self.cache is not None and not force:
            cached = await self.cache.get(_TOKEN_CACHE_KEY)
            if cached:
                return cached.decode() if isinstance(cached, bytes) else str(cached)
        data = await self.call(
            "/auth/get_admin_token",
            {"secret": self.secret, "userID": self.admin_user_id},
            admin=False,
        )
        token = str(data.get("token") or "")
        if not token:
            raise ConnectorError(
                "UNAVAILABLE", "OpenIM 没有返回管理员令牌", reason="invalid_response"
            )
        expires = int(data.get("expireTimeSeconds") or 0)
        if self.cache is not None and expires > _REFRESH_MARGIN_SECONDS:
            await self.cache.set(_TOKEN_CACHE_KEY, token, ex=expires - _REFRESH_MARGIN_SECONDS)
        return token

    # ----------------------------------------------------------------- 用户与好友

    async def register_users(self, users: Sequence[tuple[str, str]]) -> None:
        """注册用户。已注册的用户返回 CONFLICT（1102），由调用方决定是否忽略。"""
        await self.call(
            "/user/user_register",
            {"users": [{"userID": user_id, "nickname": nickname} for user_id, nickname in users]},
        )

    async def existing_users(self, user_ids: Sequence[str]) -> set[str]:
        """`/user/account_check`：accountStatus 为 1（constant.Registered）的用户。"""
        data = await self.call("/user/account_check", {"checkUserIDs": list(user_ids)})
        return {
            str(item.get("userID"))
            for item in data.get("results") or []
            if int(item.get("accountStatus") or 0) == 1
        }

    async def update_nickname(self, user_id: str, nickname: str) -> None:
        await self.call(
            "/user/update_user_info_ex", {"userInfo": {"userID": user_id, "nickname": nickname}}
        )

    async def is_friend(self, user_a: str, user_b: str) -> bool:
        """双方互为好友才返回 True（`/friend/is_friend`）。"""
        data = await self.call("/friend/is_friend", {"userID1": user_a, "userID2": user_b})
        return bool(data.get("inUser1Friends")) and bool(data.get("inUser2Friends"))

    async def import_friends(self, owner: str, friends: Sequence[str]) -> None:
        """双向加好友（`/friend/import_friend`）。调用前先用 is_friend 过滤（OC-0 F2）。"""
        if friends:
            await self.call(
                "/friend/import_friend", {"ownerUserID": owner, "friendUserIDs": list(friends)}
            )

    async def user_token(self, user_id: str, platform_id: int = PLATFORM_WEB) -> UserToken:
        """签发用户令牌。**会踢掉这个用户在同一平台的旧连接**（OC-0 F1）。"""
        data = await self.call(
            "/auth/get_user_token", {"userID": user_id, "platformID": platform_id}
        )
        token = str(data.get("token") or "")
        if not token:
            raise ConnectorError(
                "UNAVAILABLE", "OpenIM 没有返回用户令牌", reason="invalid_response"
            )
        return UserToken(token=token, expires_in=int(data.get("expireTimeSeconds") or 0))

    async def force_logout(self, user_id: str, platform_id: int = PLATFORM_WEB) -> None:
        """强制下线（停用实例、总开关关闭时）。"""
        await self.call("/auth/force_logout", {"userID": user_id, "platformID": platform_id})

    # ----------------------------------------------------------------- 消息

    async def send_text(
        self,
        *,
        send_id: str,
        text: str,
        recv_id: str = "",
        group_id: str = "",
        at_user_ids: Sequence[str] = (),
        sender_nickname: str = "",
    ) -> SentMessage:
        """以 send_id 的身份发文字消息（单聊给 recv_id，群聊给 group_id；@ 时用 106）。"""
        if bool(recv_id) == bool(group_id):
            raise ValueError("recv_id 与 group_id 必须且只能给一个")
        if at_user_ids:
            content: dict[str, Any] = {"text": text, "atUserList": list(at_user_ids)}
            content_type = CONTENT_AT_TEXT
        else:
            content = {"content": text}
            content_type = CONTENT_TEXT
        body: dict[str, Any] = {
            "sendID": send_id,
            "recvID": recv_id,
            "groupID": group_id,
            "senderNickname": sender_nickname,
            "senderPlatformID": PLATFORM_WEB,
            "content": content,
            "contentType": content_type,
            "sessionType": SESSION_GROUP if group_id else SESSION_SINGLE,
        }
        data = await self.call("/msg/send_msg", body)
        return SentMessage(
            server_msg_id=str(data.get("serverMsgID") or ""),
            client_msg_id=str(data.get("clientMsgID") or ""),
            send_time_ms=int(data.get("sendTime") or 0),
        )

    async def max_seqs(self, user_id: str) -> dict[str, int]:
        """这个用户各会话的最大序号（`/msg/newest_seq`）。"""
        data = await self.call("/msg/newest_seq", {"userID": user_id})
        return {str(k): int(v) for k, v in (data.get("maxSeqs") or {}).items()}

    async def pull_messages(
        self, user_id: str, conversation_id: str, begin: int, end: int, *, num: int = 100
    ) -> list[dict[str, Any]]:
        """按序号拉取 [begin, end] 的消息（`/msg/pull_msg_by_seq`）。

        补丁镜像要求 user_id 是调用者本人或管理员。

        返回原始 MsgData 列表（content 是 base64 编码的字节），跳过 seq 为 0 的占位项。
        """
        data = await self.call(
            "/msg/pull_msg_by_seq",
            {
                "userID": user_id,
                "seqRanges": [
                    {"conversationID": conversation_id, "begin": begin, "end": end, "num": num}
                ],
                "order": 0,
            },
        )
        pulled = (data.get("msgs") or {}).get(conversation_id) or {}
        return [m for m in pulled.get("Msgs") or [] if int(m.get("seq") or 0) > 0]

    # ----------------------------------------------------------------- 群

    async def create_group(self, *, owner: str, name: str, members: Sequence[str]) -> str:
        """建工作群（groupType=2，群主是管理员），返回群 ID。"""
        data = await self.call(
            "/group/create_group",
            {
                "ownerUserID": owner,
                "memberUserIDs": list(members),
                "adminUserIDs": [],
                "groupInfo": {"groupName": name, "groupType": GROUP_TYPE_WORK},
            },
        )
        group_id = str((data.get("groupInfo") or {}).get("groupID") or "")
        if not group_id:
            raise ConnectorError("UNAVAILABLE", "OpenIM 没有返回群 ID", reason="invalid_response")
        return group_id

    async def invite_to_group(self, group_id: str, user_ids: Sequence[str]) -> None:
        await self.call(
            "/group/invite_user_to_group",
            {"groupID": group_id, "invitedUserIDs": list(user_ids), "reason": ""},
        )

    async def kick_from_group(self, group_id: str, user_ids: Sequence[str]) -> None:
        await self.call(
            "/group/kick_group",
            {"groupID": group_id, "kickedUserIDs": list(user_ids), "reason": ""},
        )

    async def set_group_info(
        self, group_id: str, *, name: str | None = None, notification: str | None = None
    ) -> None:
        """改群名与群公告（`/group/set_group_info_ex`）。

        这些字段在 protobuf 里是 StringValue，但 OpenIM 的 JSON 解析按普通字符串接收
        （写成 {"value": ...} 会返回 1001，实测）。
        """
        body: dict[str, Any] = {"groupID": group_id}
        if name is not None:
            body["groupName"] = name
        if notification is not None:
            body["notification"] = notification
        await self.call("/group/set_group_info_ex", body)

    async def dismiss_group(self, group_id: str) -> None:
        await self.call("/group/dismiss_group", {"groupID": group_id, "deleteMember": True})

    # ----------------------------------------------------------------- 会话设置（同步到 OpenIM）

    async def set_conversation(
        self,
        owner: str,
        conversation_id: str,
        *,
        conversation_type: int,
        peer_user_id: str = "",
        group_id: str = "",
        pinned: bool | None = None,
        muted: bool | None = None,
    ) -> None:
        """置顶（isPinned）与免打扰（recvMsgOpt=2：接收但不通知）同步到 OpenIM。

        BoolValue / Int32Value 字段按普通的 true / 数字写（同 set_group_info）。
        """
        conversation: dict[str, Any] = {
            "conversationID": conversation_id,
            "conversationType": conversation_type,
            "userID": peer_user_id,
            "groupID": group_id,
        }
        if pinned is not None:
            conversation["isPinned"] = pinned
        if muted is not None:
            conversation["recvMsgOpt"] = 2 if muted else 0
        await self.call(
            "/conversation/set_conversations", {"userIDs": [owner], "conversation": conversation}
        )


def single_conversation_id(user_a: str, user_b: str) -> str:
    """单聊会话 ID：si_<两个用户 ID 按字典序>（OC-0 实测）。"""
    first, second = sorted((user_a, user_b))
    return f"si_{first}_{second}"


def group_conversation_id(group_id: str) -> str:
    """群聊会话 ID：sg_<群 ID>（工作群，sessionType=3）。"""
    return f"sg_{group_id}"
