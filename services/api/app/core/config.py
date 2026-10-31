"""运行配置：只从环境变量读取。

连接串、密钥可能带敏感信息，用 SecretStr 保存，避免出现在日志、repr 和错误信息里。
默认值只适用于本机开发（不含密码）；部署时由 Compose / Secret 注入环境变量覆盖。
"""

from functools import lru_cache
from ipaddress import IPv4Network, IPv6Network, ip_network
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
    # 轮换期间保留的旧密钥（逗号分隔，同样是 64 位十六进制）：只用于解密，
    # `python -m app.commands.rotate_credentials` 把密文全部换成当前密钥之后再删掉
    credential_encryption_keys_previous: SecretStr | None = None
    session_ttl_days: int = Field(default=30, ge=1, le=365)
    session_idle_days: int = Field(default=7, ge=1, le=365)
    # 登录 / 首次安装失败限流（Redis 计数，固定窗口）
    login_max_failures_per_account: int = Field(default=5, ge=1)
    login_max_failures_per_ip: int = Field(default=10, ge=1)
    login_failure_window_seconds: int = Field(default=900, ge=1)
    login_failure_delay_seconds: float = Field(default=0.5, ge=0, le=5)
    # 首页聚合接口的总耗时上限（各模块另有自己的超时）
    dashboard_overview_timeout_seconds: float = Field(default=3.0, gt=0, le=10)
    # 每个进程同时进行的首页聚合数：每次聚合有约 5 个模块各占一个数据库连接，
    # 超出的请求排队等候，排队的时间不计入模块的超时（T-0044 性能基准）
    dashboard_overview_concurrency: int = Field(default=3, ge=1, le=50)
    # 每个进程的数据库连接池（api 与 worker 各一个）：常驻连接数与高峰时额外允许的连接数
    database_pool_size: int = Field(default=10, ge=1, le=100)
    database_max_overflow: int = Field(default=10, ge=0, le=100)
    # 只在 API 前面有可信反向代理（Compose 中的 Caddy）时开启，取 X-Forwarded-For 最右侧的地址
    trust_forwarded_for: bool = False
    # V2 实例接口：接入入口 Caddy 的地址（逗号分隔的 IP 或网段）。只有直接来自这些地址的请求，
    # 才采用 Caddy 加的 X-MyWS-Ingress 与 X-MyWS-Client-Cert-SHA256；其他来源（包括同一网络里的
    # 其他容器）带这两个请求头也不算。为空时实例接口一律 401
    agent_ingress_trusted_proxies: str = ""
    # 接入包里写给 OpenClaw 的地址：内网入口与外网入口的基础地址（例如 https://10.0.0.5、
    # https://agent.example.com）；没有配置的入口不能生成接入包。外网模式经本机隧道访问，
    # 接入包里写的是 http://127.0.0.1:<agent_tunnel_port>
    agent_intranet_url: str = ""
    agent_public_url: str = ""
    agent_tunnel_port: int = Field(default=18443, ge=1024, le=65535)

    # 第三方集成（Sprint 3 契约第 4.3 节）：解析到私有地址时只允许这些主机名（逗号分隔），
    # 例如 Compose 中独立部署的 vikunja；其余私有、回环、链路本地地址一律拒绝（SSRF 防护）
    integration_allowed_hosts: str = ""
    connector_connect_timeout_seconds: float = Field(default=3.0, gt=0, le=30)
    connector_read_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    # 只对读请求重试（指数退避）；写请求从不自动重试，结果未知时按契约第 7 节处理
    connector_read_retries: int = Field(default=2, ge=0, le=5)
    # 最近一次成功同步超过这个时间就算 stale（契约第 10 节）
    sync_stale_after_seconds: int = Field(default=900, ge=60)
    # Vikunja 投递 Webhook 用的工作台地址（Compose 中为 http://caddy:8080）；
    # 为空时不自动注册 Webhook，只靠周期对账（契约第 9 节）
    webhook_base_url: str = ""
    # Webhook 事件处理失败的最多尝试次数，超过后进 dead_letter（规范第 10 节）
    webhook_max_attempts: int = Field(default=8, ge=1, le=50)
    # 周期对账：每 reconcile_interval_minutes 分钟做一次增量；距上次全量超过
    # full_sync_interval_seconds 再做一次全量（覆盖删除与丢失的事件）
    reconcile_interval_minutes: int = Field(default=5, ge=1, le=30)
    full_sync_interval_seconds: int = Field(default=3600, ge=300)
    # GitHub REST API 的地址（Sprint 4 契约第 6.1 节，固定为 api.github.com）；
    # 只有测试与 CI 验收会改成模拟服务，同时要把模拟服务的主机名加进 integration_allowed_hosts
    github_api_url: str = "https://api.github.com"

    # 运维（Sprint 5 契约第 3 节）：都在内部网络，由部署配置，浏览器拿不到这些地址。
    # 为空表示没有部署对应组件（docker compose --profile monitoring 启用）
    prometheus_url: str = ""
    grafana_url: str = ""
    # 浏览器打开 Grafana 仪表盘的地址（外链），与上面的内部地址分开
    grafana_dashboard_url: str = ""
    monitoring_timeout_seconds: float = Field(default=3.0, gt=0, le=30)
    # Worker 检查 api 是否就绪用的内部地址（Compose 中为 http://api:8000）；为空时不检查
    api_internal_url: str = ""
    # 服务状态检查结果的缓存时间；Worker 心跳超过这个时间没有更新就算 down
    service_status_cache_seconds: float = Field(default=15.0, ge=0, le=300)
    worker_heartbeat_stale_seconds: int = Field(default=180, ge=60, le=3600)
    # 访问日志里的路径：path 为实际路径（不含查询串）；route 只记录路由模板，连路径参数也不记录
    log_request_path: Literal["path", "route"] = "path"

    # SSE（Sprint 5 契约第 5 节）：每个 api 进程的连接数上限、单个连接的最长时间（到期后浏览器
    # 带着 Last-Event-ID 自动重连）、多久复核一次会话与成员身份
    sse_max_connections: int = Field(default=200, ge=1, le=10_000)
    sse_max_connections_per_user: int = Field(default=5, ge=1, le=100)
    sse_max_seconds: float = Field(default=600, ge=1, le=3600)
    sse_recheck_seconds: float = Field(default=60, ge=0.1, le=600)
    sse_block_seconds: float = Field(default=10, ge=0.1, le=30)
    # 通知的保留天数：已读的超过这个时间删除，未读的保留两倍时间
    notification_retention_days: int = Field(default=90, ge=1, le=3650)

    @field_validator("log_level", mode="before")
    @classmethod
    def _normalize_log_level(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value

    @field_validator(
        "prometheus_url",
        "grafana_url",
        "grafana_dashboard_url",
        "api_internal_url",
        "agent_intranet_url",
        "agent_public_url",
    )
    @classmethod
    def _http_url_or_blank(cls, value: str) -> str:
        value = value.strip()
        if value and (origin_of(value) is None or "@" in urlsplit(value).netloc):
            raise ValueError("必须是不带用户名密码的 http(s) 地址")
        return value.rstrip("/")

    @field_validator(
        "session_secret",
        "setup_token",
        "credential_encryption_key",
        "credential_encryption_keys_previous",
        mode="before",
    )
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
    def agent_ingress_proxy_networks(self) -> tuple[IPv4Network | IPv6Network, ...]:
        return tuple(
            ip_network(part.strip(), strict=False)
            for part in self.agent_ingress_trusted_proxies.split(",")
            if part.strip()
        )

    @field_validator("agent_ingress_trusted_proxies")
    @classmethod
    def _networks(cls, value: str) -> str:
        for part in value.split(","):
            if part.strip():
                try:
                    ip_network(part.strip(), strict=False)
                except ValueError:
                    raise ValueError("必须是逗号分隔的 IP 地址或网段") from None
        return value

    @property
    def allowed_integration_hosts(self) -> frozenset[str]:
        return frozenset(
            h.strip().lower() for h in self.integration_allowed_hosts.split(",") if h.strip()
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
