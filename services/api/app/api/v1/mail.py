"""Gmail 邮件（T-0128，docs/v2/MAIL_PLAN.md 第 3、5 节）。

只有 owner / admin 能用（M3）：设置 OAuth 客户端、连接与断开邮箱、查收、阅读、发信、看发件记录；
member、viewer 一律 403。邮件内容实时从 Gmail 读，不落库；响应都带 `Cache-Control: no-store`。

授权回调 `GET /mail/oauth/callback` 是浏览器从 Google 跳回来的地址：不带工作空间请求头，
靠一次性的 state（绑定发起的用户与工作空间，10 分钟）+ 登录会话 + 再次检查 admin 角色；
处理完 303 跳回 `/settings/mail`，结果放在查询参数 `mail`（connected 或错误码）。
"""

from typing import Annotated
from urllib.parse import quote, urlencode
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Request, Response, Security, status
from fastapi.responses import RedirectResponse
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.deps import (
    authenticated,
    client_ip,
    csrf_protected,
    get_app_settings,
    get_connectors,
    get_db,
    get_redis,
    workspace_admin,
)
from app.core.config import Settings
from app.core.errors import ApiError
from app.domain import mail as mail_domain
from app.domain import mail_agents, mail_aggregate
from app.domain.audit import AuditTrail
from app.domain.auth import AuthContext
from app.domain.connectors import ConnectorRuntime
from app.domain.mail import MailError, MailService, Sender
from app.domain.mail_format import ParsedMessage
from app.domain.policy import WorkspaceAccess, require_role
from app.integrations import gmail
from app.models import AgentInstance, MailAccount, MailSend
from app.schemas.common import error_responses
from app.schemas.mail import (
    MailAccountAgentsPut,
    MailAccountList,
    MailAccountOut,
    MailAccountPatch,
    MailAgentList,
    MailAgentRef,
    MailAggregateAccount,
    MailAggregateItem,
    MailAggregatePage,
    MailAttachmentOut,
    MailConnectIn,
    MailConnectOut,
    MailLabelList,
    MailLabelOut,
    MailList,
    MailMessageOut,
    MailSendIn,
    MailSendList,
    MailSendOut,
    MailSettingsOut,
    MailSettingsPut,
    MailSummary,
    MailThreadOut,
)
from app.schemas.workspaces import Role

router = APIRouter(tags=["mail"])

Admin = Annotated[WorkspaceAccess, Depends(workspace_admin)]
Context = Annotated[AuthContext, Security(authenticated)]
_write = [Depends(csrf_protected)]
NO_STORE = "no-store"
GmailId = Annotated[str, Path(pattern=r"^[A-Za-z0-9_-]{1,512}$")]


def get_mail_service(
    db: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_app_settings)],
    redis: Annotated[Redis, Depends(get_redis)],
    connectors: Annotated[ConnectorRuntime, Depends(get_connectors)],
) -> MailService:
    return MailService(db, settings, redis, connectors)


Service = Annotated[MailService, Depends(get_mail_service)]


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = NO_STORE


def account_out(account: MailAccount, agents: list[MailAgentRef]) -> MailAccountOut:
    return MailAccountOut(
        id=account.id,
        email=account.email,
        display_name=account.display_name,
        status=account.status,
        can_read=gmail.SCOPE_READ in (account.scopes or []),
        can_send=gmail.SCOPE_SEND in (account.scopes or []),
        agents=agents,
        agent_signature=account.agent_signature,
        last_error=account.last_error,
        connected_at=account.connected_at,
        version=account.version,
    )


def summary_out(parsed: ParsedMessage) -> MailSummary:
    headers = parsed.headers
    return MailSummary(
        id=parsed.message_id,
        thread_id=parsed.thread_id,
        sender=headers.get("From", ""),
        to=headers.get("To", ""),
        cc=headers.get("Cc", ""),
        subject=headers.get("Subject", ""),
        date=parsed.internal_date,
        snippet=parsed.snippet,
        unread=parsed.unread,
        labels=parsed.labels,
    )


