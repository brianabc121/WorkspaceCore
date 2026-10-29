"""认证业务：首次安装、登录、会话校验与管理、修改密码、个人资料。

约定见 docs/decisions/2026-10-30-sprint-1-auth-contract.md。路由层只负责调用与设置 Cookie。
安全相关的操作都写审计日志（app/domain/audit.py），与业务改动在同一个事务里提交。
"""

import asyncio
import hmac
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ApiError
from app.domain import security
from app.domain.audit import AuditTrail
from app.domain.policy import not_found
from app.domain.ratelimit import FailureLimiter
from app.models import AuthSession, User
from app.repositories import sessions as session_repo
from app.repositories import users as user_repo
from app.repositories import workspaces as workspace_repo
from app.schemas.auth import SessionOut
from app.schemas.users import MeResponse, UserOut
from app.schemas.workspaces import WorkspaceOut

# 首次安装时的事务级咨询锁，防止两个请求同时完成安装
_SETUP_LOCK_KEY = 7_020_061_001
# last_seen_at 至多每隔这么久写一次，避免每个请求都写库
_TOUCH_INTERVAL = timedelta(minutes=5)


@dataclass(frozen=True)
class AuthContext:
    user: User
    session: AuthSession
    token: str
    client_ip: str


def _now() -> datetime:
    return datetime.now(UTC)


def _unauthorized() -> ApiError:
    return ApiError(401, "UNAUTHORIZED", "未登录或会话已失效")


def _rate_limited(retry_after: int) -> ApiError:
    return ApiError(
        429,
        "RATE_LIMITED",
        "失败次数过多，请稍后再试",
        headers={"Retry-After": str(retry_after)},
    )


