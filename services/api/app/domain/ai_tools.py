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

import base64
import hashlib
import hmac
import json
import logging
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, ValidationError
from redis.asyncio import Redis
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ApiError
from app.domain import events
from app.domain.auth import AuthContext
from app.domain.calendar import CalendarService
from app.domain.connectors import ConnectorRuntime
from app.domain.credentials import CredentialBox, CredentialError
from app.domain.im import NOTICE_USER_ID, user_id_from_im
from app.domain.knowledge import KnowledgeService
from app.domain.notifications import NotificationService, notify_admins
from app.domain.policy import WorkspaceAccess, has_role, not_found
from app.domain.step_up import verify_step_up
from app.domain.tasks import TaskService, WriteResult
from app.jobs.queue import SEND_APPROVAL_CARDS, JobQueue
from app.models import (
    AgentInstance,
    AgentSettings,
    AiToolExecution,
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
    EXPIRING_WINDOW,
    MAX_PENDING_PER_INSTANCE,
    TOOLS,
    AiExecutionSummaryOut,
    AiToolExecutionOut,
    AiToolExecutionPage,
    ApproveRequest,
    ExecutionInstanceOption,
    ExecutionPeriod,
    ExecutionSort,
    ExecutionSource,
    PendingApproval,
    ToolSpec,
    UpdateTaskInput,
)
from app.schemas.calendar import CalendarEventCreate
from app.schemas.knowledge import BookmarkCreate
from app.schemas.notifications import NotificationMark
from app.schemas.tasks import TaskCreate, TaskUpdate
from app.schemas.workspaces import Role

logger = logging.getLogger("app.ai")