def message_out(parsed: ParsedMessage) -> MailMessageOut:
    return MailMessageOut(
        **summary_out(parsed).model_dump(),
        text=parsed.text,
        truncated=parsed.truncated,
        attachments=[
            MailAttachmentOut(
                attachment_id=a.attachment_id,
                filename=a.filename,
                mime_type=a.mime_type,
                size=a.size,
            )
            for a in parsed.attachments
        ],
    )


def send_out(record: MailSend) -> MailSendOut:
    return MailSendOut(
        id=record.id,
        account_id=record.account_id,
        actor_kind=record.actor_kind,
        actor_name=record.actor_name,
        sender=record.sender,
        to=list(record.to_addresses or []),
        cc=list(record.cc_addresses or []),
        bcc_count=record.bcc_count,
        subject=record.subject,
        status=record.status,
        error_code=record.error_code,
        gmail_message_id=record.gmail_message_id,
        gmail_thread_id=record.gmail_thread_id,
        created_at=record.created_at,
        finished_at=record.finished_at,
    )


async def _settings_out(service: MailService, access: WorkspaceAccess) -> MailSettingsOut:
    client = await service.oauth_client(access.workspace.id)
    return MailSettingsOut(
        configured=client is not None,
        client_id=client.client_id if client else None,
        client_secret_set=bool(client and client.client_secret_ref),
        redirect_uri=service.redirect_uri(),
        version=client.version if client else None,
    )


# ---------------------------------------------------------------- 设置与连接


@router.get(
    "/mail/settings",
    summary="Gmail 的 OAuth 客户端设置（admin）",
    description=(
        "不返回 client_secret，只说是否已设置；redirect_uri 要登记到 Google 的 OAuth 客户端里。"
    ),
    responses=error_responses(401, 403, 404),
)
async def get_mail_settings(access: Admin, service: Service, response: Response) -> MailSettingsOut:
    _no_store(response)
    return await _settings_out(service, access)


@router.put(
    "/mail/settings",
    summary="保存 Gmail 的 OAuth 客户端（admin，乐观并发）",
    description="第一次必须填 client_secret；之后不填表示保持不变。改动时带上读取时的 version。",
    dependencies=_write,
    responses=error_responses(401, 403, 404, 409, 422),
)
async def put_mail_settings(
    request: Request,
    access: Admin,
    context: Context,
    service: Service,
    body: MailSettingsPut,
    response: Response,
) -> MailSettingsOut:
    _no_store(response)
    secret = body.client_secret.get_secret_value().strip() if body.client_secret else None
    client = await service.put_client(
        access.workspace.id,
        context.user.id,
        client_id=body.client_id,
        client_secret=secret or None,
        version=body.version,
    )
    await AuditTrail(service.db, service.settings).record(
        "mail.client_updated",
        workspace_ids=[access.workspace.id],
        actor_id=context.user.id,
        resource_type="mail_oauth_client",
        resource_id=client.id,
        metadata={"client_id": client.client_id, "secret_changed": bool(secret)},
        client_ip=client_ip(request, service.settings),
    )
    await service.db.commit()
    return await _settings_out(service, access)


@router.post(
    "/mail/connect",
    summary="开始连接 Gmail 邮箱（admin）",
    description=(
        "返回 Google 的授权地址，浏览器打开它完成授权；10 分钟内有效、只能用一次。"
        "申请 gmail.readonly 与 gmail.send 两个范围，按实际勾选的记下能读、能发。"
    ),
    dependencies=_write,
    responses=error_responses(401, 403, 404, 409, 422),
)
async def start_mail_connect(
    access: Admin, context: Context, service: Service, body: MailConnectIn, response: Response
) -> MailConnectOut:
    _no_store(response)
    url = await service.start(access.workspace.id, context.user.id, login_hint=body.login_hint)
    return MailConnectOut(authorize_url=url)


