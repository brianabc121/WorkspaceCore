"""OpenClaw 服务器的登录密码（T-0178）：加密保存、按记录绑定用途、只在登录时解密。

依据 docs/decisions/2026-11-09-agent-server-password-auth.md（取代 2026-11-03 ADR 里
“一次密码后改用专用密钥”的部分）与 docs/v2/agent-servers-redesign/ 的接口与迁移约定第 2 节：

- 密文存在 `agent_ssh_targets.login_password_ref`（`enc:v1:` 引用），加密用途
  `agent_server_password:<目标 id>`：密文挪到另一台服务器的记录上解不开；
- 只接受 `enc:v1:` 引用：`env:` 引用是部署 Secret，不是用户保存的密码，不能当密码用；
- `credential_revision` 每次设置 / 替换密码加 1；后台任务记下开始时的版本，登录前核对，
  密码中途被换过就停止（不拿新密码去完成旧的意图）；主密钥轮换只重新加密，不改版本；
- 错误是稳定的领域错误（错误码 + 连接状态里的类别），由调用方转换成接口错误或检测结果；
  错误信息不含明文、密文或密钥。
"""

import uuid

from pydantic import SecretStr

from app.core.errors import ApiError
from app.domain.credentials import CredentialBox, CredentialError
from app.models import AgentSshTarget

_ENCRYPTED_PREFIX = "enc:v1:"

CREDENTIAL_REQUIRED = "AGENT_SERVER_CREDENTIAL_REQUIRED"
CREDENTIAL_UNREADABLE = "AGENT_SERVER_CREDENTIAL_UNREADABLE"
CREDENTIAL_CHANGED = "AGENT_SERVER_CREDENTIAL_CHANGED"


def agent_server_password_context(target_id: uuid.UUID) -> str:
    """登录密码的加密用途：绑定到服务器的 SSH 记录。"""
    return f"agent_server_password:{target_id}"


class ServerCredentialError(Exception):
    """保存的登录密码不能用于登录。

    code：稳定的错误码；reason：写进连接状态的类别（credentials_required / credential_unreadable /
    credential_changed）；message：给人看的说明。
    """

    def __init__(self, code: str, reason: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.reason = reason
        self.message = message

    def api(self) -> ApiError:
        return ApiError(409, self.code, self.message, details={"reason": self.reason})


def credential_required() -> ServerCredentialError:
    return ServerCredentialError(
        CREDENTIAL_REQUIRED,
        "credentials_required",
        "这台服务器还没有保存登录密码：先补充登录密码",
    )


def credential_unreadable() -> ServerCredentialError:
    return ServerCredentialError(
        CREDENTIAL_UNREADABLE,
        "credential_unreadable",
        "保存的登录密码无法解密（加密密钥不对或密文损坏）：请重新填写登录密码",
    )


def credential_changed() -> ServerCredentialError:
    return ServerCredentialError(
        CREDENTIAL_CHANGED,
        "credential_changed",
        "服务器的登录密码已被替换：请刷新后重试",
    )


def seal_server_password(box: CredentialBox, target_id: uuid.UUID, password: SecretStr) -> str:
    """加密登录密码（原样保存，不去掉首尾空格）。"""
    return box.seal(password.get_secret_value(), context=agent_server_password_context(target_id))


def resolve_server_password(
    box: CredentialBox, target: AgentSshTarget, *, expected_revision: int | None = None
) -> SecretStr:
    """解密这台服务器保存的登录密码。

    expected_revision：调用方开始操作时读到的凭证版本；对不上说明密码已被替换，
    抛 credential_changed。
    """
    if expected_revision is not None and target.credential_revision != expected_revision:
        raise credential_changed()
    ref = target.login_password_ref
    if ref is None:
        raise credential_required()
    if not ref.startswith(_ENCRYPTED_PREFIX):
        raise credential_unreadable()
    try:
        return box.resolve(ref, context=agent_server_password_context(target.id))
    except CredentialError as error:
        raise credential_unreadable() from error


def password_readable(box: CredentialBox | None, ref: str | None) -> bool:
    """不解密，只判断保存的密文所用的密钥是否在当前配置里（读接口用，不碰明文）。

    密文损坏或用途不匹配要到真正解密（连接检测、后台任务）时才发现。
    """
    if box is None or ref is None or not ref.startswith(_ENCRYPTED_PREFIX):
        return False
    key_id = box.key_id_in(ref)
    return key_id == box.key_id or key_id in box.previous_key_ids
