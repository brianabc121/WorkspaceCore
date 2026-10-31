"""自动化：n8n 连接、白名单工作流、受控触发与执行记录（Sprint 5 契约第 6 节）。

触发（契约第 6.3 节）：
1. Idempotency-Key：同一个键的重复请求返回同一条执行记录，不会再触发一次（Sprint 3 契约第 7 节）。
2. 策略校验（项目规则 9）：白名单启用、n8n 连接可用、工作流已激活、参数符合声明；不满足时不发请求，
   也写审计。
3. 先写一条 unknown 的执行记录并提交，再调用 n8n：进程中途退出时，记录仍在，由对账确认。
4. n8n 受理 → queued；明确拒绝 → 删掉记录并返回错误；超时等结果未知 → 保持 unknown，不自动重发。
触发请求带 W3C traceparent，trace-id 就是执行记录 ID 的十六进制；n8n 把它原样记在执行详情的
tracingContext 里，对账据此找到对应的执行（T-0037 实测）。

对账（Worker 每分钟）：更新没有结束的执行的状态；失败时只读 n8n 遮蔽后的节点名与错误类型作为摘要；
结束时通知触发者；15 分钟仍找不到对应执行的记为“无法确认是否执行”。
"""

import base64
import binascii
import json
import logging
import secrets
import uuid
from collections import defaultdict
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ApiError
from app.domain import idempotency, notifications
from app.domain.audit import AuditTrail
from app.domain.auth import AuthContext
from app.domain.connectors import ConnectorRuntime, api_error
from app.domain.credentials import CredentialError
from app.domain.policy import WorkspaceAccess, not_found
from app.integrations.base import ConnectorError
from app.integrations.http import OutboundPolicy
from app.integrations.n8n import Execution, N8nClient
from app.models import Automation, AutomationConnection, AutomationRun
from app.repositories import automations as repo
from app.schemas.automations import (
    N8N_TESTED_VERSION,
    AutomationConnectionOut,
    AutomationConnectionUpsert,
    AutomationCreate,
    AutomationOut,
    AutomationPage,
    AutomationRunOut,
    AutomationRunPage,
    AutomationUpdate,
    ParamSpec,
    TriggerRequest,
)
from app.schemas.integrations import IntegrationCheck, IntegrationTestResult, SyncState

logger = logging.getLogger("app.automations")

LINK = "/automations"
# 对账：多久以内的执行还会去 n8n 查；超过 MATCH_WINDOW 仍找不到对应执行就记为无法确认
RECONCILE_LOOKBACK = timedelta(days=1)
MATCH_WINDOW = timedelta(minutes=15)
# n8n 与工作台的时钟可能有偏差：按开始时间筛选执行时往前多查一点
CLOCK_SKEW = timedelta(minutes=2)
MAX_DETAIL_READS = 20
_N8N_STATUS = {
    "new": "queued",
    "waiting": "queued",
    "running": "running",
    "success": "success",
    "error": "error",
    "crashed": "error",
    "canceled": "canceled",
    "unknown": "unknown",
}
_FINISHED = {"success", "error", "canceled"}


def _context(connection_id: uuid.UUID, kind: str) -> str:
    """凭证加密的用途：密文挪到别的记录或别的字段上会解密失败。"""
    return f"automation_connection:{connection_id}:{kind}"


def _version_conflict(current: int) -> ApiError:
    return ApiError(
        409,
        "VERSION_CONFLICT",
        "内容已被其他操作修改，请刷新后重试",
        details={"current_version": current},
    )


def _unavailable(reason: str) -> ApiError:
    return ApiError(
        409,
        "AUTOMATION_UNAVAILABLE",
        "现在不能触发这个自动化",
        details={"reason": reason},
    )


def _validation(errors: list[tuple[list[str | int], str]]) -> ApiError:
    return ApiError(
        422,
        "VALIDATION_FAILED",
        "请求参数校验失败",
        details={
            "errors": [{"loc": loc, "msg": msg, "type": "value_error"} for loc, msg in errors]
        },
    )


def _invalid_cursor() -> ApiError:
    return _validation([(["query", "cursor"], "无效的游标")])


# ---------------------------------------------------------------- 参数校验


