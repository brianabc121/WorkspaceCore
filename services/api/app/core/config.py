"""运行配置：只从环境变量读取。

连接串、密钥可能带敏感信息，用 SecretStr 保存，避免出现在日志、repr 和错误信息里。
默认值只适用于本机开发（不含密码）；部署时由 Compose / Secret 注入环境变量覆盖。
"""

from functools import lru_cache
from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 只用于开发与测试；生产环境必须通过 SESSION_SECRET 提供，否则启动失败
_DEV_SESSION_SECRET = "development-only-session-secret-do-not-use-in-production"  # noqa: S105


def origin_of(url: str) -> str | None:
    """取 URL 的来源（scheme://host[:port]），无法解析时返回 None。"""
    parts = urlsplit(url.strip())
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return None
    return f"{parts.scheme}://{parts.netloc}".lower()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    app_env: Literal["development", "test", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    database_url: SecretStr = SecretStr("postgresql+asyncpg://workspace@localhost:5432/workspace")
    redis_url: SecretStr = SecretStr("redis://localhost:6379/0")
    # /readyz 中每项依赖检查的超时时间
    readiness_timeout_seconds: float = Field(default=2.0, gt=0, le=10)

    # 对外访问地址与额外可信来源（逗号分隔），用于 CSRF 的 Origin / Referer 校验
    public_base_url: str = "http://localhost:8080"
    cors_allowed_origins: str = ""
    # 计算 CSRF Token 的密钥；生产环境必填
    session_secret: SecretStr | None = None
    # 首次安装凭据；未配置时首次安装接口一律拒绝
    setup_token: SecretStr | None = None
    session_ttl_days: int = Field(default=30, ge=1, le=365)
    session_idle_days: int = Field(default=7, ge=1, le=365)
    # 登录 / 首次安装失败限流（Redis 计数，固定窗口）
    login_max_failures_per_account: int = Field(default=5, ge=1)
    login_max_failures_per_ip: int = Field(default=10, ge=1)
    login_failure_window_seconds: int = Field(default=900, ge=1)
    login_failure_delay_seconds: float = Field(default=0.5, ge=0, le=5)
    # 只在 API 前面有可信反向代理（Compose 中的 Caddy）时开启，取 X-Forwarded-For 最右侧的地址
    trust_forwarded_for: bool = False

    @field_validator("log_level", mode="before")
    @classmethod
    def _normalize_log_level(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value

    @model_validator(mode="after")
    def _require_production_secrets(self) -> Self:
        if self.is_production:
            secret = self.session_secret.get_secret_value() if self.session_secret else ""
            if len(secret) < 32:
                raise ValueError("生产环境必须设置至少 32 个字符的 SESSION_SECRET")
        return self

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def session_key(self) -> bytes:
        if self.session_secret is not None:
            return self.session_secret.get_secret_value().encode()
        return _DEV_SESSION_SECRET.encode()

    @property
    def cookie_secure(self) -> bool:
        return self.is_production or self.public_base_url.lower().startswith("https://")

    @property
    def trusted_origins(self) -> frozenset[str]:
        candidates = [self.public_base_url, *self.cors_allowed_origins.split(",")]
        return frozenset(o for c in candidates if c.strip() and (o := origin_of(c)))


@lru_cache
def get_settings() -> Settings:
    return Settings()
