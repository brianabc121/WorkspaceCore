"""安全原语与相关配置（不需要数据库）。"""

import pytest
from pydantic import SecretStr
from starlette.requests import Request

from app.api.deps import client_ip
from app.core.config import Settings, origin_of
from app.domain import security
from app.domain.audit import REDACTED, redact
from app.main import create_app


def test_password_hash_round_trip() -> None:
    hashed = security.hash_password("correct horse battery staple")
    assert hashed.startswith("$argon2id$")
    assert security.verify_password(hashed, "correct horse battery staple")
    assert not security.verify_password(hashed, "wrong password")
    assert not security.verify_password("not-a-hash", "anything")
    assert not security.needs_rehash(hashed)


def test_csrf_token_is_bound_to_session_and_key() -> None:
    token = security.csrf_token_for(b"key-1", "session-a")
    assert security.csrf_token_matches(b"key-1", "session-a", token)
    assert not security.csrf_token_matches(b"key-1", "session-b", token)
    assert not security.csrf_token_matches(b"key-2", "session-a", token)


def test_session_tokens_are_random_and_stored_as_hashes() -> None:
    first, second = security.new_session_token(), security.new_session_token()
    assert first != second
    assert len(security.hash_token(first)) == 64
    assert security.hash_token(first) != first


def test_email_key_ignores_case_and_whitespace() -> None:
    assert security.email_key(" Owner@Example.com ") == security.email_key("owner@example.com")


def test_production_api_requires_a_strong_session_secret() -> None:
    with pytest.raises(RuntimeError):
        create_app(Settings(app_env="production"))
    with pytest.raises(RuntimeError):
        create_app(Settings(app_env="production", session_secret=SecretStr("too-short")))
    settings = Settings(app_env="production", session_secret=SecretStr("x" * 32))
    create_app(settings)
    assert settings.cookie_secure


def test_worker_settings_do_not_require_session_secret() -> None:
    # Worker 与 API 共用 Settings，但 Compose 不给 Worker 注入 SESSION_SECRET
    settings = Settings(app_env="production")
    assert settings.session_secret is None


def test_trusted_origins_and_origin_parsing() -> None:
    settings = Settings(
        public_base_url="https://work.example.com/app",
        cors_allowed_origins="http://localhost:5173, ,not-a-url",
    )
    assert settings.trusted_origins == {"https://work.example.com", "http://localhost:5173"}
    assert origin_of("https://Work.Example.com:8443/path?q=1") == "https://work.example.com:8443"
    assert origin_of("null") is None
    assert origin_of("javascript:alert(1)") is None


def _request(headers: dict[str, str], peer: str = "172.18.0.5") -> Request:
    return Request(
        {
            "type": "http",
            "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
            "client": (peer, 12345),
        }
    )


def test_client_ip_trusts_forwarded_for_only_when_enabled() -> None:
    forwarded = {"X-Forwarded-For": "1.1.1.1, 203.0.113.9"}
    assert client_ip(_request(forwarded), Settings()) == "172.18.0.5"
    assert client_ip(_request(forwarded), Settings(trust_forwarded_for=True)) == "203.0.113.9"


def test_ip_hash_is_keyed_and_stable() -> None:
    first = security.ip_hash(b"k" * 32, "203.0.113.7")
    assert first == security.ip_hash(b"k" * 32, "203.0.113.7")
    assert first != security.ip_hash(b"k" * 32, "203.0.113.8")
    assert first != security.ip_hash(b"x" * 32, "203.0.113.7")
    assert "203.0.113.7" not in first


def test_audit_metadata_is_redacted() -> None:
    metadata = {
        "password": "hunter2-hunter2",
        "new_password": "x",
        "Authorization": "Bearer abc",
        "api_key": "k",
        "nested": {"refresh_token": "t", "name": "ok", "deep": {"a": {"b": {"c": 1}}}},
        "list": [{"secret": "s"}, "x" * 300],
        "wrapped": SecretStr("hidden"),
        "count": 3,
        "ratio": float("nan"),
    }
    redacted = redact(metadata)
    for key in ("password", "new_password", "Authorization", "api_key", "wrapped"):
        assert redacted[key] == REDACTED
    assert redacted["nested"]["refresh_token"] == REDACTED
    assert redacted["nested"]["name"] == "ok"
    assert redacted["nested"]["deep"]["a"]["b"] == "[TRUNCATED]"
    assert redacted["list"][0] == {"secret": REDACTED}
    assert len(redacted["list"][1]) == 257
    assert redacted["count"] == 3
    assert redacted["ratio"] == "nan"
    assert "hunter2" not in repr(redacted)
