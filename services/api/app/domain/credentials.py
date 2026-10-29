"""第三方连接凭证的加密与引用（S1-04；规范第 5.1 节第 7 项、第 9.3 节；契约 ADR 第 12 节）。

数据库里只保存“凭证引用”字符串，不保存明文。引用有两种形式：

- `enc:v1:<key_id>:<base64url(nonce + 密文)>`：用 CREDENTIAL_ENCRYPTION_KEY 做 AES-256-GCM 加密。
  加密时绑定用途（context，例如 `integration_account:<id>`），密文被挪到别的记录上会解密失败。
  `key_id` 由密钥派生，用来区分“密钥不对”和“密文被篡改”，也为以后轮换密钥留出位置。
- `env:WS_SECRET_<名称>`：部署 Secret，运行时从环境变量读取，数据库里没有任何密钥内容。
  只允许 `WS_SECRET_` 前缀，避免通过引用读到 SESSION_SECRET 等工作台自身的密钥。

错误信息不包含明文、密文或密钥。
"""

import base64
import binascii
import hashlib
import os
import re
from collections.abc import Mapping

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import SecretStr

from app.core.config import Settings

_ENCRYPTED_PREFIX = "enc:v1:"
_ENV_PREFIX = "env:"
_ENV_NAME = re.compile(r"WS_SECRET_[A-Z0-9_]{1,100}")
_NONCE_BYTES = 12
_KEY_BYTES = 32


class CredentialError(Exception):
    """凭证无法加密、解密或解析。"""


def parse_key(value: str) -> bytes:
    """CREDENTIAL_ENCRYPTION_KEY 是 32 字节的十六进制表示（64 个字符）。"""
    try:
        key = bytes.fromhex(value.strip())
    except ValueError:
        raise CredentialError("CREDENTIAL_ENCRYPTION_KEY 必须是 64 位十六进制") from None
    if len(key) != _KEY_BYTES:
        raise CredentialError("CREDENTIAL_ENCRYPTION_KEY 必须是 64 位十六进制")
    return key


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class CredentialBox:
    def __init__(self, key: bytes) -> None:
        if len(key) != _KEY_BYTES:
            raise CredentialError("凭证加密密钥长度必须是 32 字节")
        self._aead = AESGCM(key)
        self.key_id = hashlib.sha256(b"workspace-credential-key\x00" + key).hexdigest()[:8]

    @classmethod
    def from_settings(cls, settings: Settings) -> "CredentialBox":
        if settings.credential_encryption_key is None:
            raise CredentialError("未配置 CREDENTIAL_ENCRYPTION_KEY")
        return cls(parse_key(settings.credential_encryption_key.get_secret_value()))

    def seal(self, plaintext: str, *, context: str) -> str:
        """加密明文，返回可以存进数据库的引用。"""
        nonce = os.urandom(_NONCE_BYTES)
        sealed = self._aead.encrypt(nonce, plaintext.encode(), context.encode())
        return f"{_ENCRYPTED_PREFIX}{self.key_id}:{_b64encode(nonce + sealed)}"

    def resolve(
        self, ref: str, *, context: str, environ: Mapping[str, str] | None = None
    ) -> SecretStr:
        """把数据库里的引用还原成凭证。"""
        if ref.startswith(_ENCRYPTED_PREFIX):
            return SecretStr(self._open(ref[len(_ENCRYPTED_PREFIX) :], context))
        if ref.startswith(_ENV_PREFIX):
            name = ref[len(_ENV_PREFIX) :]
            if not _ENV_NAME.fullmatch(name):
                raise CredentialError("环境变量引用必须是 env:WS_SECRET_<名称>")
            value = (os.environ if environ is None else environ).get(name)
            if not value:
                raise CredentialError(f"环境变量 {name} 未设置")
            return SecretStr(value)
        raise CredentialError("不支持的凭证引用格式")

    def _open(self, body: str, context: str) -> str:
        key_id, _, payload = body.partition(":")
        if key_id != self.key_id:
            raise CredentialError("凭证是用另一个密钥加密的，请检查 CREDENTIAL_ENCRYPTION_KEY")
        try:
            data = _b64decode(payload)
        except (ValueError, binascii.Error):
            raise CredentialError("凭证密文格式无效") from None
        if len(data) <= _NONCE_BYTES:
            raise CredentialError("凭证密文格式无效")
        try:
            plaintext = self._aead.decrypt(
                data[:_NONCE_BYTES], data[_NONCE_BYTES:], context.encode()
            )
        except InvalidTag:
            raise CredentialError("凭证解密失败：密文被篡改或用途不匹配") from None
        return plaintext.decode()
