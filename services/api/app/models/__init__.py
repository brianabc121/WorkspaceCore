"""ORM 模型。所有自有表放在 `workspace` schema（docs/DEVELOPMENT_SPEC.md 第 5 节）。

在这里导入全部模型，Alembic 的 target_metadata 才能看到它们。
"""

from app.models.agent import (
    AgentCa,
    AgentCredential,
    AgentDeployment,
    AgentInstance,
    AgentOpsAction,
    AgentRunStop,
    AgentSecret,
    AgentSecretAccess,
    AgentSecretGrant,
    AgentSecretVersion,
    AgentSettings,
    AgentSshJob,
    AgentSshTarget,
)
from app.models.ai import AiToolExecution
from app.models.audit import AuditLog
from app.models.base import NAMING_CONVENTION, Base
from app.models.calendar import CalendarEvent
from app.models.dashboard import Dashboard, Widget
from app.models.host import (
    AgentHostState,
    AgentInstanceSample,
    AgentServer,
    AgentServerSample,
)
from app.models.identity import AuthSession, ExternalIdentity, User
from app.models.im import (
    AgentImOutbox,
    AgentImRejection,
    AgentWakeup,
    ImAccount,
    ImContactGroup,
    ImContactGroupMember,
    ImConversation,
    ImGroup,
    ImGroupMember,
    ImMedia,
    ImMessage,
    ImMessagePin,
    ImWebhookInboxEvent,
)
from app.models.integration import (
    ExternalResource,
    IdempotencyRequest,
    Integration,
    IntegrationAccount,
    SyncCursor,
)
from app.models.issue import (
    Issue,
    IssueAssignee,
    IssueComment,
    IssueEvent,
    IssueMention,
    IssueRead,
    IssueTask,
)
from app.models.knowledge import Bookmark, KnowledgeSource, ResourceLink
from app.models.knowledge_base import (
    KnowledgeBriefRead,
    KnowledgeFile,
    KnowledgeFileRef,
    KnowledgeGuardEvent,
    KnowledgeLink,
    KnowledgeNote,
    KnowledgeNoteRevision,
    KnowledgeReadStat,
    KnowledgeSpace,
    ProjectAgent,
    StorageSettings,
)
from app.models.mail import AgentMailAccount, MailAccount, MailOAuthClient, MailSend
from app.models.model_connection import (
    AgentModelBinding,
    ModelApplyJob,
    ModelConnection,
    ModelConnectionCheck,
    ModelConnectionGrant,
    ModelConnectionModel,
)
from app.models.module import ModuleSyncAttempt, WorkspaceModule
from app.models.navigation import WorkspaceNavigation
from app.models.notification import Notification
from app.models.runner import (
    RunnerCommand,
    RunnerDeleteCondition,
    RunnerEnvironment,
    RunnerHost,
    RunnerRun,
    RunnerSnapshot,
    RunnerSshJob,
    RunnerSshTarget,
    RunnerTemplate,
)
from app.models.search import SearchDocument
from app.models.task import Project, Task, TaskResultRead
from app.models.timeline import AgentUsageCursor, AgentUsageMinute, TaskEvent, TaskRun
from app.models.workspace import Workspace, WorkspaceMember

__all__ = [
    "NAMING_CONVENTION",
    "AgentCa",
    "AgentCredential",
    "AgentDeployment",
    "AgentHostState",
    "AgentImOutbox",
    "AgentImRejection",
    "AgentInstance",
    "AgentInstanceSample",
    "AgentMailAccount",
    "AgentModelBinding",
    "AgentOpsAction",
    "AgentRunStop",
    "AgentSecret",
    "AgentSecretAccess",
    "AgentSecretGrant",
    "AgentSecretVersion",
    "AgentServer",
    "AgentServerSample",
    "AgentSettings",
    "AgentSshJob",
    "AgentSshTarget",
    "AgentUsageCursor",
    "AgentUsageMinute",
    "AgentWakeup",
    "AiToolExecution",
    "AuditLog",
    "AuthSession",
    "Base",
    "Bookmark",
    "CalendarEvent",
    "Dashboard",
    "ExternalIdentity",
    "ExternalResource",
    "IdempotencyRequest",
    "ImAccount",
    "ImContactGroup",
    "ImContactGroupMember",
    "ImConversation",
    "ImGroup",
    "ImGroupMember",
    "ImMedia",
    "ImMessage",
    "ImMessagePin",
    "ImWebhookInboxEvent",
    "Integration",
    "IntegrationAccount",
    "Issue",
    "IssueAssignee",
    "IssueComment",
    "IssueEvent",
    "IssueMention",
    "IssueRead",
    "IssueTask",
    "KnowledgeBriefRead",
    "KnowledgeFile",
    "KnowledgeFileRef",
    "KnowledgeGuardEvent",
    "KnowledgeLink",
    "KnowledgeNote",
    "KnowledgeNoteRevision",
    "KnowledgeReadStat",
    "KnowledgeSource",
    "KnowledgeSpace",
    "MailAccount",
    "MailOAuthClient",
    "MailSend",
    "ModelApplyJob",
    "ModelConnection",
    "ModelConnectionCheck",
    "ModelConnectionGrant",
    "ModelConnectionModel",
    "ModuleSyncAttempt",
    "Notification",
    "Project",
    "ProjectAgent",
    "ResourceLink",
    "RunnerCommand",
    "RunnerDeleteCondition",
    "RunnerEnvironment",
    "RunnerHost",
    "RunnerRun",
    "RunnerSnapshot",
    "RunnerSshJob",
    "RunnerSshTarget",
    "RunnerTemplate",
    "SearchDocument",
    "StorageSettings",
    "SyncCursor",
    "Task",
    "TaskEvent",
    "TaskResultRead",
    "TaskRun",
    "User",
    "Widget",
    "Workspace",
    "WorkspaceMember",
    "WorkspaceModule",
    "WorkspaceNavigation",
]

# 模块自己的表（docs/modules/README.md）：由模块注册表导入，Alembic 与测试都能看到
from app.modules.registry import import_models as _import_module_models

_import_module_models()
