"""凭证加密与引用（S1-04），不需要数据库。"""

import secrets

import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.domain.credentials import CredentialBox, CredentialError, parse_key
from app.main import create_app

KEY = secrets.token_hex(32)
CONTEXT = "integration_account:7b0c"


@pytest.fixture
def box() -> CredentialBox:
    return CredentialBox(parse_key(KEY))


def test_seal_and_resolve_round_trip(box: CredentialBox) -> None:
    ref = box.seal("ghp_example-token", context=CONTEXT)
    assert ref.startswith(f"enc:v1:{box.key_id}:")
    assert "ghp_example-token" not in ref
    assert box.resolve(ref, context=CONTEXT).get_secret_value() == "ghp_example-token"
    # 每次加密使用新的随机 nonce
    assert box.seal("ghp_example-token", context=CONTEXT) != ref


def test_ciphertext_is_bound_to_its_context(box: CredentialBox) -> None:
    ref = box.seal("secret", context=CONTEXT)
    with pytest.raises(CredentialError, match="用途不匹配"):
        box.resolve(ref, context="integration_account:other")


def test_tampered_ciphertext_is_rejected(box: CredentialBox) -> None:
    ref = box.seal("secret", context=CONTEXT)
    flipped = ref[:-2] + ("A" if ref[-2] != "A" else "B") + ref[-1]
    with pytest.raises(CredentialError):
        box.resolve(flipped, context=CONTEXT)
    with pytest.raises(CredentialError, match="格式无效"):
        box.resolve(f"enc:v1:{box.key_id}:AAAA", context=CONTEXT)


def test_wrong_key_is_reported_without_leaking_material(box: CredentialBox) -> None:
    ref = box.seal("secret", context=CONTEXT)
    other = CredentialBox(parse_key(secrets.token_hex(32)))
    with pytest.raises(CredentialError, match="另一个密钥") as excinfo:
        other.resolve(ref, context=CONTEXT)
    assert KEY not in str(excinfo.value)
    assert ref not in str(excinfo.value)


@pytest.mark.parametrize("value", ["", "not-hex", "ab" * 16, "ab" * 33])
def test_invalid_keys_are_rejected(value: str) -> None:
    with pytest.raises(CredentialError, match="64 位十六进制"):
        parse_key(value)


def test_box_requires_a_configured_key() -> None:
    with pytest.raises(CredentialError, match="未配置"):
        CredentialBox.from_settings(Settings(app_env="test"))
    configured = Settings(app_env="test", credential_encryption_key=SecretStr(KEY))
    assert CredentialBox.from_settings(configured).key_id == CredentialBox(parse_key(KEY)).key_id


def test_environment_references(box: CredentialBox) -> None:
    environ = {"WS_SECRET_GITHUB": "from-deploy-secret", "SESSION_SECRET": "app-secret"}
    resolved = box.resolve("env:WS_SECRET_GITHUB", context=CONTEXT, environ=environ)
    assert resolved.get_secret_value() == "from-deploy-secret"
    # 只允许 WS_SECRET_ 前缀，不能借引用读取工作台自身的密钥
    with pytest.raises(CredentialError, match="WS_SECRET_"):
        box.resolve("env:SESSION_SECRET", context=CONTEXT, environ=environ)
    with pytest.raises(CredentialError, match="未设置"):
        box.resolve("env:WS_SECRET_MISSING", context=CONTEXT, environ=environ)
    with pytest.raises(CredentialError, match="不支持"):
        box.resolve("plain-text-token", context=CONTEXT, environ=environ)


def test_api_refuses_to_start_with_a_malformed_key() -> None:
    with pytest.raises(CredentialError):
        create_app(Settings(app_env="test", credential_encryption_key=SecretStr("not-hex")))
    # Compose 在变量缺失时传入空字符串：按未配置处理，不影响启动
    assert Settings(app_env="test", credential_encryption_key="").credential_encryption_key is None
    create_app(Settings(app_env="test", credential_encryption_key=SecretStr(KEY)))
