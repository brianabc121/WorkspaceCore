"""接入网关的 IM 部分（V2 OC-3，契约 docs/decisions/2026-10-31-v2-oc1-contract.md 第 8 节）。

实例（openclaw-channel）经接入入口访问 OpenIM，工作台在这里把关：
- **IM 令牌**：OpenIM 的用户令牌是用 share.secret 签名的 HS256 JWT（锁定源码
  openimsdk/tools `tokenverify` 与 open-im-server `pkg/authverify/token.go`），所以在本地校验签名与
  有效期；令牌是否已被踢下线仍由 OpenIM 自己判断（REST 与消息网关都会再查一次）。
- **实例**：令牌的用户必须是已启用、IM 已就绪的实例（`oc_*`，平台 5），总开关打开，
  请求来自实例登记的入口，外网入口还要同一实例的客户端证书。
- **`/im/api/{route}`**：只放行 OC-0 抓到的接口，并校验请求体里的用户等于令牌的用户；
  `/user/get_users_info` 只能查同一工作空间的 IM 账号；`/jssdk/*` 是 protobuf，按字段 1
  （ownerUserID）校验。其他一律 404，用户不符 403，响应体用 OpenIM 的格式。
- **`/im/obj/m/{media_id}`**：实例取工作台发出的图片。openclaw-channel 取图时不带任何凭证
  （插件 `src/inbound.ts`），所以按能力地址处理：随机的 media_id，只认已发出的图片。
"""

import base64
import binascii
import hashlib
import hmac
import json
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, TypeGuard

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.domain.agents import normalize_fingerprint
from app.domain.im import IM_ROLES, NOTICE_USER_ID
from app.integrations.openim import PLATFORM_WEB
from app.models import (
    AgentCredential,
    AgentInstance,
    AgentSettings,
    ImAccount,
    ImGroup,
    ImGroupMember,
    ImMedia,
    WorkspaceMember,
)

MAX_API_BODY_BYTES = 64 * 1024
# OC-0 ADR 第 5 节：插件登录同步与收发消息期间实际调用的 REST 接口
USER_ID_ROUTES = frozenset(
    {
        "/conversation/get_incremental_conversations",
        "/conversation/get_not_notify_conversation_ids",
        "/friend/get_full_friend_user_ids",
        "/friend/get_incremental_friends",
        "/group/get_full_join_group_ids",
        "/group/get_incremental_join_groups",
        "/group/get_incremental_group_members_batch",
    }
)
USERS_INFO_ROUTE = "/user/get_users_info"
# 好友申请通过 / 新的好友申请通知（工作台导入好友、补回好友时会有）到达后，SDK 会查这条申请；
# 被拦截时插件不处理这个错误，OpenClaw 进程因未处理的 Promise 拒绝而退出（T-0059 验收发现）。
# 请求体 {fromUserID, toUserID}：令牌用户必须是其中一方，另一方必须是本工作空间的 IM 用户
FRIEND_APPLY_ROUTE = "/friend/get_designated_friend_apply"
# 同样是 SDK 发消息前自己调用的（T-0059 验收发现，被拦截时消息发不出去）：
# - 单聊前查对方的好友信息 {ownerUserID, friendUserIDs}：owner 必须是令牌用户，对方都在本工作空间；
# - 群聊前查群成员信息 {groupID, userIDs}：令牌用户必须是群成员，查的也都是这个群的成员
FRIENDS_INFO_ROUTE = "/friend/get_designated_friends"
GROUP_MEMBERS_ROUTE = "/group/get_group_members_info"
PAIR_ROUTES = frozenset(
    {USERS_INFO_ROUTE, FRIEND_APPLY_ROUTE, FRIENDS_INFO_ROUTE, GROUP_MEMBERS_ROUTE}
)
PROTOBUF_ROUTES = frozenset({"/jssdk/get_active_conversations", "/jssdk/get_conversations"})


