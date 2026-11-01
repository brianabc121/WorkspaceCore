"""MCP 工具调用与审批（V2 OC-5，契约 docs/decisions/2026-10-31-v2-oc1-contract.md 第 6、9 节）。

- **请求**（MCP 调用写工具 / 高危工具时）：按实例的 scope 与“登记人角色和实例 max_role 中较低的一个”
  检查权限，不够就记一条 denied；够就建一条 pending_approval，**不执行**。同一实例、同一工具、
  同样参数只建一条（去重）；每个实例最多 20 条待审批；15 分钟没人处理就过期。
- **审批**（owner / admin 在工作台）：If-Match 等于 version；高危操作要重新输入密码
  （失败按用户限流）。
  批准后先把状态改成 executing 并提交，再用**存下的参数**以登记人的身份执行，幂等键是记录 ID；
  任务与日程按创建审批时记下的版本执行，版本变了就失败（VERSION_CONFLICT），不覆盖别人的修改。
- 执行结果：成功 succeeded；明确失败 failed（带错误码）；请求已发出但结果未知 unknown
  （不自动重试）。
- summary 由工作台按参数生成，不采用 AI 给的文字。
"""

import hashlib
import hmac
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, ValidationError
from redis.asyncio import Redis
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ApiError
from app.domain import events
from app.domain.auth import AuthContext
from app.domain.automations import AutomationService
from app.domain.calendar import CalendarService
from app.domain.connectors import ConnectorRuntime
from app.domain.credentials import CredentialBox, CredentialError
from app.domain.im import NOTICE_USER_ID
from app.domain.knowledge import KnowledgeService
from app.domain.notifications import NotificationService, notify_admins
from app.domain.policy import WorkspaceAccess, has_role, not_found
from app.domain.ratelimit import FailureLimiter
from app.domain.security import verify_password
from app.domain.tasks import TaskService, WriteResult
from app.jobs.queue import SEND_APPROVAL_CARDS, JobQueue
from app.models import (
    AgentInstance,
    AgentSettings,
    AiToolExecution,
    Automation,
    CalendarEvent,
    ImConversation,
    ImGroup,
    ImGroupMember,
    ImMessage,
    User,
    Workspace,
    WorkspaceMember,
)
from app.schemas.ai_tools import (
    APPROVAL_TTL,
    MAX_PENDING_PER_INSTANCE,
    TOOLS,
    AiToolExecutionOut,
    AiToolExecutionPage,
    ApproveRequest,
    ExecutionSource,
    PendingApproval,
    ToolSpec,
    UpdateTaskInput,
)
from app.schemas.automations import TriggerRequest
from app.schemas.calendar import CalendarEventCreate
from app.schemas.knowledge import BookmarkCreate
from app.schemas.notifications import NotificationMark
from app.schemas.tasks import TaskCreate, TaskUpdate
from app.schemas.workspaces import Role

# 高危操作二次认证：同一用户 15 分钟内输错 5 次后暂时不能再试
STEP_UP_FAILURE_LIMIT = 5
STEP_UP_WINDOW_SECONDS = 15 * 60
logger = logging.getLogger("app.ai")

SENSITIVE_MASK = "***"
# 审批中心（前端 T-0056）里定位到这条记录
APPROVAL_LINK = "/approvals?id={id}"
# 推断 source 时只看最近这么久内收到的消息
SOURCE_WINDOW = timedelta(minutes=10)
SEALED_KEY = "$sealed"
_UNKNOWN_CODES = frozenset({"RESULT_UNKNOWN", "WRITE_UNKNOWN"})


