"""运行配置：只从环境变量读取。

连接串、密钥可能带敏感信息，用 SecretStr 保存，避免出现在日志、repr 和错误信息里。
默认值只适用于本机开发（不含密码）；部署时由 Compose / Secret 注入环境变量覆盖。
"""

from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 只用于开发与测试；生产环境必须通过 SESSION_SECRET 提供，否则 API 拒绝启动
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
    # 第三方连接凭证的加密密钥：64 位十六进制（32 字节），make env 会生成
    credential_encryption_key: SecretStr | None = None
    session_ttl_days: int = Field(default=30, ge=1, le=365)
    session_idle_days: int = Field(default=7, ge=1, le=365)
    # 登录 / 首次安装失败限流（Redis 计数，固定窗口）
    login_max_failures_per_account: int = Field(default=5, ge=1)
    login_max_failures_per_ip: int = Field(default=10, ge=1)
    login_failure_window_seconds: int = Field(default=900, ge=1)
    login_failure_delay_seconds: float = Field(default=0.5, ge=0, le=5)
    # 首页聚合接口的总耗时上限（各模块另有自己的超时）
    dashboard_overview_timeout_seconds: float = Field(default=3.0, gt=0, le=10)
    # 只在 API 前面有可信反向代理（Compose 中的 Caddy）时开启，取 X-Forwarded-For 最右侧的地址
    trust_forwarded_for: bool = False

    # 第三方集成（Sprint 3 契约第 4.3 节）：解析到私有地址时只允许这些主机名（逗号分隔），
    # 例如 Compose 中独立部署的 vikunja；其余私有、回环、链路本地地址一律拒绝（SSRF 防护）
    integration_allowed_hosts: str = ""
    connector_connect_timeout_seconds: float = Field(default=3.0, gt=0, le=30)
    connector_read_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    # 只对读请求重试（指数退避）；写请求从不自动重试，结果未知时按契约第 7 节处理
    connector_read_retries: int = Field(default=2, ge=0, le=5)
    # 最近一次成功同步超过这个时间就算 stale（契约第 10 节）
    sync_stale_after_seconds: int = Field(default=900, ge=60)

    @field_validator("log_level", mode="before")
    @classmethod
    def _normalize_log_level(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value

    @field_validator("session_secret", "setup_token", "credential_encryption_key", mode="before")
    @classmethod
    def _blank_as_unset(cls, value: object) -> object:
        # Compose 的 ${X:-} 在变量缺失时传入空字符串，按“未配置”处理
        return None if isinstance(value, str) and not value.strip() else value

    def require_api_secrets(self) -> None:
        """API 启动时调用：生产环境必须提供足够长的 SESSION_SECRET。

        不放在字段校验里：Worker 也读取这份配置，但用不到会话密钥，Compose 也不给它注入。
        """
        if self.is_production:
            secret = self.session_secret.get_secret_value() if self.session_secret else ""
            if len(secret) < 32:
                raise RuntimeError("生产环境必须设置至少 32 个字符的 SESSION_SECRET")

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

    @property
    def allowed_integration_hosts(self) -> frozenset[str]:
        return frozenset(
            h.strip().lower() for h in self.integration_allowed_hosts.split(",") if h.strip()
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
