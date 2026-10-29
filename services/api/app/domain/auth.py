"""认证业务：首次安装、登录、会话校验与登出。

约定见 docs/decisions/2026-10-30-sprint-1-auth-contract.md。路由层只负责调用与设置 Cookie。
"""

import asyncio
import hmac
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ApiError
from app.domain import security
from app.domain.ratelimit import FailureLimiter
from app.models import AuthSession, User
from app.repositories import sessions as session_repo
from app.repositories import users as user_repo
from app.repositories import workspaces as workspace_repo
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
        await workspace_repo.create_with_owner(self.db, name=workspace_name, owner_id=user.id)
        token = await self._start_session(user)
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
            await asyncio.sleep(self.settings.login_failure_delay_seconds)
            raise ApiError(401, "INVALID_CREDENTIALS", "邮箱或密码错误")

        if security.needs_rehash(user.password_hash):
            user.password_hash = await asyncio.to_thread(security.hash_password, password)
        await self.limiter.clear(account_key)
        token = await self._start_session(user)
        await self.db.commit()
        return user, token

    async def authenticate(self, token: str | None) -> AuthContext:
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
        return AuthContext(user=user, session=session, token=token)

    async def logout(self, context: AuthContext) -> None:
        await session_repo.revoke(self.db, context.session, _now())
        await self.db.commit()

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
