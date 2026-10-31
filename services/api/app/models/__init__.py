"""ORM 模型。所有自有表放在 `workspace` schema（docs/DEVELOPMENT_SPEC.md 第 5 节）。

在这里导入全部模型，Alembic 的 target_metadata 才能看到它们。
"""

from app.models.audit import AuditLog
from app.models.base import NAMING_CONVENTION, Base
from app.models.calendar import CalendarEvent
from app.models.dashboard import Dashboard, Widget
from app.models.identity import AuthSession, ExternalIdentity, User
from app.models.integration import (
    ExternalResource,
    IdempotencyRequest,
    Integration,
    IntegrationAccount,
    SyncCursor,
    WebhookInboxEvent,
)
from app.models.knowledge import Bookmark, KnowledgeSource, ResourceLink
from app.models.notification import Notification
from app.models.search import SearchDocument
from app.models.workspace import Workspace, WorkspaceMember

__all__ = [
    "NAMING_CONVENTION",
    "AuditLog",
    "AuthSession",
    "Base",
    "Bookmark",
    "CalendarEvent",
    "Dashboard",
    "ExternalIdentity",
    "ExternalResource",
    "IdempotencyRequest",
    "Integration",
    "IntegrationAccount",
    "KnowledgeSource",
    "Notification",
    "ResourceLink",
    "SearchDocument",
    "SyncCursor",
    "User",
    "WebhookInboxEvent",
    "Widget",
    "Workspace",
    "WorkspaceMember",
]