def _back(result: str) -> RedirectResponse:
    response = RedirectResponse(
        "/settings/mail?" + urlencode({"mail": result}), status_code=status.HTTP_303_SEE_OTHER
    )
    response.headers["Cache-Control"] = NO_STORE
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@router.get(
    "/mail/oauth/callback",
    summary="Google 授权回调（浏览器跳转）",
    description=(
        "Google 授权后浏览器跳回这里。校验 state（一次性、10 分钟、必须是发起连接的同一个用户）"
        "并再次检查 admin 角色；成功后 303 跳到 /settings/mail?mail=connected，"
        "失败跳到 /settings/mail?mail=<错误码>。"
    ),
    status_code=status.HTTP_303_SEE_OTHER,
    response_class=RedirectResponse,
    responses=error_responses(401),
)
async def mail_oauth_callback(
    request: Request,
    context: Context,
    service: Service,
    state: Annotated[str, Query(max_length=200)] = "",
    code: Annotated[str | None, Query(max_length=2048)] = None,
    error: Annotated[str | None, Query(max_length=200)] = None,
) -> RedirectResponse:
    data = await service.take_state(state)
    if data is None or data.get("u") != str(context.user.id):
        return _back("MAIL_STATE_INVALID")
    try:
        access = await require_role(
            service.db, user_id=context.user.id, workspace_id=UUID(data["w"]), minimum=Role.admin
        )
    except (ApiError, KeyError, ValueError):
        return _back("FORBIDDEN")
    if error or not code:
        return _back("MAIL_ACCESS_DENIED" if error == "access_denied" else "MAIL_OAUTH_FAILED")
    try:
        account = await service.finish(data, code=code)
    except MailError as failure:
        await service.db.rollback()
        return _back(failure.code)
    await AuditTrail(service.db, service.settings).record(
        "mail.connected",
        workspace_ids=[access.workspace.id],
        actor_id=context.user.id,
        resource_type="mail_account",
        resource_id=account.id,
        metadata={"email": account.email, "scopes": list(account.scopes or [])},
        client_ip=client_ip(request, service.settings),
    )
    await service.db.commit()
    return _back("connected")


# ---------------------------------------------------------------- 邮箱


@router.get(
    "/mail/accounts",
    summary="已连接的 Gmail 邮箱（admin）",
    responses=error_responses(401, 403, 404),
)
async def list_mail_accounts(
    access: Admin, service: Service, response: Response
) -> MailAccountList:
    _no_store(response)
    accounts = await service.accounts(access.workspace.id)
    agents = await mail_agents.agents_by_account(service.db, [a.id for a in accounts])
    return MailAccountList(items=[account_out(a, agents[a.id]) for a in accounts])


async def _account_out(service: MailService, account: MailAccount) -> MailAccountOut:
    agents = await mail_agents.agents_by_account(service.db, [account.id])
    return account_out(account, agents[account.id])


@router.patch(
    "/mail/accounts/{account_id}",
    summary="改邮箱的智能体设置（admin，乐观并发）",
    description="agent_signature：智能体发的信末尾加署名行。分配给哪些智能体见 PUT …/agents。",
    dependencies=_write,
    responses=error_responses(401, 403, 404, 409, 422),
)
async def patch_mail_account(
    request: Request,
    account_id: UUID,
    access: Admin,
    context: Context,
    service: Service,
    body: MailAccountPatch,
    response: Response,
) -> MailAccountOut:
    _no_store(response)
    account = await service.account(access.workspace.id, account_id)
    if body.version != account.version:
        raise MailError(409, "VERSION_CONFLICT", "邮箱设置已被别人修改，请刷新后再试")
    changes: dict[str, bool] = {}
    if body.agent_signature is not None and body.agent_signature != account.agent_signature:
        account.agent_signature = body.agent_signature
        changes["agent_signature"] = body.agent_signature
    if changes:
        account.version += 1
        await AuditTrail(service.db, service.settings).record(
            "mail.account_updated",
            workspace_ids=[access.workspace.id],
            actor_id=context.user.id,
            resource_type="mail_account",
            resource_id=account.id,
            metadata={"email": account.email, **changes},
            client_ip=client_ip(request, service.settings),
        )
        await service.db.commit()
    return await _account_out(service, account)


@router.get(
    "/mail/agents",
    summary="可以分配邮箱的智能体实例（admin）",
    description="只返回 ID 与名字，给 设置 → 邮件 的“分配给智能体”用（实例列表只有 owner 能看）。",
    responses=error_responses(401, 403),
)
async def list_mail_agents(access: Admin, service: Service, response: Response) -> MailAgentList:
    _no_store(response)
    rows = await service.db.execute(
        select(AgentInstance.id, AgentInstance.name)
        .where(AgentInstance.workspace_id == access.workspace.id)
        .order_by(AgentInstance.name, AgentInstance.id)
    )
    return MailAgentList(items=[MailAgentRef(id=i, name=n) for i, n in rows.all()])