def _str_list(value: object) -> TypeGuard[list[str]]:
    return isinstance(value, list) and bool(value) and all(isinstance(v, str) for v in value)


def blocked_body() -> dict[str, Any]:
    return {
        "errCode": 1002,
        "errMsg": "NoPermissionError",
        "errDlt": "blocked by My Workspace gateway",
    }


# ---------------------------------------------------------------- IM 令牌


@dataclass(frozen=True)
class ImClaims:
    user_id: str
    platform_id: int
    expires_at: int


def _b64(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


def verify_im_token(token: str, secret: str, *, now: float | None = None) -> ImClaims | None:
    """校验 HS256 签名与有效期；格式不对、签名不符、过期一律返回 None。"""
    parts = token.split(".")
    if len(parts) != 3 or len(token) > 4096:
        return None
    try:
        header = json.loads(_b64(parts[0]))
        payload = json.loads(_b64(parts[1]))
        signature = _b64(parts[2])
    except (ValueError, binascii.Error):
        return None
    if not isinstance(header, dict) or header.get("alg") != "HS256":
        return None
    expected = hmac.new(secret.encode(), f"{parts[0]}.{parts[1]}".encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(expected, signature) or not isinstance(payload, dict):
        return None
    user_id, platform_id, expires = (
        payload.get("UserID"),
        payload.get("PlatformID"),
        payload.get("exp"),
    )
    if not isinstance(user_id, str) or not isinstance(platform_id, int):
        return None
    if not isinstance(expires, int) or expires <= (now if now is not None else time.time()):
        return None
    return ImClaims(user_id=user_id, platform_id=platform_id, expires_at=expires)


# ---------------------------------------------------------------- protobuf（只读顶层字段）


def proto_string_fields(data: bytes, field: int) -> list[str] | None:
    """取顶层某个字段（length-delimited）的全部字符串值；无法解析时返回 None。"""
    values: list[str] = []
    i = 0

    def varint() -> int | None:
        nonlocal i
        shift = result = 0
        while i < len(data) and shift < 64:
            byte = data[i]
            i += 1
            result |= (byte & 0x7F) << shift
            if not byte & 0x80:
                return result
            shift += 7
        return None

    while i < len(data):
        key = varint()
        if key is None:
            return None
        number, wire = key >> 3, key & 7
        if wire == 0:
            if varint() is None:
                return None
        elif wire == 1:
            i += 8
        elif wire == 5:
            i += 4
        elif wire == 2:
            length = varint()
            if length is None or i + length > len(data):
                return None
            chunk = data[i : i + length]
            i += length
            if number == field:
                try:
                    values.append(chunk.decode())
                except UnicodeDecodeError:
                    return None
        else:
            return None
        if i > len(data):
            return None
    return values


# ---------------------------------------------------------------- 把关


class ImGateway:
    def __init__(self, db: AsyncSession, settings: Settings) -> None:
        self.db = db
        self.settings = settings

    async def authorize(
        self, token: str | None, *, ingress: str | None, cert_fingerprint: str | None
    ) -> AgentInstance | None:
        """令牌属于这个入口上已启用的实例时返回实例，否则 None（不区分原因）。"""
        secret = self.settings.openim_secret
        if not token or secret is None or ingress not in ("intranet", "public"):
            return None
        claims = verify_im_token(token, secret.get_secret_value())
        if claims is None or claims.platform_id != PLATFORM_WEB:
            return None
        if not claims.user_id.startswith("oc_"):
            return None
        instance = await self.db.scalar(
            select(AgentInstance).where(AgentInstance.im_user_id == claims.user_id)
        )
        if instance is None or instance.status == "disabled" or not instance.im_ready:
            return None
        if instance.network_mode != ingress:
            return None
        settings = await self.db.get(AgentSettings, instance.workspace_id)
        if settings is not None and not settings.agents_enabled:
            return None
        if ingress == "public":
            if not cert_fingerprint:
                return None
            now = datetime.now(UTC)
            cert = await self.db.scalar(
                select(AgentCredential).where(
                    AgentCredential.instance_id == instance.id,
                    AgentCredential.kind == "client_cert",
                    AgentCredential.cert_fingerprint == normalize_fingerprint(cert_fingerprint),
                )
            )
            if cert is None:
                return None
            if cert.revoked_at is not None and cert.revoked_at <= now:
                return None
            if cert.expires_at is not None and cert.expires_at <= now:
                return None
        return instance

    async def workspace_im_users(self, workspace_id: uuid.UUID) -> set[str]:
        """同一工作空间能互相看到资料的 IM 账号：实例、owner / admin、系统账号。"""
        instances = await self.db.scalars(
            select(AgentInstance.im_user_id).where(AgentInstance.workspace_id == workspace_id)
        )
        admins = await self.db.scalars(
            select(ImAccount.im_user_id)
            .join(WorkspaceMember, WorkspaceMember.user_id == ImAccount.user_id)
            .where(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.role.in_(IM_ROLES),
            )
        )
        return {
            *instances.all(),
            *admins.all(),
            NOTICE_USER_ID,
            self.settings.openim_admin_user_id,
        }

    async def check_api(self, instance: AgentInstance, route: str, body: bytes) -> int | None:
        """/im/api 的白名单与用户校验：放行返回 None，否则返回 HTTP 状态码（404 / 403）。"""
        user = instance.im_user_id
        if route in PROTOBUF_ROUTES:
            owners = proto_string_fields(body, 1)
            return None if owners == [user] else 403
        if route not in USER_ID_ROUTES and route not in PAIR_ROUTES:
            return 404
        try:
            payload = json.loads(body or b"{}")
        except ValueError:
            return 403
        if not isinstance(payload, dict):
            return 403
        if route == USERS_INFO_ROUTE:
            wanted = payload.get("userIDs")
            if not isinstance(wanted, list) or not all(isinstance(u, str) for u in wanted):
                return 403
            allowed = await self.workspace_im_users(instance.workspace_id)
            return None if set(wanted) <= allowed else 403
        if route == FRIENDS_INFO_ROUTE:
            friends = payload.get("friendUserIDs")
            if payload.get("ownerUserID") != user or not _str_list(friends):
                return 403
            allowed = await self.workspace_im_users(instance.workspace_id)
            return None if set(friends) <= allowed else 403
        if route == GROUP_MEMBERS_ROUTE:
            group_id, wanted = payload.get("groupID"), payload.get("userIDs")
            if not isinstance(group_id, str) or not _str_list(wanted):
                return 403
            members = await self.group_members(instance.workspace_id, group_id)
            return None if user in members and set(wanted) <= members else 403
        if route == FRIEND_APPLY_ROUTE:
            pair = (payload.get("fromUserID"), payload.get("toUserID"))
            if not all(isinstance(u, str) for u in pair) or user not in pair:
                return 403
            other = pair[1] if pair[0] == user else pair[0]
            return None if other in await self.workspace_im_users(instance.workspace_id) else 403
        return None if payload.get("userID") == user else 403

    async def group_members(self, workspace_id: uuid.UUID, group_id: str) -> set[str]:
        """本工作空间这个群（未解散）的成员 IM ID；不是本工作空间的群返回空集合。"""
        rows = await self.db.scalars(
            select(ImGroupMember.im_user_id)
            .join(ImGroup, ImGroup.id == ImGroupMember.group_pk)
            .where(
                ImGroup.workspace_id == workspace_id,
                ImGroup.group_id == group_id,
                ImGroup.dismissed_at.is_(None),
            )
        )
        return set(rows.all())

    async def media_for_instance(self, media_id: uuid.UUID) -> ImMedia | None:
        """实例取图：只认已经发出的图片（能力地址）。"""
        media: ImMedia | None = await self.db.scalar(
            select(ImMedia).where(ImMedia.id == media_id, ImMedia.status == "used")
        )
        return media