# 审批中心（前端 T-0056）里定位到这条记录
APPROVAL_LINK = "/approvals?id={id}"
# 推断 source 时只看最近这么久内收到的消息
SOURCE_WINDOW = timedelta(minutes=10)
SEALED_KEY = "$sealed"
_UNKNOWN_CODES = frozenset({"RESULT_UNKNOWN", "WRITE_UNKNOWN"})
# 审批中心的“执行异常”：执行了但失败或结果未知；rejected / expired / denied 没有执行，不算
EXCEPTION_STATUSES = ("failed", "unknown")
# 时间范围按 created_at 往回数；all 不限
_PERIODS: dict[str, timedelta] = {"24h": timedelta(hours=24), "7d": timedelta(days=7)}
# 绑定了条件的游标前缀；没有前缀的是旧格式（创建时间|ID）
_CURSOR_V2 = "v2."


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
        # 校验用：轮换凭证密钥前发起的待审批，哈希是用旧密钥算的
        self.hash_keys: list[bytes | None] = (
            list(self.box.derive_keys("ai_tool_arguments")) if self.box else [None]
        )

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
            display=arguments,
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

    async def record_knowledge(
        self,
        requester: Requester,
        spec: ToolSpec,
        arguments: dict[str, Any],
        *,
        ok: bool,
        summary: str,
        error_code: str | None = None,
        request_id: str | None = None,
    ) -> None:
        """知识库工具（风险等级 knowledge，直接执行、不审批；docs/v2/KNOWLEDGE_PLAN.md 3.7）。"""
        row = self._row(
            requester,
            spec,
            arguments,
            status="succeeded" if ok else "failed",
            summary=summary,
            request_id=request_id,
        )
        row.executed_at = _now()
        row.error_code = error_code
        self.db.add(row)
        if spec.risk in ("knowledge", "direct"):
            events.mark(self.db, requester.workspace.id, "ai")
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
            stored=arguments,
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

    def arguments_intact(self, row: AiToolExecution) -> bool:
        """存下的参数与发起时算出的哈希一致：数据库里的参数被改过就不执行。

        配置了凭证密钥时哈希是 HMAC，密钥不在数据库里，只改数据库伪造不出匹配的哈希。
        """
        try:
            arguments = self.unsealed(row)
        except (ApiError, CredentialError, ValueError):
            return False
        return any(
            hmac.compare_digest(arguments_hash(row.tool_name, arguments, key), row.arguments_hash)
            for key in self.hash_keys
        )

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
        sender_id = user_id_from_im(message.sender_im_id)
        sender = await self.db.get(User, sender_id) if sender_id is not None else None
        return ExecutionSource(
            conversation_id=message.conversation_id,
            message_server_id=message.server_msg_id,
            triggered_by_im_id=message.sender_im_id,
            triggered_by_name=(sender.display_name or sender.email) if sender else None,
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
        except ApiError as error:
            if error.status_code == 404:
                raise ToolCallError("NOT_FOUND", "要操作的对象不存在") from None
            raise
        return _Target()

    # ---------------------------------------------------------------- 管理员：列表、汇总与详情

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
        period: ExecutionPeriod = "all",
        sort: ExecutionSort = "created_desc",
    ) -> AiToolExecutionPage:
        """调用记录（契约第 2.2 节）。

        - period 按 created_at、相对服务端当前时间计算，翻页之间窗口随请求时刻移动，不冻结快照；
        - sort=expires_asc：expires_at 升序、空值最后，同分再按 created_at、id 升序；
        - 游标绑定空间、请求的用户与可见范围、筛选、时间范围与排序，条件不符 422；
          旧格式游标只在不带新条件（period=all、sort=created_desc）时可用；
        - pending_count 是可见范围内未过期的待审批总数，不随本次筛选变化。
        """
        await self._expire_due(access.workspace.id)
        now = _now()
        clauses = [
            *self._visible(access),
            *_since(period, now),
            *_filters(statuses=statuses, risks=risks, instance_id=instance_id, tool_name=tool_name),
        ]
        binding = _binding(
            access,
            statuses=statuses,
            risks=risks,
            instance_id=instance_id,
            tool_name=tool_name,
            period=period,
            sort=sort,
        )
        if cursor:
            legacy = period == "all" and sort == "created_desc"
            clauses.append(_after(_decode_cursor(cursor, binding, legacy=legacy), sort))
        order = (
            (
                AiToolExecution.expires_at.asc().nulls_last(),
                AiToolExecution.created_at.asc(),
                AiToolExecution.id.asc(),
            )
            if sort == "expires_asc"
            else (AiToolExecution.created_at.desc(), AiToolExecution.id.desc())
        )
        rows = list(
            await self.db.scalars(
                select(AiToolExecution).where(*clauses).order_by(*order).limit(limit + 1)
            )
        )
        pending = await self.db.scalar(
            select(func.count())
            .select_from(AiToolExecution)
            .where(*self._visible(access), _pending_at(now))
        )
        more = len(rows) > limit
        rows = rows[:limit]
        return AiToolExecutionPage(
            items=[to_out(row) for row in rows],
            next_cursor=_encode_cursor(rows[-1], binding) if more else None,
            total_estimate=None,
            pending_count=int(pending or 0),
        )

    async def summary(
        self,
        access: WorkspaceAccess,
        *,
        period: ExecutionPeriod,
        instance_id: uuid.UUID | None,
        risks: Sequence[str] | None,  # 类里的 list 是上面的方法名，这里用 Sequence
        tool_name: str | None,
    ) -> AiExecutionSummaryOut:
        """审批中心的汇总卡片与筛选项（契约第 2.1 节）。

        - 只读：不写库、不调用 _expire_due、不投递作业；已到期但还没被清理的待审批
          用 expires_at > as_of 显式排除。
        - 范围是 _visible（owner / admin 整个工作空间，member / viewer 只算以自己身份执行的）
          加 period。period 按 created_at 同样作用于四个计数：待审批只有 15 分钟有效期，
          24h / 7d 实际上包含全部有效待审批，卡片口径统一为“此期间发起的调用”。
        - 计数再叠加 instance / risk / tool 条件；筛选项只用可见范围 + period，不被已选条件锁死。
          实例选项只取 ID 仍在的记录，名称是该 ID 最近一条记录的名称；被删实例（ID 为 null）
          的记录照常计数，但不进选项，也不会把它的名称算到别的实例上。
        - 全部用 SQL 聚合，不数列表的某一页。
        """
        now = _now()
        scope = [*self._visible(access), *_since(period, now)]
        pending = _pending_at(now)
        pending_count, expiring, critical, exceptions = (
            await self.db.execute(
                select(
                    func.count().filter(pending),
                    func.count().filter(
                        pending, AiToolExecution.expires_at <= now + EXPIRING_WINDOW
                    ),
                    func.count().filter(pending, AiToolExecution.risk_level == "critical"),
                    func.count().filter(AiToolExecution.status.in_(EXCEPTION_STATUSES)),
                ).where(
                    *scope,
                    *_filters(
                        statuses=None, risks=risks, instance_id=instance_id, tool_name=tool_name
                    ),
                )
            )
        ).one()
        latest = (
            select(AiToolExecution.instance_id, AiToolExecution.instance_name)
            .where(*scope, AiToolExecution.instance_id.is_not(None))
            .ext(distinct_on(AiToolExecution.instance_id))
            .order_by(
                AiToolExecution.instance_id,
                AiToolExecution.created_at.desc(),
                AiToolExecution.id.desc(),
            )
        )
        instances = [
            ExecutionInstanceOption(id=instance, name=name)
            for instance, name in (await self.db.execute(latest)).all()
            if instance is not None
        ]
        tools = await self.db.scalars(select(AiToolExecution.tool_name).where(*scope).distinct())
        return AiExecutionSummaryOut(
            pending_count=pending_count,
            expiring_count=expiring,
            critical_pending_count=critical,
            exception_count=exceptions,
            as_of=now,
            period=period,
            expiring_window_seconds=int(EXPIRING_WINDOW.total_seconds()),
            # 排序在 Python 里做：不依赖数据库的排序规则
            instances=sorted(instances, key=lambda option: (option.name, str(option.id))),
            tools=sorted(tools),
        )

    async def pending_count(self, workspace_id: uuid.UUID) -> int:
        """整个工作空间未过期的待审批数（MCP 首页概况用；审批中心按可见范围另算）。"""
        count = await self.db.scalar(
            select(func.count())
            .select_from(AiToolExecution)
            .where(AiToolExecution.workspace_id == workspace_id, _pending_at(_now()))
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
        await verify_step_up(self.redis, context.user, body.password)

    async def _execute(
        self, approver: AuthContext, row: AiToolExecution, instance: AgentInstance | None
    ) -> tuple[str, str | None, str | None]:
        """以登记人的身份执行；返回 (状态, 结果说明, 错误码)。"""
        spec = TOOLS.get(row.tool_name)
        executor = _EXECUTORS.get(row.tool_name)
        if spec is None or executor is None:
            return "failed", "这个工具已不可用，没有执行", "NOT_FOUND"
        if not self.arguments_intact(row):
            logger.warning(
                "ai tool arguments do not match their hash", extra={"execution_id": str(row.id)}
            )
            return "failed", "参数与发起时不一致，没有执行；请让实例重新发起", "ARGUMENTS_TAMPERED"
        if instance is None or instance.status == "disabled":
            return "failed", "实例已停用或已删除", "AGENT_DISABLED"
        settings = await self.db.get(AgentSettings, instance.workspace_id)
        if settings is not None and not settings.agents_enabled:
            return "failed", "总开关已关闭", "AGENT_DISABLED"
        requester = await self.requester(instance)
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
    # AI 改的完成状态：审批人还没看过结果，不替他记“已查看”（T-0166）
    result = await TaskService(s.db, s.settings, s.runtime).update_task(
        ctx, access, task_id, row.id, body, self_viewed=False
    )
    _write_ok(result)
    return "已修改任务"


async def _complete_task(
    s: AiToolService, ctx: AuthContext, access: WorkspaceAccess, row: AiToolExecution
) -> str:
    task_id = uuid.UUID(s.unsealed(row)["task_id"])
    body = TaskUpdate.model_validate({"version": row.target_version, "done": True})
    result = await TaskService(s.db, s.settings, s.runtime).update_task(
        ctx, access, task_id, row.id, body, self_viewed=False
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


_EXECUTORS: dict[str, Executor] = {
    "create_task": _create_task,
    "update_task": _update_task,
    "complete_task": _complete_task,
    "delete_task": _delete_task,
    "create_calendar_event": _create_event,
    "delete_calendar_event": _delete_event,
    "create_bookmark": _create_bookmark,
    "mark_notification_read": _mark_read,
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
    return tool


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


def _pending_at(now: datetime) -> Any:
    """有效的待审批：还没到期（expires_at = now 已经算到期）。"""
    return and_(AiToolExecution.status == "pending_approval", AiToolExecution.expires_at > now)


def _since(period: ExecutionPeriod, now: datetime) -> list[Any]:
    """时间范围：created_at 不早于 now 减去窗口（含边界）；all 不限。

    不设上界：数据库时钟略快时，刚发起的调用 created_at 可能比 now 晚一点，也要算进来。
    """
    window = _PERIODS.get(period)
    return [] if window is None else [AiToolExecution.created_at >= now - window]


def _filters(
    *,
    statuses: Sequence[str] | None,
    risks: Sequence[str] | None,
    instance_id: uuid.UUID | None,
    tool_name: str | None,
) -> list[Any]:
    clauses: list[Any] = []
    if statuses:
        clauses.append(AiToolExecution.status.in_(statuses))
    if risks:
        clauses.append(AiToolExecution.risk_level.in_(risks))
    if instance_id:
        clauses.append(AiToolExecution.instance_id == instance_id)
    if tool_name:
        clauses.append(AiToolExecution.tool_name == tool_name)
    return clauses


@dataclass(frozen=True)
class _Position:
    """游标指向的上一页最后一条。"""

    created_at: datetime
    id: uuid.UUID
    expires_at: datetime | None = None


def _binding(
    access: WorkspaceAccess,
    *,
    statuses: Sequence[str] | None,
    risks: Sequence[str] | None,
    instance_id: uuid.UUID | None,
    tool_name: str | None,
    period: ExecutionPeriod,
    sort: ExecutionSort,
) -> str:
    """游标绑定的条件摘要。只用来发现“换了条件还拿旧游标”，不是授权凭据：可见范围每次都重新校验。"""
    canonical = json.dumps(
        {
            "workspace": str(access.workspace.id),
            "user": str(access.user_id),
            "all": has_role(access.role, Role.admin),
            "status": sorted(set(statuses or [])),
            "risk": sorted(set(risks or [])),
            "instance": str(instance_id) if instance_id else None,
            "tool": tool_name,
            "period": period,
            "sort": sort,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()[:32]


def _encode_cursor(row: AiToolExecution, binding: str) -> str:
    payload = {
        "c": row.created_at.isoformat(),
        "i": str(row.id),
        "e": row.expires_at.isoformat() if row.expires_at else None,
        "b": binding,
    }
    raw = json.dumps(payload, separators=(",", ":")).encode()
    return _CURSOR_V2 + base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _decode_cursor(cursor: str, binding: str, *, legacy: bool) -> _Position:
    if not cursor.startswith(_CURSOR_V2):
        if not legacy:
            raise _cursor_error("游标与当前的时间范围或排序不符，请从第一页重新加载")
        try:
            created, row_id = cursor.split("|", 1)
            return _Position(
                created_at=_aware(datetime.fromisoformat(created)), id=uuid.UUID(row_id)
            )
        except ValueError:
            raise _cursor_error("游标无效") from None
    try:
        body = cursor.removeprefix(_CURSOR_V2)
        data = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        position = _Position(
            created_at=_aware(datetime.fromisoformat(data["c"])),
            id=uuid.UUID(data["i"]),
            expires_at=_aware(datetime.fromisoformat(data["e"])) if data["e"] else None,
        )
        bound = data["b"]
    except (ValueError, KeyError, TypeError):
        raise _cursor_error("游标无效") from None
    if bound != binding:
        raise _cursor_error("筛选、排序或可见范围变了，旧的游标不能再用：请从第一页重新加载")
    return position


def _after(position: _Position, sort: ExecutionSort) -> Any:
    """keyset：排在游标那一条之后的记录。"""
    if sort == "created_desc":
        return or_(
            AiToolExecution.created_at < position.created_at,
            and_(
                AiToolExecution.created_at == position.created_at,
                AiToolExecution.id < position.id,
            ),
        )
    later = or_(
        AiToolExecution.created_at > position.created_at,
        and_(AiToolExecution.created_at == position.created_at, AiToolExecution.id > position.id),
    )
    if position.expires_at is None:
        return and_(AiToolExecution.expires_at.is_(None), later)
    return or_(
        AiToolExecution.expires_at > position.expires_at,
        and_(AiToolExecution.expires_at == position.expires_at, later),
        AiToolExecution.expires_at.is_(None),
    )


def _cursor_error(message: str) -> ApiError:
    return ApiError(
        422,
        "VALIDATION_FAILED",
        "请求参数校验失败",
        details={"errors": [{"loc": ["query", "cursor"], "msg": message}]},
    )