@router.put(
    "/mail/accounts/{account_id}/agents",
    summary="把邮箱分配给智能体（admin，整组替换，乐观并发）",
    description=(
        "instance_ids：能用这个邮箱的智能体实例。没分配的智能体看不到这个邮箱；"
        "能不能查、能不能发仍按每个实例的邮件开关。变化的实例 version 也加 1。"
    ),
    dependencies=_write,
    responses=error_responses(401, 403, 404, 409, 422),
)
async def put_mail_account_agents(
    request: Request,
    account_id: UUID,
    access: Admin,
    context: Context,
    service: Service,
    body: MailAccountAgentsPut,
    response: Response,
) -> MailAccountOut:
    _no_store(response)
    account = await service.account(access.workspace.id, account_id)
    if body.version != account.version:
        raise MailError(409, "VERSION_CONFLICT", "邮箱设置已被别人修改，请刷新后再试")
    added, removed = await mail_agents.set_instances_for_account(
        service.db, account, body.instance_ids, user_id=context.user.id
    )
    if added or removed:
        account.version += 1
        await AuditTrail(service.db, service.settings).record(
            "mail.account_agents_updated",
            workspace_ids=[access.workspace.id],
            actor_id=context.user.id,
            resource_type="mail_account",
            resource_id=account.id,
            metadata={
                "email": account.email,
                "added": [str(i.id) for i in added],
                "removed": [str(i.id) for i in removed],
            },
            client_ip=client_ip(request, service.settings),
        )
        await service.db.commit()
    return await _account_out(service, account)


@router.delete(
    "/mail/accounts/{account_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="断开邮箱（admin）",
    description="尽力撤销 Google 授权并删除保存的令牌，取消它对智能体的全部分配；发件记录保留。",
    dependencies=_write,
    responses=error_responses(401, 403, 404, 422),
)
async def disconnect_mail_account(
    request: Request, account_id: UUID, access: Admin, context: Context, service: Service
) -> None:
    account = await service.account(access.workspace.id, account_id)
    await service.disconnect(account)
    # 断开即取消全部分配：以后重新连接同一个邮箱，要重新分配给智能体
    unassigned = await mail_agents.clear_account(service.db, account.id)
    await AuditTrail(service.db, service.settings).record(
        "mail.disconnected",
        workspace_ids=[access.workspace.id],
        actor_id=context.user.id,
        resource_type="mail_account",
        resource_id=account.id,
        metadata={"email": account.email, "unassigned_agents": unassigned},
        client_ip=client_ip(request, service.settings),
    )
    await service.db.commit()


# ---------------------------------------------------------------- 读


@router.get(
    "/mail/accounts/{account_id}/labels",
    summary="邮箱的标签（admin）",
    responses=error_responses(401, 403, 404, 409, 429, 503, 504),
)
async def list_mail_labels(
    account_id: UUID, access: Admin, service: Service, response: Response
) -> MailLabelList:
    _no_store(response)
    account = await service.account(access.workspace.id, account_id)
    labels = await service.labels(account)
    return MailLabelList(
        items=[
            MailLabelOut(
                id=str(label.get("id") or ""),
                name=str(label.get("name") or ""),
                type=str(label.get("type") or "user"),
                unread=int(label["messagesUnread"])
                if isinstance(label.get("messagesUnread"), int)
                else None,
            )
            for label in labels
            if label.get("id")
        ]
    )


def get_sessionmaker(request: Request) -> async_sessionmaker[AsyncSession]:
    """聚合读取给每个邮箱 worker 开自己的会话（不共用请求的 AsyncSession）。"""
    maker: async_sessionmaker[AsyncSession] = request.app.state.sessionmaker
    return maker


