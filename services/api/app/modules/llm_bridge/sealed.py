"""会话桥模块加密保存的列：登记的会话桥的管理令牌。凭证轮换经模块注册表读到这里。"""

from app.domain.credential_rotation import SealedField
from app.modules.llm_bridge.models import LlmBridge
from app.modules.llm_bridge.service import secret_context

SEALED_FIELDS: tuple[SealedField, ...] = (
    SealedField(
        "llm_bridge_bridges.token_ref",
        LlmBridge,
        "token_ref",
        lambda row: secret_context(row.workspace_id, row.id),
    ),
)
