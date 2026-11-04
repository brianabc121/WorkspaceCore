"""二次认证：高危操作前再输一次自己的登录密码（V2 OC-5；T-0114 的运维动作共用）。

连续输错 5 次锁 15 分钟（按用户计，审批与运维动作共用一个计数）。
"""

from pydantic import SecretStr
from redis.asyncio import Redis

from app.core.errors import ApiError
from app.domain.ratelimit import FailureLimiter
from app.domain.security import verify_password
from app.models import User

STEP_UP_FAILURE_LIMIT = 5
STEP_UP_WINDOW_SECONDS = 15 * 60


async def verify_step_up(
    redis: Redis | None,
    user: User,
    password: SecretStr | None,
    *,
    message: str = "高危操作需要重新输入密码",
) -> None:
    """没带密码 403 STEP_UP_REQUIRED；密码错误 403 STEP_UP_FAILED；错太多次 429。"""
    if password is None:
        raise ApiError(403, "STEP_UP_REQUIRED", message)
    limiter = FailureLimiter(redis, STEP_UP_WINDOW_SECONDS) if redis is not None else None
    key = f"step_up_fail:{user.id}"
    if limiter is not None:
        retry = await limiter.retry_after({key: STEP_UP_FAILURE_LIMIT})
        if retry is not None:
            raise ApiError(
                429,
                "RATE_LIMITED",
                "密码错误次数过多，请稍后再试",
                headers={"Retry-After": str(retry)},
            )
    if not verify_password(user.password_hash, password.get_secret_value()):
        if limiter is not None:
            await limiter.record_failure([key])
        raise ApiError(403, "STEP_UP_FAILED", "密码错误")