@router.get(
    "/mail/messages",
    summary="全部邮箱或单个邮箱的邮件（admin，聚合读取）",
    description=(
        "不填 account_id：读全部已连接的邮箱，按 Gmail internalDate 倒序合并成一批（同一时间按"
        " account_id、id 排序，时间未知的放最后；不跨邮箱去重）。填了只读这个邮箱。"
        "每个邮箱每批最多 per_account_limit 封；“全部邮件”（不填 label）不含垃圾邮件与垃圾箱。"
        "全部邮箱模式下 label 只能是系统标签，自定义标签要指定邮箱。"
        "accounts 是每个邮箱的状态：一个邮箱失败不影响其他邮箱。"
        "next_cursor 加载下一批（15 分钟内有效）；邮箱范围、q、label、per_account_limit 变了旧游标"
        " 422 MAIL_CURSOR_MISMATCH，过期 422 MAIL_CURSOR_EXPIRED。"
        "读信、会话、附件、回复仍用 /mail/accounts/{account_id}/… 的接口，必须带所属邮箱。"
    ),
    responses=error_responses(401, 403, 404, 422),
)
async def list_mail_messages(
    access: Admin,
    context: Context,
    service: Service,
    sessionmaker: Annotated[async_sessionmaker[AsyncSession], Depends(get_sessionmaker)],
    response: Response,
    account_id: UUID | None = None,
    q: Annotated[str | None, Query(max_length=500)] = None,
    label: Annotated[str | None, Query(max_length=100, pattern=r"^[A-Za-z0-9_-]+$")] = None,
    cursor: Annotated[
        str | None, Query(min_length=16, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    ] = None,
    per_account_limit: Annotated[
        int, Query(ge=1, le=mail_aggregate.MAX_LIMIT)
    ] = mail_aggregate.DEFAULT_LIMIT,
) -> MailAggregatePage:
    _no_store(response)
    # 先取出 ID：聚合读取会结束请求会话的事务，之后不再碰 ORM 对象
    workspace_id, user_id = access.workspace.id, context.user.id
    page = await mail_aggregate.MailAggregateService(
        sessionmaker, service.settings, service.redis, service.runtime
    ).batch(
        service.db,
        workspace_id=workspace_id,
        user_id=user_id,
        account_id=account_id,
        query=(q or "").strip() or None,
        label=label,
        cursor=cursor,
        limit=per_account_limit,
    )
    return MailAggregatePage(
        items=[
            MailAggregateItem(
                **summary_out(message).model_dump(),
                account_id=ref.id,
                account_email=ref.email,
                account_display_name=ref.display_name,
            )
            for ref, message in page.items
        ],
        accounts=[
            MailAggregateAccount(
                account_id=r.ref.id,
                email=r.ref.email,
                display_name=r.ref.display_name,
                status=r.status,
                error_code=r.error_code,
                loaded=r.loaded,
                exhausted=r.exhausted,
                unread=r.unread,
                retry_at=r.retry_at,
            )
            for r in page.accounts
        ],
        outcome=page.outcome,
        partial=page.partial,
        next_cursor=page.next_cursor,
        total_estimate=page.total_estimate,
    )


@router.get(
    "/mail/accounts/{account_id}/messages",
    summary="查收、搜索邮件（admin）",
    description="q 用 Gmail 搜索语法（如 from:a@b.com is:unread）；label 是标签 ID（如 INBOX）。",
    responses=error_responses(401, 403, 404, 409, 422, 429, 503, 504),
)
async def search_mail(
    account_id: UUID,
    access: Admin,
    service: Service,
    response: Response,
    q: Annotated[str | None, Query(max_length=500)] = None,
    label: Annotated[str | None, Query(max_length=100, pattern=r"^[A-Za-z0-9_-]+$")] = None,
    page_token: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=mail_domain.LIST_LIMIT)] = 20,
) -> MailList:
    _no_store(response)
    account = await service.account(access.workspace.id, account_id)
    listing = await service.search(
        account, query=q, label=label, max_results=limit, page_token=page_token
    )
    return MailList(
        items=[summary_out(m) for m in listing.messages],
        next_page_token=listing.next_page_token,
        estimate=listing.estimate,
    )


@router.get(
    "/mail/accounts/{account_id}/messages/{message_id}",
    summary="读一封邮件（admin）",
    description="正文是纯文字（只有 HTML 时已转成纯文字），超过 2 万字截断；附件只给清单。",
    responses=error_responses(401, 403, 404, 409, 422, 429, 503, 504),
)
async def get_mail_message(
    account_id: UUID,
    message_id: GmailId,
    access: Admin,
    service: Service,
    response: Response,
) -> MailMessageOut:
    _no_store(response)
    account = await service.account(access.workspace.id, account_id)
    return message_out(await service.message(account, message_id))


