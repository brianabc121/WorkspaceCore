"""IM 账号与好友（V2 OC-3，契约 docs/decisions/2026-10-31-v2-oc1-contract.md 第 5.1、7 节）。

工作台后端以 imAdmin 身份调用 OpenIM（连接器 app/integrations/openim.py）：
- 管理员（owner / admin）的账号 `ws_<用户 UUID 十六进制>` 按需注册，登记在 im_accounts；
- 实例的账号 `oc_<实例 UUID 十六进制>` 在登记实例时注册，并与本工作空间的 owner / admin
  互加好友；全部成功后 im_ready = true。失败时保持 false，由 Worker 定时重试，
  实例启动时取 IM 令牌也会再试一次；
- 停用实例、关闭总开关时强制下线（尽力而为：回调与接入网关还会按状态拒绝）。
- 好友关系只增不减：工作台没有删除好友的功能（方案第 4.4 节）。
"""

import logging
import uuid
from collections.abc import Iterable, Sequence

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ApiError
from app.domain.connectors import ConnectorRuntime
from app.integrations.base import ConnectorError
from app.integrations.http import ConnectorHttp, OutboundPolicy, host_of
from app.integrations.openim import OpenIMClient, TokenCache
from app.models import AgentInstance, ImAccount, User, WorkspaceMember

logger = logging.getLogger("app.im")

# 系统通知账号：发审批卡片与系统提示（契约第 5.1 节）
NOTICE_USER_ID = "ws_notice"
NOTICE_NICKNAME = "系统通知"
IM_ROLES = ("owner", "admin")
# OpenIM 的昵称没有明确上限；与工作台的名称上限保持一致，避免超长
_NICKNAME_MAX = 64


def admin_im_user_id(user_id: uuid.UUID) -> str:
    return f"ws_{user_id.hex}"


def user_id_from_im(im_user_id: str) -> uuid.UUID | None:
    """`ws_<32 位十六进制>` → 用户 ID；其他格式（包括 ws_notice）返回 None。"""
    if not im_user_id.startswith("ws_") or len(im_user_id) != 35:
        return None
    try:
        return uuid.UUID(hex=im_user_id[3:])
    except ValueError:
        return None


def im_unavailable() -> ApiError:
    return ApiError(503, "IM_UNAVAILABLE", "IM 服务暂时不可用，请稍后重试")


def openim_client(
    settings: Settings, runtime: ConnectorRuntime, cache: TokenCache | None
) -> OpenIMClient | None:
    """没有部署 OpenIM（未配置地址或 secret）时返回 None。

    OpenIM 在内部网络，地址是部署配置的，所以只允许这一个主机名；熔断状态与其他连接器共用
    （按 "openim" 计数），测试替换的 transport 也一并沿用。
    """
    if not settings.openim_enabled or settings.openim_secret is None:
        return None
    http = ConnectorHttp(
        policy=OutboundPolicy(frozenset({host_of(settings.openim_api_url)})),
        connect_timeout=settings.openim_timeout_seconds,
        read_timeout=settings.openim_timeout_seconds,
        read_retries=0,
        breaker=runtime.breaker,
        transport=runtime.transport,
        breaker_scope="openim",
    )
    return OpenIMClient(
        http,
        settings.openim_api_url.rstrip("/"),
        settings.openim_secret.get_secret_value(),
        admin_user_id=settings.openim_admin_user_id,
        cache=cache,
    )


def _nickname(value: str, fallback: str) -> str:
    return (value.strip() or fallback)[:_NICKNAME_MAX]


class ImAccounts:
    """账号登记、好友与下线。数据库写入在调用方的事务里，由调用方提交。"""

    def __init__(self, db: AsyncSession, client: OpenIMClient) -> None:
        self.db = db
        self.client = client

    async def _register(self, im_user_id: str, nickname: str) -> None:
        try:
            await self.client.register_users([(im_user_id, nickname)])
        except ConnectorError as error:
            # 已注册（1102）：之前的尝试注册成功但没来得及登记，照常继续
            if error.kind != "CONFLICT":
                raise

    async def ensure_user(self, user: User) -> str:
        """管理员的 OpenIM 账号；第一次需要时注册并登记。"""
        existing = await self.db.get(ImAccount, user.id)
        if existing is not None:
            return existing.im_user_id
        im_user_id = admin_im_user_id(user.id)
        await self._register(im_user_id, _nickname(user.display_name, user.email))
        await self.db.execute(
            pg_insert(ImAccount)
            .values(user_id=user.id, im_user_id=im_user_id)
            .on_conflict_do_nothing()
        )
        return im_user_id

    async def ensure_notice_user(self) -> str:
        await self._register(NOTICE_USER_ID, NOTICE_NICKNAME)
        return NOTICE_USER_ID

    async def workspace_admins(self, workspace_id: uuid.UUID) -> list[User]:
        """能用 IM 的成员：本工作空间里状态正常的 owner / admin。"""
        rows = await self.db.scalars(
            select(User)
            .join(WorkspaceMember, WorkspaceMember.user_id == User.id)
            .where(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.role.in_(IM_ROLES),
                User.status == "active",
            )
            .order_by(User.created_at, User.id)
        )
        return list(rows.all())

    async def befriend(self, owner: str, others: Iterable[str]) -> list[str]:
        """owner 与 others 互加好友，跳过已是好友的（OC-0 F2：重复导入会多一条通知）。"""
        missing = [other for other in others if not await self.client.is_friend(owner, other)]
        if missing:
            try:
                await self.client.import_friends(owner, missing)
            except ConnectorError as error:
                if error.kind != "CONFLICT":
                    raise
        return missing

    async def provision_instance(self, instance: AgentInstance) -> None:
        """注册实例账号，与本工作空间的 owner / admin 互加好友；成功后 im_ready = true。

        出错时抛出 ConnectorError，im_ready 保持原值；重复调用是安全的。
        """
        await self._register(instance.im_user_id, _nickname(instance.im_nickname, instance.name))
        admins = [
            await self.ensure_user(user)
            for user in await self.workspace_admins(instance.workspace_id)
        ]
        await self.befriend(instance.im_user_id, admins)
        instance.im_ready = True

    async def force_logout(self, im_user_ids: Sequence[str]) -> int:
        """强制下线；单个失败只记日志，返回成功的个数。"""
        done = 0
        for im_user_id in im_user_ids:
            try:
                await self.client.force_logout(im_user_id)
                done += 1
            except ConnectorError as error:
                logger.warning(
                    "openim force logout failed",
                    extra={"im_user_id": im_user_id, "error_kind": error.kind},
                )
        return done


async def provision_pending(db: AsyncSession, client: OpenIMClient, *, limit: int = 50) -> int:
    """Worker 定时任务：IM 尚未就绪、没有停用的实例再试一次。返回这次就绪的个数。"""
    instances = (
        await db.scalars(
            select(AgentInstance)
            .where(AgentInstance.im_ready.is_(False), AgentInstance.status != "disabled")
            .order_by(AgentInstance.created_at)
            .limit(limit)
        )
    ).all()
    accounts = ImAccounts(db, client)
    ready = 0
    for instance in instances:
        try:
            await accounts.provision_instance(instance)
        except ConnectorError as error:
            logger.warning(
                "openim provisioning failed",
                extra={"instance_id": str(instance.id), "error_kind": error.kind},
            )
            # 后面的实例多半也会失败（OpenIM 不可用）：等下一轮
            await db.rollback()
            break
        await db.commit()
        ready += 1
    return ready
