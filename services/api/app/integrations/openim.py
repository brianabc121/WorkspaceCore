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

import hashlib
import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urlencode

from app.integrations.base import ConnectorError, ConnectorErrorKind
from app.integrations.http import ConnectorHttp

# OpenIM 平台号：实例按 Web（5）登录（OC-0 核验）；工作台代发也用 5
PLATFORM_WEB = 5
# 会话类型（sdkws.MsgData.sessionType）
SESSION_SINGLE = 1
SESSION_GROUP = 3
# 通知会话（sn_）：只有通知账号能发 OA 通知（contentType 1400）
SESSION_NOTIFICATION = 4
# 消息类型
CONTENT_TEXT = 101
CONTENT_PICTURE = 102
CONTENT_AT_TEXT = 106
CONTENT_OA_NOTIFICATION = 1400
# 通知账号的 AppMangerLevel（protocol constant.AppNotificationAdmin，v0.0.73-alpha.19 核实）
APP_NOTIFICATION_ADMIN = 3
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
    # 不进 repr：异常回溯与调试输出里不出现 secret
    secret: str = field(repr=False)
    admin_user_id: str = "imAdmin"
    cache: TokenCache | None = None

    # ----------------------------------------------------------------- 基础

    async def call(
        self, path: str, body: Mapping[str, Any], *, admin: bool = True
    ) -> dict[str, Any]:
        """调用一个 REST 接口，返回 `data`（不是 JSON 对象时为 {}）。"""
        data = await self.call_any(path, body, admin=admin)
        return data if isinstance(data, dict) else {}

    async def call_any(self, path: str, body: Mapping[str, Any], *, admin: bool = True) -> Any:
        """调用一个 REST 接口，原样返回 `data`（有的接口是列表）。管理员令牌失效时重取一次。"""
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
                return payload.get("data")
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

    async def add_notification_account(self, user_id: str, nickname: str) -> None:
        """注册通知账号（`/user/add_notification_account`，AppMangerLevel = 3）。

        只有通知账号能发 OA 通知；用户 ID 已存在时 OpenIM 返回 1001（VALIDATION_FAILED），
        已有的普通账号不能升级（v3.8.3-patch.18 源码核实），所以调用方先用 existing_users 判断。
        """
        await self.call(
            "/user/add_notification_account",
            {
                "userID": user_id,
                "nickName": nickname,
                "faceURL": "",
                "appMangerLevel": APP_NOTIFICATION_ADMIN,
            },
        )

    async def send_oa_notification(
        self, *, send_id: str, recv_id: str, name: str, text: str, ex: str
    ) -> SentMessage:
        """以通知账号 send_id 给 recv_id 发 OA 通知（会话 sn_<两个 ID 按字典序>）。

        ex 同时写进消息的 ex 与 OA 元素的 ex：投影按消息的 ex 识别审批卡片（实测原样保留）。
        """
        return await self._send(
            send_id=send_id,
            recv_id=recv_id,
            group_id="",
            content={
                "notificationName": name,
                "notificationType": 1,
                "text": text,
                "mixType": 0,
                "ex": ex,
            },
            content_type=CONTENT_OA_NOTIFICATION,
            sender_nickname=name,
            ex=ex,
            session_type=SESSION_NOTIFICATION,
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

    async def online_users(self, user_ids: Sequence[str]) -> set[str]:
        """当前在线的用户（`/user/get_users_online_status`，data 是列表；status 1 为在线）。"""
        if not user_ids:
            return set()
        data = await self.call_any("/user/get_users_online_status", {"userIDs": list(user_ids)})
        items = data if isinstance(data, list) else []
        return {
            str(item.get("userID"))
            for item in items
            if isinstance(item, dict) and int(item.get("status") or 0) == 1
        }

    async def force_logout(self, user_id: str, platform_id: int = PLATFORM_WEB) -> None:
        """强制下线（停用实例、总开关关闭时）。"""
        await self.call("/auth/force_logout", {"userID": user_id, "platformID": platform_id})

    # ----------------------------------------------------------------- 消息

    async def _send(
        self,
        *,
        send_id: str,
        recv_id: str,
        group_id: str,
        content: Mapping[str, Any],
        content_type: int,
        sender_nickname: str,
        ex: str,
        session_type: int | None = None,
    ) -> SentMessage:
        if bool(recv_id) == bool(group_id):
            raise ValueError("recv_id 与 group_id 必须且只能给一个")
        body: dict[str, Any] = {
            "sendID": send_id,
            "recvID": recv_id,
            "groupID": group_id,
            "senderNickname": sender_nickname,
            "senderPlatformID": PLATFORM_WEB,
            "content": dict(content),
            "contentType": content_type,
            "sessionType": session_type or (SESSION_GROUP if group_id else SESSION_SINGLE),
            # 原样存进 MsgData.ex（服务端生成 clientMsgID，调用方不能指定）
            "ex": ex,
        }
        data = await self.call("/msg/send_msg", body)
        return SentMessage(
            server_msg_id=str(data.get("serverMsgID") or ""),
            client_msg_id=str(data.get("clientMsgID") or ""),
            send_time_ms=int(data.get("sendTime") or 0),
        )

    async def send_picture(
        self,
        *,
        send_id: str,
        picture: Mapping[str, Any],
        recv_id: str = "",
        group_id: str = "",
        ex: str = "",
    ) -> SentMessage:
        """图片消息（102）：picture 是 PictureElem（sourcePicture、bigPicture、snapshotPicture，
        每个都要有 type、width、height、url，`pkg/apistruct/msg.go`）。"""
        return await self._send(
            send_id=send_id,
            recv_id=recv_id,
            group_id=group_id,
            content=picture,
            content_type=CONTENT_PICTURE,
            sender_nickname="",
            ex=ex,
        )

    async def send_text(
        self,
        *,
        send_id: str,
        text: str,
        recv_id: str = "",
        group_id: str = "",
        at_user_ids: Sequence[str] = (),
        sender_nickname: str = "",
        ex: str = "",
    ) -> SentMessage:
        """以 send_id 的身份发文字消息（单聊给 recv_id，群聊给 group_id；@ 时用 106）。"""
        if at_user_ids:
            content: dict[str, Any] = {"text": text, "atUserList": list(at_user_ids)}
            content_type = CONTENT_AT_TEXT
        else:
            content = {"content": text}
            content_type = CONTENT_TEXT
        return await self._send(
            send_id=send_id,
            recv_id=recv_id,
            group_id=group_id,
            content=content,
            content_type=content_type,
            sender_nickname=sender_nickname,
            ex=ex,
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

    async def create_group(
        self,
        *,
        owner: str,
        name: str,
        members: Sequence[str],
        group_id: str = "",
        notification: str = "",
    ) -> str:
        """建工作群（groupType=2，群主是管理员），返回群 ID。

        group_id 为空时由 OpenIM 生成；指定且已存在时返回 1202（CONFLICT），调用方可以据此
        把重复的建群当作已完成（`internal/rpc/group/group.go` 的 GenGroupID）。
        调用者 imAdmin 只用来核对用户与计入成员数，并不入群（真实实例已核实）。
        """
        info: dict[str, Any] = {"groupName": name, "groupType": GROUP_TYPE_WORK}
        if group_id:
            info["groupID"] = group_id
        if notification:
            info["notification"] = notification
        data = await self.call(
            "/group/create_group",
            {
                "ownerUserID": owner,
                "memberUserIDs": list(members),
                "adminUserIDs": [],
                "groupInfo": info,
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


# 分片哈希之间的分隔符（openimsdk/tools s3/cont/consts.go）
_PART_SEPARATOR = ","


def _md5(data: bytes) -> str:
    return hashlib.md5(data, usedforsecurity=False).hexdigest()


def _with_query(url: str, query: Sequence[Mapping[str, Any]] | None) -> str:
    pairs = [(kv["key"], v) for kv in query or [] for v in kv.get("values") or []]
    return url if not pairs else url + ("&" if "?" in url else "?") + urlencode(pairs)


def _headers(items: Sequence[Mapping[str, Any]] | None) -> dict[str, str]:
    return {str(kv["key"]): str(kv["values"][0]) for kv in items or [] if kv.get("values")}


class ObjectStore:
    """OpenIM 的对象存储接口（`/object/*`，imAdmin 令牌）。

    流程在第 3 部分用 probes/object-check.py 对真实实例核过：part_size → initiate_multipart_upload
    → 按签好的地址逐片 PUT（ETag 必须等于分片的 MD5）→ complete_multipart_upload；
    同样内容（同一个哈希）已存在时 initiate 不返回 upload，直接算作完成。
    下载：access_url 签出对象存储的地址再 GET（可带缩略图参数 type=image&width&height）。
    """

    def __init__(self, client: "OpenIMClient") -> None:
        self.client = client

    async def upload(self, name: str, data: bytes, content_type: str) -> None:
        size = int(
            (await self.client.call("/object/part_size", {"size": len(data)})).get("size") or 0
        )
        if size <= 0:
            raise ConnectorError(
                "UNAVAILABLE", "OpenIM 没有返回分片大小", reason="invalid_response"
            )
        parts = [data[i : i + size] for i in range(0, len(data), size)] or [b""]
        hashes = [_md5(p) for p in parts]
        init = await self.client.call(
            "/object/initiate_multipart_upload",
            {
                "hash": _md5(_PART_SEPARATOR.join(hashes).encode()),
                "size": len(data),
                "partSize": size,
                "maxParts": -1,
                "cause": "myws",
                "name": name,
                "contentType": content_type,
            },
        )
        upload = init.get("upload")
        if not upload:
            return
        sign = upload.get("sign") or {}
        for number, part in enumerate(parts, start=1):
            signed = next(
                (p for p in sign.get("parts") or [] if int(p.get("partNumber") or 0) == number),
                None,
            )
            if signed is None:
                raise ConnectorError(
                    "UNAVAILABLE", "OpenIM 没有签出分片地址", reason="invalid_response"
                )
            url = _with_query(
                str(signed.get("url") or sign.get("url") or ""),
                [*(sign.get("query") or []), *(signed.get("query") or [])],
            )
            response = await self.client.http.request(
                "PUT",
                url,
                headers={**_headers(sign.get("header")), **_headers(signed.get("header"))},
                content=part,
            )
            if response.status_code != 200:
                raise ConnectorError(
                    "UNAVAILABLE",
                    f"对象存储返回 HTTP {response.status_code}",
                    status=response.status_code,
                    reason="object_put",
                )
            if response.headers.get("ETag", "").strip('"') != hashes[number - 1]:
                raise ConnectorError("UNAVAILABLE", "分片校验不一致", reason="object_etag")
        await self.client.call(
            "/object/complete_multipart_upload",
            {
                "uploadID": upload.get("uploadID"),
                "parts": hashes,
                "name": name,
                "contentType": content_type,
                "cause": "myws",
            },
        )

    async def download(
        self, name: str, *, query: Mapping[str, str] | None = None
    ) -> tuple[bytes, str]:
        body: dict[str, Any] = {"name": name}
        if query:
            body["query"] = dict(query)
        url = str((await self.client.call("/object/access_url", body)).get("url") or "")
        if not url:
            raise ConnectorError(
                "UNAVAILABLE", "OpenIM 没有签出下载地址", reason="invalid_response"
            )
        response = await self.client.http.request("GET", url, headers={})
        if response.status_code == 404:
            raise ConnectorError("NOT_FOUND", "对象不存在", status=404, reason="object_missing")
        if response.status_code != 200:
            raise ConnectorError(
                "UNAVAILABLE",
                f"对象存储返回 HTTP {response.status_code}",
                status=response.status_code,
                reason="object_get",
            )
        return response.content, response.headers.get("Content-Type", "")


def single_conversation_id(user_a: str, user_b: str) -> str:
    """单聊会话 ID：si_<两个用户 ID 按字典序>（OC-0 实测）。"""
    first, second = sorted((user_a, user_b))
    return f"si_{first}_{second}"


def notice_conversation_id(user_a: str, user_b: str) -> str:
    """通知会话 ID：sn_<两个用户 ID 按字典序>（v3.8.3-patch.18 源码与实例实测）。"""
    first, second = sorted((user_a, user_b))
    return f"sn_{first}_{second}"


def group_conversation_id(group_id: str) -> str:
    """群聊会话 ID：sg_<群 ID>（工作群，sessionType=3）。"""
    return f"sg_{group_id}"
