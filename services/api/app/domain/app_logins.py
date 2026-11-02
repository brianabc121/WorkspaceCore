"""n8n 管理界面的登录账号（T-0080，docs/decisions/2026-11-02-app-logins.md）。

T-0082 移除了 Vikunja，之后只有 n8n。

- 只有 admin 以上能读写（路由层 workspace_admin；命令行先 require_role）。
- 密码加密保存（CredentialBox，用途 `app_login:<id>:password`），列表接口只返回是否保存了密码；
  查看明文要单独调用 reveal，每次写审计 `app_login.revealed`。审计元数据只有应用名与改了哪些字段。
- 工作台不去 n8n 校验密码：这里只是给管理员看的备忘，登录失败时在页面上改即可。
  `deploy.sh connect` 自动保存的密码都已经在接入时登录成功过。
"""

import uuid
from typing import cast

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ApiError
from app.domain.audit import AuditTrail
from app.domain.auth import Actor
from app.domain.connectors import ConnectorRuntime
from app.domain.credentials import CredentialError
from app.domain.policy import WorkspaceAccess
from app.models import AppLogin
from app.models.app_login import APPS
from app.repositories import automations as automation_repo
from app.schemas.app_logins import (
    App,
    AppLoginList,
    AppLoginOut,
    AppLoginSecret,
    AppLoginUpsert,
)


def password_context(login_id: uuid.UUID) -> str:
    """凭证加密的用途：密文挪到别的记录上会解密失败。"""
    return f"app_login:{login_id}:password"


def _version_conflict(current: int | None) -> ApiError:
    return ApiError(
        409,
        "VERSION_CONFLICT",
        "内容已被其他操作修改，请刷新后重试",
        details={"current_version": current},
    )


def _validation(loc: list[str | int], msg: str) -> ApiError:
    return ApiError(
        422,
        "VALIDATION_FAILED",
        "请求参数校验失败",
        details={"errors": [{"loc": loc, "msg": msg, "type": "value_error"}]},
    )


def _missing() -> ApiError:
    return ApiError(404, "NOT_FOUND", "还没有保存这个账号的密码")


async def _get(
    db: AsyncSession, workspace_id: uuid.UUID, app: str, *, lock: bool = False
) -> AppLogin | None:
    stmt = select(AppLogin).where(AppLogin.workspace_id == workspace_id, AppLogin.app == app)
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    result: AppLogin | None = await db.scalar(stmt)
    return result


class AppLoginService:
    def __init__(self, db: AsyncSession, settings: Settings, runtime: ConnectorRuntime) -> None:
        self.db = db
        self.settings = settings
        self.runtime = runtime
        self.audit = AuditTrail(db, settings)

    async def _login_url(self, workspace_id: uuid.UUID, app: str) -> str | None:
        connection = await automation_repo.get_connection(self.db, workspace_id)
        return connection.public_url if connection else None

    async def _out(self, workspace_id: uuid.UUID, app: App, login: AppLogin | None) -> AppLoginOut:
        url = await self._login_url(workspace_id, app)
        if login is None:
            return AppLoginOut(app=app, configured=False, login_url=url)
        return AppLoginOut(
            app=app,
            configured=True,
            username=login.username,
            password_set=login.password_ref is not None,
            login_url=url,
            updated_at=login.updated_at,
            version=login.version,
        )

    async def list(self, access: WorkspaceAccess) -> AppLoginList:
        workspace_id = access.workspace.id
        rows = await self.db.scalars(select(AppLogin).where(AppLogin.workspace_id == workspace_id))
        by_app = {row.app: row for row in rows}
        return AppLoginList(
            items=[await self._out(workspace_id, cast(App, app), by_app.get(app)) for app in APPS]
        )

    async def put(
        self, context: Actor, access: WorkspaceAccess, app: App, body: AppLoginUpsert
    ) -> AppLoginOut:
        workspace_id = access.workspace.id
        login = await _get(self.db, workspace_id, app, lock=True)
        box = self.runtime.credentials()
        if login is None:
            if body.version is not None:
                raise _version_conflict(None)  # 读取之后被删除了
            if body.username is None:
                raise _validation(["body", "username"], "新建时必填")
            login_id = uuid.uuid4()
            login = AppLogin(
                id=login_id,
                workspace_id=workspace_id,
                app=app,
                username=body.username,
                password_ref=None
                if body.password is None
                else box.seal(body.password.get_secret_value(), context=password_context(login_id)),
                updated_by=context.user.id,
            )
            self.db.add(login)
            action = "app_login.created"
        else:
            if body.version is None:
                raise _validation(["body", "version"], "修改时必填")
            if body.version != login.version:
                raise _version_conflict(login.version)
            if body.username is not None:
                login.username = body.username
            if body.password is not None:
                login.password_ref = box.seal(
                    body.password.get_secret_value(), context=password_context(login.id)
                )
            login.updated_by = context.user.id
            login.version += 1
            action = "app_login.updated"
        try:
            await self.db.flush()
        except IntegrityError:
            # 两个请求同时新建：后到的那个按版本冲突处理
            await self.db.rollback()
            raise _version_conflict(None) from None
        await self.audit.record(
            action,
            workspace_ids=[workspace_id],
            actor_id=context.user.id,
            resource_type="app_login",
            resource_id=login.id,
            metadata={"app": app, "fields": sorted(body.model_fields_set - {"version"})},
            client_ip=context.client_ip,
        )
        await self.db.commit()
        await self.db.refresh(login)
        return await self._out(workspace_id, app, login)

    async def reveal(self, context: Actor, access: WorkspaceAccess, app: App) -> AppLoginSecret:
        workspace_id = access.workspace.id
        login = await _get(self.db, workspace_id, app)
        if login is None or login.password_ref is None:
            raise _missing()
        try:
            password = self.runtime.credentials().resolve(
                login.password_ref, context=password_context(login.id)
            )
        except CredentialError as error:
            raise ApiError(
                503, "EXTERNAL_UNAVAILABLE", "无法读取保存的密码，请重新保存一次"
            ) from error
        await self.audit.record(
            "app_login.revealed",
            workspace_ids=[workspace_id],
            actor_id=context.user.id,
            resource_type="app_login",
            resource_id=login.id,
            metadata={"app": app},
            client_ip=context.client_ip,
        )
        await self.db.commit()
        return AppLoginSecret(
            app=app, username=login.username, password=password.get_secret_value()
        )

    async def delete(self, context: Actor, access: WorkspaceAccess, app: App, version: int) -> None:
        workspace_id = access.workspace.id
        login = await _get(self.db, workspace_id, app, lock=True)
        if login is None:
            raise ApiError(404, "NOT_FOUND", "还没有保存这个账号")
        if login.version != version:
            raise _version_conflict(login.version)
        await self.db.delete(login)
        await self.audit.record(
            "app_login.deleted",
            workspace_ids=[workspace_id],
            actor_id=context.user.id,
            resource_type="app_login",
            resource_id=login.id,
            metadata={"app": app},
            client_ip=context.client_ip,
        )
        await self.db.commit()
