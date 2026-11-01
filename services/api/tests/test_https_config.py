"""用域名对外提供 HTTPS 的配置（T-0065；docs/deploy-guide.md 第 5 步）：只核对仓库里的配置文件。

- 两份 Caddyfile 共用 routes.caddy，安全响应头与路由只写一处；
- HTTPS 模式保留内部的 :8080（Vikunja 的 Webhook 经 http://caddy:8080 投递），域名站点加 HSTS；
- compose.https.yaml 发布 80 / 443，域名与邮箱缺一不可；
- 恢复演练的临时环境固定只用 compose.yaml，不去占 80 / 443。
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CADDY = ROOT / "infra/caddy"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_both_caddyfiles_share_the_routes() -> None:
    routes = _read(CADDY / "routes.caddy")
    for header in ("Content-Security-Policy", "X-Frame-Options", "-Server"):
        assert header in routes
    assert "flush_interval -1" in routes  # SSE 不缓冲
    for name in ("Caddyfile", "https.Caddyfile"):
        text = _read(CADDY / name)
        assert ":8080 {\n\timport /etc/caddy/routes.caddy\n}" in text, name
        assert "reverse_proxy" not in text, f"{name} 的路由应只写在 routes.caddy 里"
        assert "http://127.0.0.1:8099" in text, f"{name} 缺少容器健康检查的站点"


def test_https_caddyfile_serves_the_domain_with_hsts() -> None:
    text = _read(CADDY / "https.Caddyfile")
    assert "auto_https off" not in text
    assert "email {$ACME_EMAIL}" in text
    site = text[text.index("{$SITE_DOMAIN} {") :]
    assert "import /etc/caddy/routes.caddy" in site
    assert 'Strict-Transport-Security "max-age=31536000; includeSubDomains"' in site
    # 默认模式不申请证书
    assert "auto_https off" in _read(CADDY / "Caddyfile")


def test_https_override_publishes_80_and_443_and_requires_domain_and_email() -> None:
    text = _read(ROOT / "compose.https.yaml")
    for port in ('"80:80"', '"443:443"', '"443:443/udp"'):
        assert port in text
    assert "SITE_DOMAIN: ${SITE_DOMAIN:?" in text
    assert "ACME_EMAIL: ${ACME_EMAIL:?" in text
    assert "./infra/caddy/https.Caddyfile:/etc/caddy/Caddyfile:ro" in text
    base = _read(ROOT / "compose.yaml")
    assert "./infra/caddy/routes.caddy:/etc/caddy/routes.caddy:ro" in base
    env = _read(ROOT / ".env.example")
    assert "# COMPOSE_FILE=compose.yaml:compose.https.yaml" in env
    assert "\nSITE_DOMAIN=\n" in env and "\nACME_EMAIL=\n" in env


def test_restore_drill_never_uses_the_https_override() -> None:
    script = _read(ROOT / "scripts/restore-verify.sh")
    block = script[script.index('cat >> "$work/restore.env"') :]
    assert "\nCOMPOSE_FILE=compose.yaml\n" in block[: block.index("\nEOF\n")]