def validate_params(specs: Sequence[ParamSpec], params: dict[str, Any]) -> dict[str, Any]:
    """按白名单的声明校验触发参数；错误信息不回显参数值。"""
    declared = {spec.name: spec for spec in specs}
    errors: list[tuple[list[str | int], str]] = []
    for name in params:
        if name not in declared:
            errors.append((["body", "params", name], "没有声明这个参数"))
    clean: dict[str, Any] = {}
    for spec in specs:
        loc: list[str | int] = ["body", "params", spec.name]
        if spec.name not in params or params[spec.name] is None:
            if spec.required:
                errors.append((loc, "必填"))
            continue
        value = params[spec.name]
        if spec.type == "string":
            if not isinstance(value, str):
                errors.append((loc, "必须是字符串"))
                continue
            if spec.max_length is not None and len(value) > spec.max_length:
                errors.append((loc, f"最多 {spec.max_length} 个字符"))
                continue
            if spec.choices is not None and value not in spec.choices:
                errors.append((loc, "不是允许的取值"))
                continue
        elif spec.type == "number":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                errors.append((loc, "必须是数字"))
                continue
        elif spec.type == "boolean" and not isinstance(value, bool):
            errors.append((loc, "必须是 true 或 false"))
            continue
        clean[spec.name] = value
    if errors:
        raise _validation(errors)
    return clean


# ---------------------------------------------------------------- 输出


def _specs(automation: Automation) -> list[ParamSpec]:
    return [ParamSpec.model_validate(item) for item in automation.params_json]


def run_out(run: AutomationRun) -> AutomationRunOut:
    return AutomationRunOut(
        id=run.id,
        automation_id=run.automation_id,
        status=run.status,
        external_execution_id=run.external_execution_id,
        triggered_by=run.triggered_by,
        error_summary=run.error_summary,
        started_at=run.started_at,
        finished_at=run.finished_at,
        created_at=run.created_at,
    )


def automation_out(automation: Automation, last: AutomationRun | None) -> AutomationOut:
    return AutomationOut(
        id=automation.id,
        name=automation.name,
        description=automation.description,
        workflow_id=automation.workflow_id,
        webhook_path=automation.webhook_path,
        params=_specs(automation),
        enabled=automation.enabled,
        workflow_active=automation.workflow_active,
        last_run=run_out(last) if last else None,
        version=automation.version,
        created_at=automation.created_at,
        updated_at=automation.updated_at,
    )


