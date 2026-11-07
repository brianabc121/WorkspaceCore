"""运行配置：只从环境变量读取。

连接串、密钥可能带敏感信息，用 SecretStr 保存，避免出现在日志、repr 和错误信息里。
默认值只适用于本机开发（不含密码）；部署时由 Compose / Secret 注入环境变量覆盖。
"""

import re
from functools import lru_cache
from ipaddress import IPv4Network, IPv6Network, ip_address, ip_network
from typing import Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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
    # 外网入口的服务器证书由工作台内部 CA 签发时为 true（deploy.sh connect 自动配置，T-0088）：
    # 接入包附带 CA 证书（tls/ca.pem），本机隧道用它校验入口
    agent_public_internal_ca: bool = False
    # 外网入口的主机名不在公网 DNS 里时（没有配置域名，用 agent-gateway.myworkspace.internal），
    # 接入包在本机隧道容器里把主机名映射到这个地址（工作台服务器的公网 IP）
    agent_public_address: str = ""
    # SSH 自动部署（T-0089）：默认不允许连接本机回环地址；只有测试与 CI（目标是本机的 sshd）才打开
    agent_ssh_allow_loopback: bool = False
    # 每晚复盘的 OpenClaw 定时任务（知识库方案 3.9，T-0100）：几点、按哪个时区（IANA 时区名）
    agent_reflection_cron: str = "0 22 * * *"
    agent_reflection_timezone: str = "Asia/Shanghai"

    # V2 IM（OC-3，契约第 5、7、10 节）：OpenIM 的 REST 地址（内部网络，例如 http://openim-server:10002）
    # 与 share.secret（换 imAdmin 令牌）。为空表示没有部署 OpenIM：IM 接口返回 503 IM_UNAVAILABLE
    openim_api_url: str = ""
    openim_secret: SecretStr | None = None
    openim_admin_user_id: str = "imAdmin"
    # OpenIM 回调地址 /internal/openim/<path_token>/<命令>：path_token 至少 32 个字符；
    # 只接受来自这些网段（OpenIM 所在的 Docker 网络）的回调
    openim_callback_path_token: SecretStr | None = None
    openim_callback_allowed_cidrs: str = ""
    openim_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    # OpenIM 对象存储（Garage）的地址：OpenIM 签出的上传与下载链接指向它（OpenIM 的
    # IMENV_MINIO_EXTERNALADDRESS 要设成同一个地址）。只有工作台后端访问它，不对外暴露；
    # 为空时不能上传和显示图片
    openim_object_url: str = ""
    # 没有用于发消息的上传，多少分钟后删除记录（契约第 5.4 节）
    im_upload_ttl_minutes: int = Field(default=60, ge=5, le=1440)
    # 写进图片消息、给实例取图用的地址前缀（openclaw-channel 取图时不带凭证、也不改地址）。
    # 默认：配置了外网入口时用 http://<外网域名>:<agent_tunnel_port>（外网实例经本机隧道访问，
    # 外网入口要求 Host 与 SNI 一致，所以必须是这个域名）；只有内网入口时用内网入口地址
    agent_im_object_base_url: str = ""

    # 第三方集成（Sprint 3 契约第 4.3 节）：解析到私有地址时只允许这些主机名（逗号分隔），
    # 例如独立部署的内部集成服务；其余私有、回环、链路本地地址一律拒绝（SSRF 防护）
    integration_allowed_hosts: str = ""
    connector_connect_timeout_seconds: float = Field(default=3.0, gt=0, le=30)
    connector_read_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    # 只对读请求重试（指数退避）；写请求从不自动重试，结果未知时按契约第 7 节处理
    connector_read_retries: int = Field(default=2, ge=0, le=5)
    # 最近一次成功同步超过这个时间就算 stale（契约第 10 节）
    sync_stale_after_seconds: int = Field(default=900, ge=60)
    # 周期对账：每 reconcile_interval_minutes 分钟做一次增量；距上次全量超过
    # full_sync_interval_seconds 再做一次全量（覆盖删除）；现在只有 GitHub
    reconcile_interval_minutes: int = Field(default=5, ge=1, le=30)
    full_sync_interval_seconds: int = Field(default=3600, ge=300)
    # GitHub REST API 的地址（Sprint 4 契约第 6.1 节，固定为 api.github.com）；
    # 只有测试与 CI 验收会改成模拟服务，同时要把模拟服务的主机名加进 integration_allowed_hosts
    github_api_url: str = "https://api.github.com"
    # Google OAuth 2.0 与 Gmail API 的地址（T-0128，核验记录 2026-11-06-gmail-verification.md）；
    # 只有测试与 CI 验收会改成模拟服务（同样要把主机名加进 integration_allowed_hosts）
    google_oauth_auth_url: str = "https://accounts.google.com/o/oauth2/v2/auth"
    google_oauth_token_url: str = "https://oauth2.googleapis.com/token"  # noqa: S105 - 公开地址
    google_oauth_revoke_url: str = "https://oauth2.googleapis.com/revoke"
    gmail_api_url: str = "https://gmail.googleapis.com"

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
    # IM 消息投影的保留天数（方案第 5 节“有保留期”）；OpenIM 里的消息不受影响
    im_message_retention_days: int = Field(default=365, ge=7, le=3650)

    # 知识库图片（T-0103，docs/v2/TIMELINE_PLAN.md 第 4 节）：主存储与可选副本。
    # local 存在 file_dir（Compose 里是卷 kb-files）；r2 / oss 走 S3 兼容接口
    # （自己签 SigV4，桶保持私有）。都没配置就存本地。图片一律经工作台读取，浏览器不直连对象存储
    file_storage: Literal["local", "r2", "oss"] = "local"
    file_storage_replica: Literal["none", "local", "r2", "oss"] = "none"
    file_dir: str = "/data/kb-files"
    file_max_mb: int = Field(default=10, ge=1, le=50)
    file_quota_mb: int = Field(default=2048, ge=1)
    file_storage_timeout_seconds: float = Field(default=20.0, gt=0, le=120)
    # Cloudflare R2：endpoint https://<ACCOUNT_ID>.r2.cloudflarestorage.com
    # （有辖区时加 .<JURISDICTION>），region auto，路径风格。
    # r2_endpoint / r2_region 只在测试或别的 S3 兼容服务上覆盖默认值
    r2_account_id: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: SecretStr | None = None
    r2_bucket: str = ""
    r2_jurisdiction: str = ""
    r2_endpoint: str = ""
    r2_region: str = "auto"
    # 阿里云 OSS：endpoint https://s3.oss-<region>.aliyuncs.com，只支持虚拟托管风格
    oss_region: str = ""
    oss_access_key_id: str = ""
    oss_access_key_secret: SecretStr | None = None
    oss_bucket: str = ""
    oss_endpoint: str = ""

    # 新闻早报（T-0153，docs/v2/NEWS_PLAN.md）：TrendRadar 两个容器只在内部网络 news 里。
    # trendradar_mcp_url 为空表示没有部署 TrendRadar：设置页显示“未部署”，智能体看不到 news_* 工具
    trendradar_mcp_url: str = ""
    # 生成的 TrendRadar 配置文件写到这里（Compose 的卷 trendradar-config；
    # TrendRadar 只读挂载为 /app/config）
    news_config_dir: str = "/var/lib/workspace/trendradar"
    # TrendRadar 把早报推给工作台用的地址前缀（news 网络里的 api）
    news_ingest_base_url: str = "http://api:8000"
    # 设置里填的地址（RSS、自建 NewsNow、推送 Webhook、大模型接口）解析到私有地址时只允许这些主机名
    # （逗号分隔）；只有验收用的模拟服务需要，其余私有、回环、链路本地地址与没有点的主机名一律拒绝
    news_allowed_hosts: str = ""
    news_timeout_seconds: float = Field(default=15.0, gt=0, le=60)
    # 方案 B：智能体多少分钟内没写完早报就记为失败
    news_agent_timeout_minutes: int = Field(default=90, ge=10, le=600)

    @field_validator("agent_reflection_timezone")
    @classmethod
    def _iana_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise ValueError(
                "AGENT_REFLECTION_TIMEZONE 要用 IANA 时区名，例如 Asia/Shanghai"
            ) from error
        return value

    @field_validator("agent_reflection_cron")
    @classmethod
    def _five_field_cron(cls, value: str) -> str:
        fields = value.split()
        if len(fields) != 5 or not all(re.fullmatch(r"[0-9*/,\-]+", f) for f in fields):
            raise ValueError("AGENT_REFLECTION_CRON 要是 5 段的 cron 表达式，例如 0 22 * * *")
        return value

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
        "openim_api_url",
        "trendradar_mcp_url",
        "news_ingest_base_url",
        "r2_endpoint",
        "oss_endpoint",
    )
    @classmethod
    def _http_url_or_blank(cls, value: str) -> str:
        value = value.strip()
        if value and (origin_of(value) is None or "@" in urlsplit(value).netloc):
            raise ValueError("必须是不带用户名密码的 http(s) 地址")
        return value.rstrip("/")

    @field_validator("agent_public_address")
    @classmethod
    def _ip_or_blank(cls, value: str) -> str:
        value = value.strip()
        if value:
            try:
                return str(ip_address(value))
            except ValueError as error:
                raise ValueError("必须是 IP 地址") from error
        return value

    @field_validator(
        "session_secret",
        "setup_token",
        "credential_encryption_key",
        "credential_encryption_keys_previous",
        "openim_secret",
        "openim_callback_path_token",
        "r2_secret_access_key",
        "oss_access_key_secret",
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

    @property
    def openim_callback_networks(self) -> tuple[IPv4Network | IPv6Network, ...]:
        return tuple(
            ip_network(part.strip(), strict=False)
            for part in self.openim_callback_allowed_cidrs.split(",")
            if part.strip()
        )

    @property
    def im_object_base_url(self) -> str:
        if self.agent_im_object_base_url:
            return self.agent_im_object_base_url.rstrip("/")
        public_host = urlsplit(self.agent_public_url).hostname if self.agent_public_url else None
        if public_host:
            return f"http://{public_host}:{self.agent_tunnel_port}"
        if self.agent_intranet_url:
            return self.agent_intranet_url.rstrip("/")
        return f"http://127.0.0.1:{self.agent_tunnel_port}"

    @property
    def openim_enabled(self) -> bool:
        return bool(self.openim_api_url and self.openim_secret)

    @field_validator("openim_callback_path_token")
    @classmethod
    def _path_token(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and len(value.get_secret_value()) < 32:
            raise ValueError("至少 32 个字符")
        return value

    @field_validator("agent_ingress_trusted_proxies", "openim_callback_allowed_cidrs")
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
    def allowed_news_hosts(self) -> frozenset[str]:
        return frozenset(h.strip().lower() for h in self.news_allowed_hosts.split(",") if h.strip())

    @property
    def allowed_integration_hosts(self) -> frozenset[str]:
        return frozenset(
            h.strip().lower() for h in self.integration_allowed_hosts.split(",") if h.strip()
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
