"""新闻早报加密保存的列：大模型 Key、推送凭证、接收 token（主键是 workspace_id）。

凭证轮换（app/domain/credential_rotation.py）经模块注册表读到这里。
"""

from app.domain.credential_rotation import SealedField
from app.modules.news.models import NewsSettings
from app.modules.news.service import secret_context

SEALED_FIELDS: tuple[SealedField, ...] = (
    SealedField(
        "news_settings.ai_key_ref",
        NewsSettings,
        "ai_key_ref",
        lambda row: secret_context(row.workspace_id, "ai_key"),
    ),
    SealedField(
        "news_settings.channel_secrets_ref",
        NewsSettings,
        "channel_secrets_ref",
        lambda row: secret_context(row.workspace_id, "channels"),
    ),
    SealedField(
        "news_settings.ingest_token_ref",
        NewsSettings,
        "ingest_token_ref",
        lambda row: secret_context(row.workspace_id, "ingest"),
    ),
)