def _iso(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None


def connection_sync(
    connection: AutomationConnection | None, *, stale_after_seconds: int
) -> SyncState:
    """n8n 连接的新鲜度（契约第 6.2 节）：未配置为 not_configured，Key 失效为 unauthorized。"""
    if connection is None:
        return SyncState(status="not_configured", as_of=None, reason="not_configured")
    if connection.status == "disabled":
        return SyncState(status="not_configured", as_of=None, reason="disabled")
    last = _iso(connection.settings_json.get("last_sync_at"))
    if connection.status == "unauthorized":
        return SyncState(status="unauthorized", as_of=last, reason="token_rejected")
    if connection.status == "pending":
        return SyncState(status="unavailable", as_of=None, reason="not_tested")
    if last is None:
        return SyncState(status="unavailable", as_of=None, reason="not_synced")
    if connection.status == "unavailable":
        return SyncState(status="stale", as_of=last, reason="source_unavailable")
    if datetime.now(UTC) - last <= timedelta(seconds=stale_after_seconds):
        return SyncState(status="ok", as_of=last)
    return SyncState(status="stale", as_of=last, reason="sync_overdue")


def _connection_out(
    connection: AutomationConnection | None, settings: Settings
) -> AutomationConnectionOut:
    if connection is None:
        return AutomationConnectionOut(configured=False)
    data = connection.settings_json
    return AutomationConnectionOut(
        configured=True,
        base_url=connection.base_url,
        public_url=connection.public_url,
        status=connection.status,
        api_key_set=bool(connection.api_key_ref),
        trigger_secret_set=bool(connection.trigger_secret_ref),
        tested_version=N8N_TESTED_VERSION,
        instance_version=data.get("instance_version"),
        last_tested_at=_iso(data.get("last_tested_at")),
        sync=connection_sync(connection, stale_after_seconds=settings.sync_stale_after_seconds),
        version=connection.version,
    )


def _encode_cursor(run: AutomationRun) -> str:
    raw = json.dumps([run.created_at.isoformat(), str(run.id)]).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        decoded = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        if not (isinstance(decoded, list) and len(decoded) == 2):
            raise ValueError
        created_at, run_id = datetime.fromisoformat(decoded[0]), uuid.UUID(decoded[1])
    except (ValueError, TypeError, binascii.Error):
        raise _invalid_cursor() from None
    if created_at.tzinfo is None:
        raise _invalid_cursor()
    return created_at, run_id


def _offset(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        offset = int(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode())
    except (ValueError, binascii.Error, UnicodeDecodeError):
        raise _invalid_cursor() from None
    if not 0 <= offset <= 100_000:
        raise _invalid_cursor()
    return offset


def _offset_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(str(offset).encode()).rstrip(b"=").decode()


# ---------------------------------------------------------------- 服务


class AutomationService:
    def __init__(self, db: AsyncSession, settings: Settings, runtime: ConnectorRuntime) -> None:
        self.db = db
        self.settings = settings
        self.runtime = runtime
        self.audit = AuditTrail(db, settings)

    # ---------------------------------------------------------------- n8n 连接

    def _client(self, connection: AutomationConnection) -> N8nClient:
        box = self.runtime.credentials()
        try:
            key = box.resolve(connection.api_key_ref, context=_context(connection.id, "api_key"))
        except CredentialError as error:
            raise ApiError(
                503, "EXTERNAL_UNAVAILABLE", "无法读取 n8n 的 API Key，请重新保存连接"
            ) from error
        return N8nClient(connection.base_url, key.get_secret_value(), self.runtime.http())

    def _secret(self, connection: AutomationConnection) -> str:
        box = self.runtime.credentials()
        try:
            secret = box.resolve(
                connection.trigger_secret_ref, context=_context(connection.id, "trigger_secret")
            )
        except CredentialError as error:
            raise ApiError(
                503, "EXTERNAL_UNAVAILABLE", "无法读取 n8n 的共享密钥，请重新保存连接"
            ) from error
        return secret.get_secret_value()

    async def _check_url(self, url: str) -> None:
        try:
            await OutboundPolicy(self.settings.allowed_integration_hosts).check(url)
        except ConnectorError as error:
            if error.reason == "dns_failed":
                return  # 暂时解析不到不阻止保存；测试连接时会报告不可达
            raise _validation([(["body", "base_url"], "不允许访问这个地址")]) from None

    async def get_connection(self, access: WorkspaceAccess) -> AutomationConnectionOut:
        connection = await repo.get_connection(self.db, access.workspace.id)
        return _connection_out(connection, self.settings)

    async def put_connection(
        self, context: AuthContext, access: WorkspaceAccess, body: AutomationConnectionUpsert
    ) -> AutomationConnectionOut:
        connection = await repo.get_connection(self.db, access.workspace.id, lock=True)
        if body.base_url is not None:
            await self._check_url(body.base_url)
        box = self.runtime.credentials()
        if connection is None:
            if body.version is not None:
                raise not_found()
            missing = [
                name
                for name in ("base_url", "api_key", "trigger_secret")
                if getattr(body, name) is None
            ]
            if missing:
                raise _validation([(["body", name], "新建连接时必填") for name in missing])
            if body.base_url is None or body.api_key is None or body.trigger_secret is None:
                raise _validation([(["body"], "新建连接时必填")])
            connection_id = uuid.uuid4()
            connection = AutomationConnection(
                id=connection_id,
                workspace_id=access.workspace.id,
                base_url=body.base_url,
                public_url=body.public_url or body.base_url,
                status="disabled" if body.enabled is False else "pending",
                api_key_ref=box.seal(
                    body.api_key.get_secret_value(), context=_context(connection_id, "api_key")
                ),
                trigger_secret_ref=box.seal(
                    body.trigger_secret.get_secret_value(),
                    context=_context(connection_id, "trigger_secret"),
                ),
                settings_json={},
            )
            self.db.add(connection)
            action = "automation_connection.created"
        else:
            if body.version is None:
                raise _validation([(["body", "version"], "修改时必填")])
            if body.version != connection.version:
                raise _version_conflict(connection.version)
            credentials_changed = False
            if body.base_url is not None and body.base_url != connection.base_url:
                connection.base_url = body.base_url
                credentials_changed = True
            if body.public_url is not None:
                connection.public_url = body.public_url
            if body.api_key is not None:
                connection.api_key_ref = box.seal(
                    body.api_key.get_secret_value(), context=_context(connection.id, "api_key")
                )
                credentials_changed = True
            if body.trigger_secret is not None:
                connection.trigger_secret_ref = box.seal(
                    body.trigger_secret.get_secret_value(),
                    context=_context(connection.id, "trigger_secret"),
                )
            if body.enabled is False:
                connection.status = "disabled"
            elif body.enabled is True and connection.status == "disabled":
                connection.status = "pending"  # 重新启用：需要再测试一次
            elif credentials_changed and connection.status != "disabled":
                connection.status = "pending"
            connection.version += 1
            action = "automation_connection.updated"
        await self.db.flush()
        await self.audit.record(
            action,
            workspace_ids=[access.workspace.id],
            actor_id=context.user.id,
            resource_type="automation_connection",
            resource_id=connection.id,
            metadata={"fields": sorted(body.model_fields_set - {"version"})},
            client_ip=context.client_ip,
        )
        await self.db.commit()
        await self.db.refresh(connection)
        return _connection_out(connection, self.settings)

    async def test_connection(
        self, context: AuthContext, access: WorkspaceAccess
    ) -> IntegrationTestResult:
        connection = await repo.get_connection(self.db, access.workspace.id, lock=True)
        if connection is None:
            raise ApiError(409, "INTEGRATION_UNAVAILABLE", "还没有配置 n8n 连接")
        client = self._client(connection)
        checks: list[IntegrationCheck] = []
        warnings: list[str] = []
        version: str | None = None
        failure: ConnectorError | None = None
        try:
            await client.ready()
            checks.append(IntegrationCheck(name="reachable", ok=True))
        except ConnectorError as error:
            failure = error
            checks.append(IntegrationCheck(name="reachable", ok=False, code=error.reason))
        if failure is None:
            # n8n 完成初始设置后，不登录就读不到版本号，只读 API Key 也读不到（T-0037 实测）
            checks.append(IntegrationCheck(name="version", ok=True, code="not_reported"))
            warnings.append(
                f"n8n 不提供版本号；请确认部署的是核验过的 {N8N_TESTED_VERSION}"
                "（infra/n8n/compose.yaml 已按 digest 锁定）"
            )
            try:
                await client.check_token()
                checks.append(IntegrationCheck(name="token", ok=True))
            except ConnectorError as error:
                failure = error
                checks.append(IntegrationCheck(name="token", ok=False, code=error.reason))
        ok = failure is None
        if connection.status != "disabled":
            if ok:
                connection.status = "active"
            elif failure is not None and failure.kind == "AUTH_FAILED":
                connection.status = "unauthorized"
            else:
                connection.status = "unavailable"
        now = datetime.now(UTC).isoformat()
        connection.settings_json = {
            **connection.settings_json,
            "instance_version": version,
            "last_tested_at": now,
            **({"last_sync_at": now} if ok else {}),
        }
        await self.audit.record(
            "automation_connection.tested",
            workspace_ids=[access.workspace.id],
            actor_id=context.user.id,
            resource_type="automation_connection",
            resource_id=connection.id,
            metadata={"ok": ok, "status": connection.status},
            client_ip=context.client_ip,
        )
        await self.db.commit()
        return IntegrationTestResult(
            ok=ok,
            status=connection.status,
            instance_version=version,
            api_version=None,
            tested_version=N8N_TESTED_VERSION,
            compatible=False,
            checks=checks,
            warnings=warnings,
        )

    async def _usable_connection(self, access: WorkspaceAccess) -> AutomationConnection:
        connection = await repo.get_connection(self.db, access.workspace.id)
        if connection is None or connection.status in {"pending", "disabled"}:
            raise ApiError(409, "INTEGRATION_UNAVAILABLE", "n8n 连接还没有配置或没有测试通过")
        return connection

    # ---------------------------------------------------------------- 白名单

    async def list_automations(
        self, access: WorkspaceAccess, *, cursor: str | None, limit: int
    ) -> AutomationPage:
        offset = _offset(cursor)
        rows = await repo.list_page(self.db, access.workspace.id, offset=offset, limit=limit + 1)
        page = rows[:limit]
        last = await repo.last_runs(self.db, [a.id for a in page])
        connection = await repo.get_connection(self.db, access.workspace.id)
        return AutomationPage(
            items=[automation_out(a, last.get(a.id)) for a in page],
            next_cursor=_offset_cursor(offset + limit) if len(rows) > limit else None,
            total_estimate=None,
            connection=connection_sync(
                connection, stale_after_seconds=self.settings.sync_stale_after_seconds
            ),
        )

    async def _conflict(
        self,
        access: WorkspaceAccess,
        *,
        webhook_path: str | None,
        workflow_id: str | None,
        exclude: uuid.UUID | None = None,
    ) -> None:
        existing = await repo.find_conflict(
            self.db,
            access.workspace.id,
            webhook_path=webhook_path,
            workflow_id=workflow_id,
            exclude=exclude,
        )
        if existing is not None:
            raise ApiError(
                409,
                "AUTOMATION_EXISTS",
                "这个工作流或 Webhook 路径已经登记过",
                details={"existing_id": str(existing.id)},
            )

    async def create(
        self, context: AuthContext, access: WorkspaceAccess, body: AutomationCreate
    ) -> AutomationOut:
        connection = await self._usable_connection(access)
        await self._conflict(access, webhook_path=body.webhook_path, workflow_id=body.workflow_id)
        try:
            workflow = await self._client(connection).get_workflow(body.workflow_id)
        except ConnectorError as error:
            if error.kind == "NOT_FOUND":
                raise ApiError(
                    422,
                    "VALIDATION_FAILED",
                    "n8n 里没有这个工作流",
                    details={
                        "reason": "workflow_not_found",
                        "errors": [
                            {
                                "loc": ["body", "workflow_id"],
                                "msg": "n8n 里没有这个工作流",
                                "type": "value_error",
                            }
                        ],
                    },
                ) from None
            raise api_error(error) from None
        automation = Automation(
            id=uuid.uuid4(),
            workspace_id=access.workspace.id,
            name=body.name,
            description=body.description,
            workflow_id=body.workflow_id,
            webhook_path=body.webhook_path,
            params_json=[spec.model_dump() for spec in body.params],
            enabled=body.enabled,
            workflow_active=workflow.active,
        )
        self.db.add(automation)
        try:
            await self.db.flush()
        except IntegrityError:
            await self.db.rollback()
            await self._conflict(
                access, webhook_path=body.webhook_path, workflow_id=body.workflow_id
            )
            raise
        await self.audit.record(
            "automation.created",
            workspace_ids=[access.workspace.id],
            actor_id=context.user.id,
            resource_type="automation",
            resource_id=automation.id,
            metadata={"workflow_id": body.workflow_id, "params": [p.name for p in body.params]},
            client_ip=context.client_ip,
        )
        await self.db.commit()
        await self.db.refresh(automation)
        return automation_out(automation, None)

    async def _locked(
        self, access: WorkspaceAccess, automation_id: uuid.UUID, version: int
    ) -> Automation:
        automation = await repo.get(self.db, access.workspace.id, automation_id, lock=True)
        if automation is None:
            raise not_found()
        if automation.version != version:
            raise _version_conflict(automation.version)
        return automation

    async def update(
        self,
        context: AuthContext,
        access: WorkspaceAccess,
        automation_id: uuid.UUID,
        body: AutomationUpdate,
    ) -> AutomationOut:
        automation = await self._locked(access, automation_id, body.version)
        if body.webhook_path is not None and body.webhook_path != automation.webhook_path:
            await self._conflict(
                access, webhook_path=body.webhook_path, workflow_id=None, exclude=automation.id
            )
            automation.webhook_path = body.webhook_path
        if body.name is not None:
            automation.name = body.name
        if body.description is not None:
            automation.description = body.description
        if body.params is not None:
            automation.params_json = [spec.model_dump() for spec in body.params]
        if body.enabled is not None:
            automation.enabled = body.enabled
        automation.version += 1
        await self.audit.record(
            "automation.updated",
            workspace_ids=[access.workspace.id],
            actor_id=context.user.id,
            resource_type="automation",
            resource_id=automation.id,
            metadata={"fields": sorted(body.model_fields_set - {"version"})},
            client_ip=context.client_ip,
        )
        await self.db.commit()
        await self.db.refresh(automation)
        last = (await repo.last_runs(self.db, [automation.id])).get(automation.id)
        return automation_out(automation, last)

    async def delete(
        self,
        context: AuthContext,
        access: WorkspaceAccess,
        automation_id: uuid.UUID,
        version: int,
    ) -> None:
        automation = await self._locked(access, automation_id, version)
        automation.deleted_at = datetime.now(UTC)
        automation.enabled = False
        automation.version += 1
        await self.audit.record(
            "automation.deleted",
            workspace_ids=[access.workspace.id],
            actor_id=context.user.id,
            resource_type="automation",
            resource_id=automation.id,
            metadata={"workflow_id": automation.workflow_id},
            client_ip=context.client_ip,
        )
        await self.db.commit()

    # ---------------------------------------------------------------- 触发与执行记录

    async def runs(
        self,
        access: WorkspaceAccess,
        automation_id: uuid.UUID,
        *,
        cursor: str | None,
        limit: int,
    ) -> AutomationRunPage:
        if await repo.get(self.db, access.workspace.id, automation_id) is None:
            raise not_found()
        before = _decode_cursor(cursor) if cursor else None
        rows = await repo.list_runs(self.db, automation_id, before=before, limit=limit + 1)
        page = rows[:limit]
        return AutomationRunPage(
            items=[run_out(run) for run in page],
            next_cursor=_encode_cursor(page[-1]) if len(rows) > limit else None,
            total_estimate=None,
        )

    async def _audit_trigger(
        self,
        context: AuthContext,
        access: WorkspaceAccess,
        automation: Automation,
        *,
        outcome: str,
        run_id: uuid.UUID | None,
        params: Sequence[str],
    ) -> None:
        await self.audit.record(
            "automation.triggered",
            workspace_ids=[access.workspace.id],
            actor_id=context.user.id,
            resource_type="automation",
            resource_id=automation.id,
            metadata={
                "outcome": outcome,
                "run_id": str(run_id) if run_id else None,
                # 只记参数名，不记参数值（契约第 9 节）
                "params": sorted(params),
            },
            client_ip=context.client_ip,
        )

    async def _replay(self, record: Any) -> AutomationRunOut:
        if record.status == "failed":
            error = (record.response_json or {}).get("error") or {}
            raise ApiError(
                record.response_code or 409,
                error.get("code", "CONFLICT"),
                error.get("message", "请求失败"),
                details=error.get("details"),
                headers=error.get("headers"),
            )
        run_id = (record.response_json or {}).get("body", {}).get("run_id")
        run = await repo.get_run(self.db, uuid.UUID(run_id)) if run_id else None
        if run is None:
            raise not_found()
        return run_out(run)

    async def trigger(
        self,
        context: AuthContext,
        access: WorkspaceAccess,
        automation_id: uuid.UUID,
        key: uuid.UUID,
        body: TriggerRequest,
    ) -> AutomationRunOut:
        begun = await idempotency.begin(
            self.db,
            workspace_id=access.workspace.id,
            actor_id=context.user.id,
            key=key,
            request_fingerprint=idempotency.fingerprint(
                access.workspace.id,
                "POST",
                f"/automations/{automation_id}/trigger",
                body.model_dump(mode="json"),
            ),
            context={"operation": "automation.trigger", "automation_id": str(automation_id)},
        )
        record = begun.record
        if begun.state == "replay":
            return await self._replay(record)
        if begun.state == "unknown":
            # 上次的处理进程中途退出：执行记录已写入就交给对账，否则当作新的请求
            run_id = record.context_json.get("run_id")
            run = await repo.get_run(self.db, uuid.UUID(run_id)) if run_id else None
            if run is not None:
                idempotency.succeed(record, 202, {"run_id": str(run.id)})
                await self.db.commit()
                return run_out(run)
        automation = await repo.get(self.db, access.workspace.id, automation_id)
        if automation is None:
            await idempotency.discard(self.db, record)
            await self.db.commit()
            raise not_found()
        try:
            connection, params = await self._policy(automation, body)
        except ApiError as error:
            reason = (error.details or {}).get("reason") or error.code
            await self._audit_trigger(
                context,
                access,
                automation,
                outcome=f"rejected:{reason}",
                run_id=None,
                params=list(body.params),
            )
            await idempotency.discard(self.db, record)
            await self.db.commit()
            raise
        client = self._client(connection)
        secret = self._secret(connection)
        run = AutomationRun(
            id=uuid.uuid4(),
            workspace_id=access.workspace.id,
            automation_id=automation.id,
            status="unknown",
            triggered_by=context.user.id,
        )
        run.trace_id = run.id.hex
        self.db.add(run)
        record.context_json = {**record.context_json, "run_id": str(run.id)}
        await self.db.commit()
        traceparent = f"00-{run.trace_id}-{secrets.token_hex(8)}-01"
        payload = {"workspace_run_id": str(run.id), "params": params}
        try:
            await client.trigger(automation.webhook_path, secret, payload, traceparent)
        except ConnectorError as error:
            return await self._trigger_failed(
                context, access, automation, record, run, error, list(params)
            )
        run.status = "queued"
        idempotency.succeed(record, 202, {"run_id": str(run.id)})
        await self._audit_trigger(
            context, access, automation, outcome="queued", run_id=run.id, params=list(params)
        )
        await self.db.commit()
        await self.db.refresh(run)
        return run_out(run)

    async def _policy(
        self, automation: Automation, body: TriggerRequest
    ) -> tuple[AutomationConnection, dict[str, Any]]:
        """契约第 6.3 节的策略校验，依次检查。"""
        if not automation.enabled:
            raise _unavailable("disabled")
        connection = await repo.get_connection(self.db, automation.workspace_id)
        if connection is None:
            raise _unavailable("not_configured")
        if connection.status not in {"active", "unavailable"}:
            raise _unavailable("connection_unavailable")
        if automation.workflow_active is False:
            raise _unavailable("workflow_inactive")
        return connection, validate_params(_specs(automation), body.params)

    async def _trigger_failed(
        self,
        context: AuthContext,
        access: WorkspaceAccess,
        automation: Automation,
        record: Any,
        run: AutomationRun,
        error: ConnectorError,
        params: list[str],
    ) -> AutomationRunOut:
        """契约第 6.3 节：明确拒绝 → 不保留执行记录；结果未知 → 保持 unknown，由对账确认。"""
        definite = error.not_sent or error.kind in {"AUTH_FAILED", "NOT_FOUND", "VALIDATION_FAILED"}
        if not definite:
            idempotency.succeed(record, 202, {"run_id": str(run.id)})
            await self._audit_trigger(
                context, access, automation, outcome="unknown", run_id=run.id, params=params
            )
            await self.db.commit()
            await self.db.refresh(run)
            return run_out(run)
        await self.db.delete(run)
        if error.kind == "NOT_FOUND":
            problem = _unavailable("webhook_not_found")
            automation.workflow_active = False
        elif error.kind == "AUTH_FAILED":
            problem = ApiError(
                502, "INTEGRATION_UNAUTHORIZED", "n8n 拒绝了共享密钥，请管理员检查连接设置"
            )
        else:
            problem = api_error(error)
        if error.not_sent:
            await idempotency.discard(self.db, record)  # 确定没有送达：允许用同一个键重试
        else:
            idempotency.fail(record, problem)
        await self._audit_trigger(
            context,
            access,
            automation,
            outcome=f"rejected:{error.reason or error.kind}",
            run_id=None,
            params=params,
        )
        if error.kind in {"AUTH_FAILED", "NOT_FOUND"}:
            await notifications.notify_admins(
                self.db,
                access.workspace.id,
                type_="automation_misconfigured",
                severity="warning",
                title=f"自动化「{automation.name}」无法触发",
                body="n8n 拒绝了触发请求（共享密钥不对，或 Webhook 路径不存在、工作流未激活）。"
                "请在 n8n 与自动化设置里检查。",
                link=LINK,
                key=str(automation.id),
            )
        await self.db.commit()
        raise problem


# ---------------------------------------------------------------- 对账（Worker）


def _apply(run: AutomationRun, execution: Execution) -> bool:
    """把 n8n 的执行状态写到执行记录上；返回这次是否刚结束。"""
    was_finished = run.status in _FINISHED
    run.external_execution_id = execution.id
    run.status = _N8N_STATUS.get(execution.status, "unknown")
    run.started_at = execution.started_at or run.started_at
    if run.status in _FINISHED:
        run.finished_at = execution.stopped_at or run.finished_at or datetime.now(UTC)
    return run.status in _FINISHED and not was_finished


async def _notify_finished(db: AsyncSession, automation: Automation, run: AutomationRun) -> None:
    if run.triggered_by is None:
        return
    if run.status == "success":
        await notifications.notify_user(
            db,
            run.workspace_id,
            run.triggered_by,
            type_="automation_succeeded",
            severity="info",
            title=f"自动化「{automation.name}」已完成",
            body="",
            link=LINK,
            key=str(run.id),
        )
    elif run.status in {"error", "canceled"}:
        await notifications.notify_user(
            db,
            run.workspace_id,
            run.triggered_by,
            type_="automation_failed",
            severity="error",
            title=f"自动化「{automation.name}」执行失败"
            if run.status == "error"
            else f"自动化「{automation.name}」已被取消",
            body=run.error_summary or "",
            link=LINK,
            key=str(run.id),
        )


async def _reconcile_connection(
    db: AsyncSession, connection: AutomationConnection, client: N8nClient, now: datetime
) -> int:
    automations = {a.id: a for a in await repo.list_active(db, connection.workspace_id)}
    if not automations:
        await client.ping()  # 没有白名单时也确认 Key 仍然有效
    # 顺便刷新白名单里工作流的激活状态（未激活的不能触发）
    for listed in automations.values():
        try:
            workflow = await client.get_workflow(listed.workflow_id)
        except ConnectorError as error:
            if error.kind != "NOT_FOUND":
                raise
            listed.workflow_active = False
            continue
        listed.workflow_active = workflow.active
    runs = await repo.open_runs(db, connection.workspace_id, since=now - RECONCILE_LOOKBACK)
    by_automation: dict[uuid.UUID, list[AutomationRun]] = defaultdict(list)
    for run in runs:
        by_automation[run.automation_id].append(run)
    updated = 0
    for automation_id, items in by_automation.items():
        automation: Automation | None = automations.get(automation_id) or await db.get(
            Automation, automation_id
        )
        if automation is None:
            continue
        unmatched = {run.trace_id: run for run in items if run.external_execution_id is None}
        if unmatched:
            earliest = min(run.created_at for run in unmatched.values())
            known = await repo.mapped_execution_ids(db, automation_id)
            candidates = await client.list_executions(
                automation.workflow_id, started_after=earliest - CLOCK_SKEW
            )
            reads = 0
            for candidate in candidates:
                if candidate.id in known or not unmatched or reads >= MAX_DETAIL_READS:
                    continue
                reads += 1
                detail = await client.get_execution(candidate.id)
                found = unmatched.pop(detail.trace_id, None) if detail.trace_id else None
                if found is not None:
                    if _apply(found, detail):
                        await _finish(db, automation, found, client)
                    updated += 1
            for run in unmatched.values():
                if now - run.created_at > MATCH_WINDOW and run.error_summary is None:
                    run.status = "unknown"
                    run.error_summary = "无法确认是否执行：n8n 里找不到对应的执行记录"
                    await _notify_unknown(db, automation, run)
                    updated += 1
        for run in items:
            if run.external_execution_id is None or run.status in _FINISHED:
                continue
            try:
                detail = await client.get_execution(run.external_execution_id)
            except ConnectorError as error:
                if error.kind != "NOT_FOUND":
                    raise
                continue  # n8n 里已被清理：保持原状态
            if _apply(run, detail):
                await _finish(db, automation, run, client)
                updated += 1
    return updated


async def _finish(
    db: AsyncSession, automation: Automation, run: AutomationRun, client: N8nClient
) -> None:
    if run.status == "error" and run.external_execution_id:
        try:
            run.error_summary = (await client.error_summary(run.external_execution_id) or "")[
                :500
            ] or None
        except ConnectorError:
            run.error_summary = None
    await _notify_finished(db, automation, run)


async def _notify_unknown(db: AsyncSession, automation: Automation, run: AutomationRun) -> None:
    if run.triggered_by is None:
        return
    await notifications.notify_user(
        db,
        run.workspace_id,
        run.triggered_by,
        type_="automation_failed",
        severity="warning",
        title=f"自动化「{automation.name}」无法确认是否执行",
        body="触发请求的结果未知，n8n 里也找不到对应的执行记录。确认没有执行后可以重新触发。",
        link=LINK,
        key=f"{run.id}:unknown",
    )


async def reconcile_all(
    sessions: Callable[[], AsyncSession], settings: Settings, runtime: ConnectorRuntime
) -> int:
    """Worker 每分钟一次：逐个 n8n 连接对账；一个连接失败不影响其他连接。"""
    async with sessions() as db:
        ids = [c.workspace_id for c in await repo.connections_to_reconcile(db)]
    total = 0
    for workspace_id in ids:
        async with sessions() as db:
            connection = await repo.get_connection(db, workspace_id, lock=True)
            if connection is None or connection.status not in {"active", "unavailable"}:
                continue
            service = AutomationService(db, settings, runtime)
            old_status = connection.status
            try:
                client = service._client(connection)
                total += await _reconcile_connection(db, connection, client, datetime.now(UTC))
                connection.status = "active"
                connection.settings_json = {
                    **connection.settings_json,
                    "last_sync_at": datetime.now(UTC).isoformat(),
                }
            except ApiError as error:
                logger.error("automation reconcile skipped", extra={"error_type": error.code})
                await db.rollback()
                continue
            except ConnectorError as error:
                await db.rollback()
                connection = await repo.get_connection(db, workspace_id, lock=True)
                if connection is None:
                    continue
                connection.status = "unauthorized" if error.kind == "AUTH_FAILED" else "unavailable"
                logger.warning("automation reconcile failed", extra={"error_type": error.kind})
            if connection.status == "unauthorized" and old_status != "unauthorized":
                await notifications.notify_admins(
                    db,
                    workspace_id,
                    type_="integration_unauthorized",
                    severity="error",
                    title="n8n 的 API Key 已失效",
                    body="无法读取执行记录。请在自动化设置里更新 API Key 并重新测试连接。",
                    link=LINK,
                    key=f"n8n:{workspace_id}",
                )
            await db.commit()
    return total