class AuthService:
    def __init__(self, db: AsyncSession, redis: Redis, settings: Settings) -> None:
        self.db = db
        self.settings = settings
        self.limiter = FailureLimiter(redis, settings.login_failure_window_seconds)
        self.audit = AuditTrail(db, settings)

    # ---------------------------------------------------------------- 首次安装

    async def setup_required(self) -> bool:
        return await user_repo.count(self.db) == 0

    async def complete_setup(
        self,
        *,
        setup_token: str,
        email: str,
        password: str,
        display_name: str,
        workspace_name: str,
        client_ip: str,
    ) -> tuple[User, str]:
        ip_key = f"ratelimit:setup:ip:{client_ip}"
        retry = await self.limiter.retry_after({ip_key: self.settings.login_max_failures_per_ip})
        if retry is not None:
            raise _rate_limited(retry)

        await self.db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _SETUP_LOCK_KEY})
        if not await self.setup_required():
            raise ApiError(409, "SETUP_COMPLETED", "已完成首次安装")

        expected = self.settings.setup_token
        if expected is None or not hmac.compare_digest(
            expected.get_secret_value().encode(), setup_token.encode()
        ):
            await self.db.rollback()
            await self.limiter.record_failure([ip_key])
            raise ApiError(403, "SETUP_TOKEN_INVALID", "安装凭据错误或未配置")

        password_hash = await asyncio.to_thread(security.hash_password, password)
        user = await user_repo.create(
            self.db, email=email, password_hash=password_hash, display_name=display_name
        )
        workspace = await workspace_repo.create_with_owner(
            self.db, name=workspace_name, owner_id=user.id
        )
        token = await self._start_session(user)
        await self.audit.record(
            "setup.completed",
            workspace_ids=[workspace.id],
            actor_id=user.id,
            resource_type="workspace",
            resource_id=workspace.id,
            client_ip=client_ip,
        )
        await self.db.commit()
        return user, token

    # ---------------------------------------------------------------- 登录 / 会话

    async def login(self, *, email: str, password: str, client_ip: str) -> tuple[User, str]:
        ip_key = f"ratelimit:login:ip:{client_ip}"
        account_key = f"ratelimit:login:account:{security.email_key(email)}"
        limits = {
            ip_key: self.settings.login_max_failures_per_ip,
            account_key: self.settings.login_max_failures_per_account,
        }
        retry = await self.limiter.retry_after(limits)
        if retry is not None:
            raise _rate_limited(retry)

        user = await user_repo.get_by_email(self.db, email)
        if user is None:
            await asyncio.to_thread(security.verify_dummy, password)
            valid = False
        else:
            valid = await asyncio.to_thread(security.verify_password, user.password_hash, password)

        if user is None or not valid or user.status != "active":
            await self.limiter.record_failure(limits)
            await self._audit_login_failure(user, valid=valid, client_ip=client_ip)
            await self.db.commit()
            await asyncio.sleep(self.settings.login_failure_delay_seconds)
            raise ApiError(401, "INVALID_CREDENTIALS", "邮箱或密码错误")

        if security.needs_rehash(user.password_hash):
            user.password_hash = await asyncio.to_thread(security.hash_password, password)
        await self.limiter.clear(account_key)
        token = await self._start_session(user)
        await self.audit.record_for_account(
            "auth.login_succeeded", user_id=user.id, actor_id=user.id, client_ip=client_ip
        )
        await self.db.commit()
        return user, token

    async def _audit_login_failure(self, user: User | None, *, valid: bool, client_ip: str) -> None:
        """失败原因只写进审计日志，不返回给客户端。不存在的账户不记录邮箱。"""
        if user is None:
            await self.audit.record(
                "auth.login_failed",
                workspace_ids=[None],
                metadata={"reason": "unknown_account"},
                client_ip=client_ip,
            )
            return
        reason = "invalid_password" if not valid else "account_disabled"
        await self.audit.record_for_account(
            "auth.login_failed",
            user_id=user.id,
            actor_id=None,
            metadata={"reason": reason},
            client_ip=client_ip,
        )

    async def authenticate(self, token: str | None, *, client_ip: str) -> AuthContext:
        if not token:
            raise _unauthorized()
        session = await session_repo.get_by_token_hash(self.db, security.hash_token(token))
        now = _now()
        if session is None or session.revoked_at is not None or session.expires_at <= now:
            raise _unauthorized()
        last_activity = session.last_seen_at or session.created_at
        if now - last_activity > timedelta(days=self.settings.session_idle_days):
            raise _unauthorized()
        user = await user_repo.get(self.db, session.user_id)
        if user is None or user.status != "active":
            raise _unauthorized()
        if session.last_seen_at is None or now - session.last_seen_at > _TOUCH_INTERVAL:
            session.last_seen_at = now
            await self.db.commit()
        return AuthContext(user=user, session=session, token=token, client_ip=client_ip)

    async def logout(self, context: AuthContext) -> None:
        await session_repo.revoke(self.db, context.session, _now())
        await self.audit.record_for_account(
            "auth.logout",
            user_id=context.user.id,
            actor_id=context.user.id,
            resource_type="auth_session",
            resource_id=context.session.id,
            client_ip=context.client_ip,
        )
        await self.db.commit()

    # ---------------------------------------------------------------- 会话管理

    def _idle_since(self, now: datetime) -> datetime:
        return now - timedelta(days=self.settings.session_idle_days)

    async def list_sessions(self, context: AuthContext) -> list[SessionOut]:
        now = _now()
        sessions = await session_repo.list_active_for_user(
            self.db, context.user.id, now=now, idle_since=self._idle_since(now)
        )
        return [
            SessionOut(
                id=s.id,
                created_at=s.created_at,
                last_seen_at=s.last_seen_at,
                expires_at=s.expires_at,
                current=s.id == context.session.id,
            )
            for s in sessions
        ]

    async def revoke_session(self, context: AuthContext, session_id: uuid.UUID) -> bool:
        """撤销自己的一个有效会话，返回它是否就是当前会话。别人的或已失效的会话按不存在处理。"""
        now = _now()
        session = await session_repo.get_active_for_user(
            self.db,
            session_id=session_id,
            user_id=context.user.id,
            now=now,
            idle_since=self._idle_since(now),
        )
        if session is None:
            raise not_found()
        await session_repo.revoke(self.db, session, now)
        current = session.id == context.session.id
        await self.audit.record_for_account(
            "auth.session_revoked",
            user_id=context.user.id,
            actor_id=context.user.id,
            resource_type="auth_session",
            resource_id=session.id,
            metadata={"current": current},
            client_ip=context.client_ip,
        )
        await self.db.commit()
        return current

    async def change_password(
        self, context: AuthContext, *, current_password: str, new_password: str
    ) -> str:
        """校验当前密码后更换密码，撤销该用户全部会话并签发新会话，返回新的会话 Token。"""
        user = context.user
        key = f"ratelimit:password:user:{user.id}"
        limits = {key: self.settings.login_max_failures_per_account}
        retry = await self.limiter.retry_after(limits)
        if retry is not None:
            raise _rate_limited(retry)

        if not await asyncio.to_thread(
            security.verify_password, user.password_hash, current_password
        ):
            await self.limiter.record_failure(limits)
            await self.audit.record_for_account(
                "auth.password_change_failed",
                user_id=user.id,
                actor_id=user.id,
                client_ip=context.client_ip,
            )
            await self.db.commit()
            await asyncio.sleep(self.settings.login_failure_delay_seconds)
            raise ApiError(400, "INVALID_CURRENT_PASSWORD", "当前密码错误")

        user.password_hash = await asyncio.to_thread(security.hash_password, new_password)
        revoked = await session_repo.revoke_all_for_user(self.db, user.id, _now())
        token = await self._start_session(user)
        await self.limiter.clear(key)
        await self.audit.record_for_account(
            "auth.password_changed",
            user_id=user.id,
            actor_id=user.id,
            metadata={"revoked_sessions": revoked},
            client_ip=context.client_ip,
        )
        await self.db.commit()
        return token

    async def _start_session(self, user: User) -> str:
        token = security.new_session_token()
        await session_repo.create(
            self.db,
            user_id=user.id,
            token_hash=security.hash_token(token),
            expires_at=_now() + timedelta(days=self.settings.session_ttl_days),
        )
        return token

    # ---------------------------------------------------------------- 当前用户

    async def workspaces(self, user: User) -> list[WorkspaceOut]:
        rows = await workspace_repo.list_for_user(self.db, user.id)
        return [
            WorkspaceOut(
                id=w.id, name=w.name, role=role, created_at=w.created_at, updated_at=w.updated_at
            )
            for w, role in rows
        ]

    async def me(self, user: User) -> MeResponse:
        return MeResponse(
            user=UserOut.model_validate(user, from_attributes=True),
            workspaces=await self.workspaces(user),
        )

    async def update_profile(self, context: AuthContext, *, display_name: str) -> UserOut:
        user = context.user
        if user.display_name != display_name:
            old = user.display_name
            user.display_name = display_name
            await self.db.flush()
            await self.audit.record_for_account(
                "user.updated",
                user_id=user.id,
                actor_id=user.id,
                metadata={"display_name": {"old": old, "new": display_name}},
                client_ip=context.client_ip,
            )
            await self.db.commit()
        return UserOut.model_validate(user, from_attributes=True)