@router.get(
    "/mail/accounts/{account_id}/threads/{thread_id}",
    summary="读一个会话（admin）",
    description="按时间顺序，最多最近 20 封。",
    responses=error_responses(401, 403, 404, 409, 422, 429, 503, 504),
)
async def get_mail_thread(
    account_id: UUID,
    thread_id: GmailId,
    access: Admin,
    service: Service,
    response: Response,
) -> MailThreadOut:
    _no_store(response)
    account = await service.account(access.workspace.id, account_id)
    messages = await service.thread(account, thread_id)
    return MailThreadOut(thread_id=thread_id, messages=[message_out(m) for m in messages])


@router.get(
    "/mail/accounts/{account_id}/messages/{message_id}/attachments/{attachment_id}",
    summary="下载附件（admin）",
    description=(
        "经工作台转发，以 application/octet-stream 下载（不在浏览器里打开）；"
        "最大 25 MB，更大的请在 Gmail 里下载。"
    ),
    response_class=Response,
    responses={
        200: {"content": {"application/octet-stream": {}}, "description": "附件内容"},
        **error_responses(401, 403, 404, 409, 413, 422, 429, 503, 504),
    },
)
async def download_mail_attachment(
    account_id: UUID,
    message_id: GmailId,
    attachment_id: GmailId,
    access: Admin,
    service: Service,
    filename: Annotated[str, Query(max_length=255)] = "attachment",
) -> Response:
    account = await service.account(access.workspace.id, account_id)
    content = await service.attachment(account, message_id, attachment_id)
    safe = "".join(c for c in filename if c.isprintable() and c not in '"\\/;') or "attachment"
    return Response(
        content,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(safe)}",
            "Cache-Control": NO_STORE,
            "X-Content-Type-Options": "nosniff",
        },
    )


# ---------------------------------------------------------------- 发


@router.post(
    "/mail/accounts/{account_id}/send",
    summary="发一封纯文字邮件（admin）",
    description=(
        "收件人合计最多 20 个；正文里像是有密钥时拒绝（SECRET_BLOCKED）。"
        "回复时给 reply_to_message_id，主题可以不填。Gmail 没有及时响应时记为 unknown，"
        "不自动重发：请到 Gmail 的“已发送”里确认。"
    ),
    dependencies=_write,
    responses=error_responses(401, 403, 404, 409, 422, 429, 503, 504),
)
async def send_mail(
    request: Request,
    account_id: UUID,
    access: Admin,
    context: Context,
    service: Service,
    body: MailSendIn,
    response: Response,
) -> MailSendOut:
    _no_store(response)
    account = await service.account(access.workspace.id, account_id)
    record = await service.send(
        account,
        Sender(kind="user", name=context.user.display_name, user_id=context.user.id),
        to=body.to,
        cc=body.cc,
        bcc=body.bcc,
        subject=body.subject,
        body=body.body,
        reply_to_message_id=body.reply_to_message_id,
    )
    await AuditTrail(service.db, service.settings).record(
        "mail.sent",
        workspace_ids=[access.workspace.id],
        actor_id=context.user.id,
        resource_type="mail_send",
        resource_id=record.id,
        metadata={
            "email": account.email,
            "recipients": len(record.to_addresses) + len(record.cc_addresses) + record.bcc_count,
            "reply": bool(record.reply_to_message_id),
        },
        client_ip=client_ip(request, service.settings),
    )
    await service.db.commit()
    return send_out(record)


@router.get(
    "/mail/sends",
    summary="发件记录（admin）",
    description=(
        "人和智能体经工作台发的信（不含正文），最新的在前；instance_id 只看某个智能体实例。"
    ),
    responses=error_responses(401, 403, 404, 422),
)
async def list_mail_sends(
    access: Admin,
    service: Service,
    response: Response,
    instance_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> MailSendList:
    _no_store(response)
    records = await service.sends(access.workspace.id, instance_id=instance_id, limit=limit)
    return MailSendList(items=[send_out(r) for r in records])
