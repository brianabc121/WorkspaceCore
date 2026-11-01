"""ORM 模型。所有自有表放在 `workspace` schema（docs/DEVELOPMENT_SPEC.md 第 5 节）。

在这里导入全部模型，Alembic 的 target_metadata 才能看到它们。
"""

from app.models.agent import (
    AgentCa,
    AgentCredential,
    AgentInstance,
    AgentSecret,
    AgentSecretAccess,
    AgentSecretGrant,
    AgentSecretVersion,
    AgentSettings,
)
from app.models.audit import AuditLog
from app.models.automation import Automation, AutomationConnection, AutomationRun
from app.models.base import NAMING_CONVENTION, Base
from app.models.calendar import CalendarEvent
from app.models.dashboard import Dashboard, Widget
from app.models.identity import AuthSession, ExternalIdentity, User
from app.models.im import ImAccount, ImGroup, ImGroupMember, ImWebhookInboxEvent
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
    "AgentCa",
    "AgentCredential",
    "AgentInstance",
    "AgentSecret",
    "AgentSecretAccess",
    "AgentSecretGrant",
    "AgentSecretVersion",
    "AgentSettings",
    "AuditLog",
    "AuthSession",
    "Automation",
    "AutomationConnection",
    "AutomationRun",
    "Base",
    "Bookmark",
    "CalendarEvent",
    "Dashboard",
    "ExternalIdentity",
    "ExternalResource",
    "IdempotencyRequest",
    "ImAccount",
    "ImGroup",
    "ImGroupMember",
    "ImWebhookInboxEvent",
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
