"""密码哈希、会话 Token 与 CSRF Token。

约定见 docs/decisions/2026-10-30-sprint-1-auth-contract.md 第 3、4、9 节。

Argon2 计算较重，调用方应通过 asyncio.to_thread 执行，避免阻塞事件循环。
"""

import base64
import hashlib
import hmac
import secrets
from functools import cache

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

# Argon2id，RFC 9106 推荐的低内存配置：t=3、m=64 MiB、p=4（参数变化时旧哈希会在登录时自动升级）
_hasher = PasswordHasher(time_cost=3, memory_cost=64 * 1024, parallelism=4)


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


@cache
def _dummy_hash() -> str:
    return _hasher.hash(secrets.token_urlsafe(16))


def verify_dummy(password: str) -> None:
    """账户不存在时也做一次同等代价的校验，使响应时间与密码错误时一致。"""
    verify_password(_dummy_hash(), password)


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def csrf_token_for(key: bytes, session_token: str) -> str:
    digest = hmac.new(key, session_token.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def csrf_token_matches(key: bytes, session_token: str, candidate: str) -> bool:
    return hmac.compare_digest(csrf_token_for(key, session_token), candidate)


def email_key(email: str) -> str:
    """限流计数用的账户键：不在 Redis 里保存明文邮箱。"""
    return hashlib.sha256(email.strip().lower().encode()).hexdigest()