class ToolCallError(Exception):
    """MCP 工具的错误（ToolError 的 code）。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Requester:
    """发起调用的实例，以及它以谁的身份、什么角色执行。"""

    instance: AgentInstance
    workspace: Workspace
    acting_user: User | None
    role: Role | None  # 登记人角色与实例 max_role 中较低的一个；登记人已不在工作空间时为 None

    def access(self) -> WorkspaceAccess:
        if self.role is None:
            raise ToolCallError("FORBIDDEN_ROLE", "登记这个实例的人已不在工作空间")
        return WorkspaceAccess(
            workspace=self.workspace,
            role=self.role,
            user_id=self.acting_user.id if self.acting_user else None,
        )


def _now() -> datetime:
    return datetime.now(UTC)


def arguments_hash(tool_name: str, arguments: dict[str, Any], key: bytes | None = None) -> str:
    """去重用的参数哈希。给了 key 时是 HMAC：敏感参数（邮箱、单号）的取值不能从哈希反推。"""
    canonical = json.dumps(
        {"tool": tool_name, "arguments": arguments},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()
    if key is None:
        return hashlib.sha256(canonical).hexdigest()
    return hmac.new(key, canonical, hashlib.sha256).hexdigest()


def _lower(a: Role, b: Role) -> Role:
    return a if has_role(b, a) else b


def _clip(text: str, limit: int = 60) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _not_pending() -> ApiError:
    return ApiError(409, "EXECUTION_NOT_PENDING", "这条记录已经处理过或已过期")


def _version_conflict(current: int) -> ApiError:
    return ApiError(
        409,
        "VERSION_CONFLICT",
        "内容已被其他操作修改，请刷新后重试",
        details={"current_version": current},
    )


def parse_if_match(value: str) -> int:
    return int(value.removeprefix("W/").strip('"'))


@dataclass
class _Target:
    """创建审批时查到的目标：用于 summary 与执行时的版本比对。"""

    title: str = ""
    version: str | None = None
    sensitive: tuple[str, ...] = ()


class AiToolService:
    def __init__(
        self,
        db: AsyncSession,
        settings: Settings,
        runtime: ConnectorRuntime,
        redis: Redis | None = None,
        jobs: JobQueue | None = None,
    ) -> None:
        self.db = db
        self.settings = settings
        self.runtime = runtime
        self.redis = redis
        # 有作业队列时（MCP 调用），新的待审批交给 worker 发 IM 审批卡片
        self.jobs = jobs
        try:
            self.box: CredentialBox | None = CredentialBox.from_settings(settings)
        except CredentialError:
            self.box = None
        self.hash_key = self.box.derive_key("ai_tool_arguments") if self.box else None

    # ---------------------------------------------------------------- 发起方

    async def requester(self, instance: AgentInstance) -> Requester:
        workspace = await self.db.get(Workspace, instance.workspace_id)
        if workspace is None:
            raise not_found()
        user = await self.db.get(User, instance.acting_user_id) if instance.acting_user_id else None
        role: Role | None = None
        if user is not None:
            member = await self.db.get(WorkspaceMember, (workspace.id, user.id))
            if member is not None:
                role = _lower(Role(member.role), Role(instance.max_role))
        return Requester(instance=instance, workspace=workspace, acting_user=user, role=role)

    def check(self, requester: Requester, spec: ToolSpec) -> str | None:
        """权限不够时返回错误码（FORBIDDEN_SCOPE / FORBIDDEN_ROLE），否则 None。"""
        if spec.scope == "read_write" and requester.instance.scope != "read_write":
            return "FORBIDDEN_SCOPE"
        if requester.role is None or not has_role(requester.role, spec.min_role):
            return "FORBIDDEN_ROLE"
        return None

    # ---------------------------------------------------------------- 记录

    def _row(
        self,
        requester: Requester,
        spec: ToolSpec,
        arguments: dict[str, Any],
        *,
        status: str,
        summary: str,
        display: dict[str, Any] | None = None,
        stored: dict[str, Any] | None = None,
        row_id: uuid.UUID | None = None,
        source: ExecutionSource | None = None,
        request_id: str | None = None,
    ) -> AiToolExecution:
        """arguments 用于哈希与默认展示；stored 存进 arguments 列，执行时用（只有待审批才有）。"""
        return AiToolExecution(
            id=row_id or uuid.uuid4(),
            workspace_id=requester.workspace.id,
            instance_id=requester.instance.id,
            instance_name=requester.instance.name,
            acting_user_id=requester.acting_user.id if requester.acting_user else None,
            tool_name=spec.name,
            risk_level=spec.risk,
            status=status,
            summary=summary,
            arguments=stored,
            arguments_display=display if display is not None else arguments,
            arguments_hash=arguments_hash(spec.name, arguments, self.hash_key),
            source=(
                source
                or ExecutionSource(
                    conversation_id=None,
                    message_server_id=None,
                    triggered_by_im_id=None,
                    inferred=True,
                )
            ).model_dump(),
            request_id=request_id,
            version=1,
        )

    async def record_denied(
        self,
        requester: Requester,
        spec: ToolSpec,
        arguments: dict[str, Any],
        code: str,
        *,
        source: ExecutionSource | None = None,
        request_id: str | None = None,
    ) -> None:
        row = self._row(
            requester,
            spec,
            arguments,
            status="denied",
            summary=f"拒绝调用 {spec.name}：权限不够",
            display=_display(arguments, (), mask_all=spec.name == "trigger_automation"),
            source=source,
            request_id=request_id,
        )
        row.error_code = code
        self.db.add(row)
        events.mark(self.db, requester.workspace.id, "ai")
        await self.db.commit()

    async def record_read(
        self,
        requester: Requester,
        spec: ToolSpec,
        arguments: dict[str, Any],
        *,
        ok: bool,
        error_code: str | None = None,
        source: ExecutionSource | None = None,
        request_id: str | None = None,
    ) -> None:
        row = self._row(
            requester,
            spec,
            arguments,
            status="succeeded" if ok else "failed",
            summary=f"读取：{spec.name}",
            source=source,
            request_id=request_id,
        )
        row.executed_at = _now()
        row.error_code = error_code
        self.db.add(row)
        await self.db.commit()

    # ---------------------------------------------------------------- 发起写操作（待审批）

    async def request(
        self,
        requester: Requester,
        spec: ToolSpec,
        raw: dict[str, Any],
        *,
        source: ExecutionSource | None = None,
        request_id: str | None = None,
    ) -> PendingApproval:
        """写工具 / 高危工具：只建待审批记录，不执行（A14）。"""
        try:
            parsed = spec.input_model.model_validate(raw)
        except ValidationError as error:
            raise ToolCallError("VALIDATION_FAILED", validation_message(error)) from None
        arguments = parsed.model_dump(mode="json", exclude_unset=spec.name == "update_task")
        denied = self.check(requester, spec)
        if denied is not None:
            await self.record_denied(
                requester, spec, arguments, denied, source=source, request_id=request_id
            )
            raise ToolCallError(denied, "这个实例没有权限调用这个工具")
        await self._expire_due(requester.workspace.id)
        digest = arguments_hash(spec.name, arguments, self.hash_key)
        existing = await self._pending_duplicate(requester.instance.id, spec.name, digest)
        if existing is not None:
            return _pending(existing, deduplicated=True)
        count = await self.db.scalar(
            select(func.count())
            .select_from(AiToolExecution)
            .where(
                AiToolExecution.instance_id == requester.instance.id,
                AiToolExecution.status == "pending_approval",
            )
        )
        if (count or 0) >= MAX_PENDING_PER_INSTANCE:
            raise ToolCallError("TOO_MANY_PENDING", "待审批的操作太多，请等管理员处理后再试")
        target = await self._target(requester, spec.name, parsed)
        row_id = uuid.uuid4()
        row = self._row(
            requester,
            spec,
            arguments,
            status="pending_approval",
            summary=_summary(spec.name, parsed, target),
            display=_display(arguments, target.sensitive),
            stored=self._seal(arguments, target.sensitive, row_id),
            row_id=row_id,
            source=source or await self.infer_source(requester.instance),
            request_id=request_id,
        )
        row.target_version = target.version
        row.expires_at = _now() + APPROVAL_TTL
        self.db.add(row)
        events.mark(self.db, requester.workspace.id, "ai", "im")
        await self._notify_requested(row)
        try:
            await self.db.commit()
        except IntegrityError:
            # 并发的同样请求先建好了（部分唯一索引）：返回那一条
            await self.db.rollback()
            existing = await self._pending_duplicate(requester.instance.id, spec.name, digest)
            if existing is None:
                raise
            return _pending(existing, deduplicated=True)
        await self._enqueue_cards(row.id)
        return _pending(row, deduplicated=False)

    async def _enqueue_cards(self, execution_id: uuid.UUID) -> None:
        """投递发审批卡片的作业；投递失败只记日志（通知中心里已经有 approval_requested）。"""
        if self.jobs is None:
            return
        try:
            await self.jobs.enqueue(SEND_APPROVAL_CARDS, str(execution_id))
        except Exception as error:
            logger.warning(
                "approval card job not enqueued",
                extra={"execution_id": str(execution_id), "error_type": type(error).__name__},
            )

    def _seal(
        self, arguments: dict[str, Any], sensitive: tuple[str, ...], row_id: uuid.UUID
    ) -> dict[str, Any]:
        """敏感参数的取值加密后再存（绑定到这条记录），数据库与备份里没有明文。"""
        params = arguments.get("params")
        if not sensitive or not isinstance(params, dict) or not params.keys() & set(sensitive):
            return arguments
        if self.box is None:
            raise ToolCallError("EXTERNAL_UNAVAILABLE", "未配置凭证加密密钥，不能保存敏感参数")
        box = self.box
        sealed = {
            k: {SEALED_KEY: box.seal(json.dumps(v), context=f"ai_tool_execution:{row_id}:{k}")}
            if k in sensitive
            else v
            for k, v in params.items()
        }
        return {**arguments, "params": sealed}

    def unsealed(self, row: AiToolExecution) -> dict[str, Any]:
        """执行用的参数：解开加密的敏感参数。"""
        arguments = dict(row.arguments or {})
        params = arguments.get("params")
        if not isinstance(params, dict):
            return arguments
        opened: dict[str, Any] = {}
        for k, v in params.items():
            if isinstance(v, dict) and set(v) == {SEALED_KEY}:
                if self.box is None:
                    raise ApiError(500, "NOT_CONFIGURED", "未配置凭证加密密钥")
                text = self.box.resolve(v[SEALED_KEY], context=f"ai_tool_execution:{row.id}:{k}")
                opened[k] = json.loads(text.get_secret_value())
            else:
                opened[k] = v
        return {**arguments, "params": opened}

    async def infer_source(self, instance: AgentInstance) -> ExecutionSource:
        """MCP 请求不带会话信息：取这个实例最近收到的、管理员发来的私聊或 @ 它的群消息。

        只看最近 SOURCE_WINDOW 内的消息；找不到时各字段为空，inferred 仍为 true（契约第 6 节）。
        """
        since = _now() - SOURCE_WINDOW
        singles = select(ImConversation.conversation_id).where(
            ImConversation.peer_instance_id == instance.id
        )
        groups = (
            select(func.concat("sg_", ImGroup.group_id))
            .join(ImGroupMember, ImGroupMember.group_pk == ImGroup.id)
            .where(ImGroupMember.im_user_id == instance.im_user_id)
        )
        message: ImMessage | None = await self.db.scalar(
            select(ImMessage)
            .where(
                ImMessage.workspace_id == instance.workspace_id,
                ImMessage.sent_at >= since,
                ImMessage.sender_im_id.startswith("ws_", autoescape=True),
                ImMessage.sender_im_id != NOTICE_USER_ID,
                or_(
                    ImMessage.conversation_id.in_(singles),
                    and_(
                        ImMessage.conversation_id.in_(groups),
                        ImMessage.at_im_ids.contains([instance.im_user_id]),
                    ),
                ),
            )
            .order_by(ImMessage.sent_at.desc(), ImMessage.id.desc())
            .limit(1)
        )
        if message is None:
            return ExecutionSource(
                conversation_id=None, message_server_id=None, triggered_by_im_id=None, inferred=True
            )
        return ExecutionSource(
            conversation_id=message.conversation_id,
            message_server_id=message.server_msg_id,
            triggered_by_im_id=message.sender_im_id,
            inferred=True,
        )

    async def _pending_duplicate(
        self, instance_id: uuid.UUID, tool_name: str, digest: str
    ) -> AiToolExecution | None:
        row: AiToolExecution | None = await self.db.scalar(
            select(AiToolExecution).where(
                AiToolExecution.instance_id == instance_id,
                AiToolExecution.tool_name == tool_name,
                AiToolExecution.arguments_hash == digest,
                AiToolExecution.status == "pending_approval",
            )
        )
        return row

    async def _target(self, requester: Requester, tool: str, parsed: BaseModel) -> _Target:
        access = requester.access()
        ref: dict[str, Any] = parsed.model_dump()
        try:
            if tool in ("update_task", "complete_task", "delete_task"):
                task = await TaskService(self.db, self.settings, self.runtime).get_task(
                    access, ref["task_id"]
                )
                return _Target(title=task.title, version=task.version)
            if tool == "delete_calendar_event":
                event: CalendarEvent | None = await self.db.scalar(
                    select(CalendarEvent).where(
                        CalendarEvent.id == ref["event_id"],
                        CalendarEvent.workspace_id == requester.workspace.id,
                    )
                )
                if event is None:
                    raise not_found()
                return _Target(title=event.title, version=str(event.version))
            if tool == "trigger_automation":
                automation: Automation | None = await self.db.scalar(
                    select(Automation).where(
                        Automation.id == ref["automation_id"],
                        Automation.workspace_id == requester.workspace.id,
                        Automation.deleted_at.is_(None),
                    )
                )
                if automation is None:
                    raise not_found()
                sensitive = tuple(
                    str(p.get("name")) for p in automation.params_json if p.get("sensitive")
                )
                return _Target(title=automation.name, sensitive=sensitive)
        except ApiError as error:
            if error.status_code == 404:
                raise ToolCallError("NOT_FOUND", "要操作的对象不存在") from None
            raise
        return _Target()

    # ---------------------------------------------------------------- 管理员：列表与详情

    def _visible(self, access: WorkspaceAccess) -> list[Any]:
        clauses: list[Any] = [AiToolExecution.workspace_id == access.workspace.id]
        if not has_role(access.role, Role.admin):
            clauses.append(AiToolExecution.acting_user_id == access.user_id)
        return clauses

    async def list(
        self,
        access: WorkspaceAccess,
        *,
        statuses: list[str] | None,
        risks: list[str] | None,
        instance_id: uuid.UUID | None,
        tool_name: str | None,
        cursor: str | None,
        limit: int,
    ) -> AiToolExecutionPage:
        await self._expire_due(access.workspace.id)
        clauses = self._visible(access)
        if statuses:
            clauses.append(AiToolExecution.status.in_(statuses))
        if risks:
            clauses.append(AiToolExecution.risk_level.in_(risks))
        if instance_id:
            clauses.append(AiToolExecution.instance_id == instance_id)
        if tool_name:
            clauses.append(AiToolExecution.tool_name == tool_name)
        if cursor:
            created, row_id = _decode_cursor(cursor)
            clauses.append(
                (AiToolExecution.created_at < created)
                | ((AiToolExecution.created_at == created) & (AiToolExecution.id < row_id))
            )
        rows = list(
            await self.db.scalars(
                select(AiToolExecution)
                .where(*clauses)
                .order_by(AiToolExecution.created_at.desc(), AiToolExecution.id.desc())
                .limit(limit + 1)
            )
        )
        pending = await self.pending_count(access.workspace.id)
        more = len(rows) > limit
        rows = rows[:limit]
        return AiToolExecutionPage(
            items=[to_out(row) for row in rows],
            next_cursor=_encode_cursor(rows[-1]) if more else None,
            total_estimate=None,
            pending_count=pending,
        )

    async def pending_count(self, workspace_id: uuid.UUID) -> int:
        count = await self.db.scalar(
            select(func.count())
            .select_from(AiToolExecution)
            .where(
                AiToolExecution.workspace_id == workspace_id,
                AiToolExecution.status == "pending_approval",
                AiToolExecution.expires_at > _now(),
            )
        )
        return int(count or 0)

    async def get(self, access: WorkspaceAccess, execution_id: uuid.UUID) -> AiToolExecution:
        await self._expire_due(access.workspace.id)
        row: AiToolExecution | None = await self.db.scalar(
            select(AiToolExecution).where(
                AiToolExecution.id == execution_id, *self._visible(access)
            )
        )
        if row is None:
            raise not_found()
        return row

    async def get_for_instance(
        self, instance: AgentInstance, execution_id: uuid.UUID
    ) -> AiToolExecution | None:
        """get_action_status：实例只能查自己发起的记录。"""
        await self._expire_due(instance.workspace_id)
        row: AiToolExecution | None = await self.db.scalar(
            select(AiToolExecution).where(
                AiToolExecution.id == execution_id, AiToolExecution.instance_id == instance.id
            )
        )
        return row

    # ---------------------------------------------------------------- 管理员：拒绝与批准

    async def _locked(self, access: WorkspaceAccess, execution_id: uuid.UUID) -> AiToolExecution:
        row: AiToolExecution | None = await self.db.scalar(
            select(AiToolExecution)
            .where(AiToolExecution.id == execution_id, *self._visible(access))
            .with_for_update()
        )
        if row is None:
            raise not_found()
        return row

    async def _decidable(self, row: AiToolExecution, if_match: int) -> None:
        if row.version != if_match:
            raise _version_conflict(row.version)
        if row.status != "pending_approval":
            raise _not_pending()
        if row.expires_at is not None and row.expires_at <= _now():
            row.status = "expired"
            row.arguments = None
            row.version += 1
            await self._notify_expired(row.workspace_id, row.id, row.instance_name, row.summary)
            raise _not_pending()

    # ---------------------------------------------------------------- 通知中心

    async def _notify_requested(self, row: AiToolExecution) -> None:
        """approval_requested：发给能审批的 owner / admin（契约第 6、11 节）。"""
        critical = row.risk_level == "critical"
        await notify_admins(
            self.db,
            row.workspace_id,
            type_="approval_requested",
            severity="warning" if critical else "info",
            title=_clip(f"「{row.instance_name}」请求：{row.summary}", 120),
            body=("高危操作，批准时需要重新输入密码。" if critical else "")
            + "15 分钟内没人处理会自动拒绝。",
            link=APPROVAL_LINK.format(id=row.id),
            key=str(row.id),
        )

    async def _notify_expired(
        self, workspace_id: uuid.UUID, execution_id: uuid.UUID, instance_name: str, summary: str
    ) -> None:
        await notify_admins(
            self.db,
            workspace_id,
            type_="approval_expired",
            severity="info",
            title=_clip(f"「{instance_name}」的请求已过期：{summary}", 120),
            body="15 分钟内没人处理，已自动拒绝，没有执行。",
            link=APPROVAL_LINK.format(id=execution_id),
            key=str(execution_id),
        )

    async def reject(
        self,
        context: AuthContext,
        access: WorkspaceAccess,
        execution_id: uuid.UUID,
        if_match: int,
        reason: str,
    ) -> AiToolExecution:
        row = await self._locked(access, execution_id)
        try:
            await self._decidable(row, if_match)
        except ApiError:
            await self.db.commit()
            raise
        row.status = "rejected"
        row.arguments = None
        row.decided_by = context.user.id
        row.decided_at = _now()
        row.decision_reason = reason or None
        row.version += 1
        events.mark(self.db, access.workspace.id, "ai", "im")
        await self.db.commit()
        return row

    async def approve(
        self,
        context: AuthContext,
        access: WorkspaceAccess,
        execution_id: uuid.UUID,
        if_match: int,
        body: ApproveRequest,
    ) -> AiToolExecution:
        workspace_id = access.workspace.id  # 执行失败回滚后 ORM 对象会过期，先记下
        row = await self._locked(access, execution_id)
        try:
            await self._decidable(row, if_match)
        except ApiError:
            await self.db.commit()
            raise
        if row.risk_level == "critical":
            await self._step_up(context, body)
        instance = await self.db.get(AgentInstance, row.instance_id) if row.instance_id else None
        row.status = "executing"
        row.decided_by = context.user.id
        row.decided_at = _now()
        row.version += 1
        events.mark(self.db, workspace_id, "ai", "im")
        # 先提交 executing：进程中途退出时这条记录停在 executing，不会被再次批准、重复执行
        await self.db.commit()
        status, summary, error_code = await self._execute(context, row, instance)
        # 执行失败时会回滚，记录对象随之过期；重新读出来再写结果（异步会话不能隐式加载）
        await self.db.refresh(row)
        row.status = status
        row.arguments = None  # 执行完就不再需要完整参数
        row.result_summary = summary
        row.error_code = error_code
        row.executed_at = _now()
        row.version += 1
        events.mark(self.db, workspace_id, "ai")
        await self.db.commit()
        return row

    async def _step_up(self, context: AuthContext, body: ApproveRequest) -> None:
        if body.password is None:
            raise ApiError(403, "STEP_UP_REQUIRED", "高危操作需要重新输入密码")
        limiter = (
            FailureLimiter(self.redis, STEP_UP_WINDOW_SECONDS) if self.redis is not None else None
        )
        key = f"step_up_fail:{context.user.id}"
        if limiter is not None:
            retry = await limiter.retry_after({key: STEP_UP_FAILURE_LIMIT})
            if retry is not None:
                raise ApiError(
                    429,
                    "RATE_LIMITED",
                    "密码错误次数过多，请稍后再试",
                    headers={"Retry-After": str(retry)},
                )
        if not verify_password(context.user.password_hash, body.password.get_secret_value()):
            if limiter is not None:
                await limiter.record_failure([key])
            raise ApiError(403, "STEP_UP_FAILED", "密码错误")

    async def _execute(
        self, approver: AuthContext, row: AiToolExecution, instance: AgentInstance | None
    ) -> tuple[str, str | None, str | None]:
        """以登记人的身份执行；返回 (状态, 结果说明, 错误码)。"""
        if instance is None or instance.status == "disabled":
            return "failed", "实例已停用或已删除", "AGENT_DISABLED"
        settings = await self.db.get(AgentSettings, instance.workspace_id)
        if settings is not None and not settings.agents_enabled:
            return "failed", "总开关已关闭", "AGENT_DISABLED"
        requester = await self.requester(instance)
        spec = TOOLS[row.tool_name]
        denied = self.check(requester, spec)
        if denied is not None or requester.acting_user is None:
            return "failed", "登记人或实例的权限已不够", denied or "FORBIDDEN_ROLE"
        # 服务只用到 context.user 与 client_ip；会话沿用审批人的（审计里 actor 是登记人）
        context = AuthContext(
            user=requester.acting_user,
            session=approver.session,
            token="",
            client_ip=approver.client_ip,
        )
        executor = _EXECUTORS[row.tool_name]
        try:
            summary = await executor(self, context, requester.access(), row)
        except ApiError as error:
            await self.db.rollback()
            if error.code in _UNKNOWN_CODES or error.status_code == 504:
                return "unknown", "请求已发出，结果未知；请到对应页面确认，不会自动重试", error.code
            return "failed", error.message, error.code
        return "succeeded", summary, None

    # ---------------------------------------------------------------- 过期

    async def _expire_due(self, workspace_id: uuid.UUID | None = None) -> int:
        stmt = (
            update(AiToolExecution)
            .where(
                AiToolExecution.status == "pending_approval",
                AiToolExecution.expires_at <= _now(),
            )
            .values(status="expired", arguments=None, version=AiToolExecution.version + 1)
            .returning(
                AiToolExecution.id,
                AiToolExecution.workspace_id,
                AiToolExecution.instance_name,
                AiToolExecution.summary,
            )
        )
        if workspace_id is not None:
            stmt = stmt.where(AiToolExecution.workspace_id == workspace_id)
        expired = list((await self.db.execute(stmt)).all())
        for execution_id, ws, instance_name, summary in expired:
            events.mark(self.db, ws, "ai", "im")
            await self._notify_expired(ws, execution_id, instance_name, summary)
        if expired:
            await self.db.commit()
        return len(expired)

    async def expire_due(self) -> int:
        """Worker 每分钟调用：15 分钟没人处理的待审批默认拒绝（expired）。"""
        return await self._expire_due()


# ---------------------------------------------------------------- 执行器


def _write_ok(result: WriteResult) -> None:
    if result.status_code >= 500:
        raise ApiError(504, "RESULT_UNKNOWN", "结果未知")
    if result.status_code >= 400:
        body = result.body or {}
        error = body.get("error") if isinstance(body.get("error"), dict) else {}
        raise ApiError(
            result.status_code,
            str((error or {}).get("code") or "HTTP_ERROR"),
            str((error or {}).get("message") or "执行失败"),
        )


Executor = Callable[[AiToolService, AuthContext, WorkspaceAccess, AiToolExecution], Awaitable[str]]


async def _create_task(
    s: AiToolService, ctx: AuthContext, access: WorkspaceAccess, row: AiToolExecution
) -> str:
    body = TaskCreate.model_validate(s.unsealed(row))
    result = await TaskService(s.db, s.settings, s.runtime).create_task(ctx, access, row.id, body)
    _write_ok(result)
    return f"已新建任务「{_clip(body.title)}」"


async def _update_task(
    s: AiToolService, ctx: AuthContext, access: WorkspaceAccess, row: AiToolExecution
) -> str:
    args = UpdateTaskInput.model_validate(s.unsealed(row))
    changes = args.model_dump(mode="json", exclude_unset=True)
    task_id = uuid.UUID(changes.pop("task_id"))
    body = TaskUpdate.model_validate({**changes, "version": row.target_version})
    result = await TaskService(s.db, s.settings, s.runtime).update_task(
        ctx, access, task_id, row.id, body
    )
    _write_ok(result)
    return "已修改任务"


async def _complete_task(
    s: AiToolService, ctx: AuthContext, access: WorkspaceAccess, row: AiToolExecution
) -> str:
    task_id = uuid.UUID(s.unsealed(row)["task_id"])
    body = TaskUpdate.model_validate({"version": row.target_version, "done": True})
    result = await TaskService(s.db, s.settings, s.runtime).update_task(
        ctx, access, task_id, row.id, body
    )
    _write_ok(result)
    return "已完成任务"


async def _delete_task(
    s: AiToolService, ctx: AuthContext, access: WorkspaceAccess, row: AiToolExecution
) -> str:
    task_id = uuid.UUID(s.unsealed(row)["task_id"])
    result = await TaskService(s.db, s.settings, s.runtime).delete_task(
        ctx, access, task_id, row.id, row.target_version or ""
    )
    _write_ok(result)
    return "已删除任务"


async def _create_event(
    s: AiToolService, ctx: AuthContext, access: WorkspaceAccess, row: AiToolExecution
) -> str:
    body = CalendarEventCreate.model_validate(s.unsealed(row))
    await CalendarService(s.db, s.settings).create_event(ctx, access, body)
    await s.db.commit()
    return f"已新建日程「{_clip(body.title)}」"


async def _delete_event(
    s: AiToolService, ctx: AuthContext, access: WorkspaceAccess, row: AiToolExecution
) -> str:
    event_id = uuid.UUID(s.unsealed(row)["event_id"])
    await CalendarService(s.db, s.settings).delete_event(
        ctx, access, event_id, int(row.target_version or 0)
    )
    await s.db.commit()
    return "已删除日程"


async def _create_bookmark(
    s: AiToolService, ctx: AuthContext, access: WorkspaceAccess, row: AiToolExecution
) -> str:
    body = BookmarkCreate.model_validate(s.unsealed(row))
    await KnowledgeService(s.db, s.settings).create_bookmark(ctx, access, body)
    await s.db.commit()
    return f"已新建书签「{_clip(body.title)}」"


async def _mark_read(
    s: AiToolService, ctx: AuthContext, access: WorkspaceAccess, row: AiToolExecution
) -> str:
    body = NotificationMark.model_validate({"ids": s.unsealed(row)["ids"]})
    result = await NotificationService(s.db).mark(access, body)
    await s.db.commit()
    return f"已标记 {result.updated} 条通知为已读"


async def _trigger(
    s: AiToolService, ctx: AuthContext, access: WorkspaceAccess, row: AiToolExecution
) -> str:
    arguments = s.unsealed(row)
    automation_id = uuid.UUID(arguments["automation_id"])
    body = TriggerRequest.model_validate({"params": arguments.get("params", {})})
    run = await AutomationService(s.db, s.settings, s.runtime).trigger(
        ctx, access, automation_id, row.id, body
    )
    if run.status == "unknown":
        raise ApiError(504, "RESULT_UNKNOWN", "结果未知")
    return f"已触发工作流（运行状态：{run.status}）"


_EXECUTORS: dict[str, Executor] = {
    "create_task": _create_task,
    "update_task": _update_task,
    "complete_task": _complete_task,
    "delete_task": _delete_task,
    "create_calendar_event": _create_event,
    "delete_calendar_event": _delete_event,
    "create_bookmark": _create_bookmark,
    "mark_notification_read": _mark_read,
    "trigger_automation": _trigger,
}


# ---------------------------------------------------------------- 展示


def _summary(tool: str, parsed: BaseModel, target: _Target) -> str:
    data = parsed.model_dump()
    title = _clip(target.title) if target.title else ""
    if tool == "create_task":
        return f"新建任务「{_clip(data['title'])}」"
    if tool == "update_task":
        labels = {
            "title": "标题",
            "description": "描述",
            "priority": "优先级",
            "due_at": "截止时间",
            "project_id": "项目",
        }
        changed = [labels[k] for k in parsed.model_fields_set if k in labels]
        return f"修改任务「{title}」：{'、'.join(changed)}"
    if tool == "complete_task":
        return f"完成任务「{title}」"
    if tool == "delete_task":
        return f"删除任务「{title}」"
    if tool == "create_calendar_event":
        when = data.get("start_date") or data.get("start_at")
        return f"新建日程「{_clip(data['title'])}」（{when}）"
    if tool == "delete_calendar_event":
        return f"删除日程「{title}」"
    if tool == "create_bookmark":
        return f"新建书签「{_clip(data['title'])}」"
    if tool == "mark_notification_read":
        return f"把 {len(data['ids'])} 条通知标为已读"
    if tool == "trigger_automation":
        return f"触发工作流「{title}」"
    return tool


def _display(
    arguments: dict[str, Any], sensitive: tuple[str, ...], *, mask_all: bool = False
) -> dict[str, Any]:
    """给人看的参数：敏感参数换成 ***；mask_all 时全部参数值都换掉（没查过哪些是敏感的）。"""
    params = arguments.get("params")
    if not isinstance(params, dict) or not (sensitive or mask_all):
        return arguments
    masked = {k: SENSITIVE_MASK if mask_all or k in sensitive else v for k, v in params.items()}
    return {**arguments, "params": masked}


def validation_message(error: ValidationError) -> str:
    details = error.errors()
    if not details:
        return "参数不合法"
    loc = ".".join(str(part) for part in details[0]["loc"])
    return f"参数不合法：{loc} {details[0]['msg']}".strip()


def _pending(row: AiToolExecution, *, deduplicated: bool) -> PendingApproval:
    return PendingApproval(
        action_id=row.id,
        summary=row.summary,
        expires_at=row.expires_at or row.created_at + APPROVAL_TTL,
        deduplicated=deduplicated,
    )


def to_out(row: AiToolExecution) -> AiToolExecutionOut:
    return AiToolExecutionOut(
        id=row.id,
        instance_id=row.instance_id,
        instance_name=row.instance_name,
        acting_user_id=row.acting_user_id,
        tool_name=row.tool_name,
        risk_level=row.risk_level,
        status=row.status,
        summary=row.summary,
        arguments=row.arguments_display,
        arguments_hash=row.arguments_hash,
        source=ExecutionSource.model_validate(
            row.source
            or {
                "conversation_id": None,
                "message_server_id": None,
                "triggered_by_im_id": None,
                "inferred": True,
            }
        ),
        expires_at=row.expires_at,
        decided_by=row.decided_by,
        decided_at=row.decided_at,
        decision_reason=row.decision_reason,
        executed_at=row.executed_at,
        result_summary=row.result_summary,
        error_code=row.error_code,
        request_id=row.request_id,
        version=row.version,
        created_at=row.created_at,
    )


def _encode_cursor(row: AiToolExecution) -> str:
    return f"{row.created_at.isoformat()}|{row.id}"


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        created, row_id = cursor.split("|", 1)
        return datetime.fromisoformat(created), uuid.UUID(row_id)
    except ValueError:
        raise ApiError(
            422,
            "VALIDATION_FAILED",
            "请求参数校验失败",
            details={"errors": [{"loc": ["query", "cursor"], "msg": "游标无效"}]},
        ) from None
