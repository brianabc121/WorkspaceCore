"""内存版 Google OAuth 2.0 + Gmail API v1，用 httpx.MockTransport 接到 Connector 上（T-0128）。

请求与响应的形状按核验记录 docs/decisions/2026-11-06-gmail-verification.md：

- 令牌端点 `POST /token`（表单）：authorization_code 校验 client、redirect_uri、PKCE S256；
  refresh_token 失效返回 400 `invalid_grant`；client 不对返回 401 `invalid_client`；
  响应 access_token / expires_in / refresh_token（只在换 code 时）/ scope / token_type；
- 撤销 `POST /revoke?token=`：成功 200，令牌不认识 400；
- Gmail：401 带 `UNAUTHENTICATED`；缺范围 403 `insufficientPermissions`；
  限流 429 `rateLimitExceeded`；
  Message 资源的 payload 是 MessagePart 树（正文 base64url），`format=metadata` 只给指定的头；
  `labels/{id}`（labels.get）带 messagesTotal / messagesUnread（T-0170）。

多邮箱聚合（T-0170）用到的：每个邮箱单独的读取故障（read_failures）与延迟（delays）、
指定消息 ID（不同邮箱里故意相同）、没有 internalDate 的邮件，以及并发请求数的记录。
"""

import asyncio
import base64
import hashlib
import json
from dataclasses import dataclass, field
from email import message_from_bytes, policy
from email.message import EmailMessage
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx

OAUTH = "https://oauth2.googleapis.com"
GMAIL = "https://gmail.googleapis.com"
AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
READ = "https://www.googleapis.com/auth/gmail.readonly"
SEND = "https://www.googleapis.com/auth/gmail.send"
CLIENT_ID = "1234567890-fake.apps.googleusercontent.com"
CLIENT_SECRET = "GOCSPX-fake-client-secret-000000"  # noqa: S105 - 测试用的假值
HOSTS = "oauth2.googleapis.com,gmail.googleapis.com"
SETTINGS: dict[str, Any] = {
    "integration_allowed_hosts": HOSTS,
    "google_oauth_token_url": f"{OAUTH}/token",
    "google_oauth_revoke_url": f"{OAUTH}/revoke",
    "gmail_api_url": GMAIL,
}


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii")


def _error(status: int, reason: str, message: str, state: str) -> httpx.Response:
    return httpx.Response(
        status,
        json={
            "error": {
                "code": status,
                "message": message,
                "errors": [{"message": message, "domain": "global", "reason": reason}],
                "status": state,
            }
        },
    )


@dataclass
class Grant:
    email: str
    scopes: list[str]
    refresh_token: str


@dataclass
class Mailbox:
    email: str
    messages: dict[str, dict[str, Any]] = field(default_factory=dict)
    attachments: dict[str, bytes] = field(default_factory=dict)
    sent: list[EmailMessage] = field(default_factory=list)
    sent_bodies: list[dict[str, Any]] = field(default_factory=list)


class FakeGoogle:
    def __init__(self) -> None:
        self.clients = {CLIENT_ID: CLIENT_SECRET}
        self.codes: dict[str, dict[str, Any]] = {}
        self.refresh_tokens: dict[str, Grant] = {}
        self.access_tokens: dict[str, Grant] = {}
        self.revoked: list[str] = []
        self.mailboxes: dict[str, Mailbox] = {}
        self.requests: list[httpx.Request] = []
        self.send_failure: str | None = None  # timeout / rate / server
        self.read_failure: str | None = None  # rate
        self.read_failures: dict[str, str] = {}  # 邮箱 → rate / server（只影响这个邮箱的读取）
        self.delays: dict[str, float] = {}  # 邮箱 → 每个 Gmail 请求的延迟（秒）
        self.in_flight = 0
        self.max_in_flight = 0
        self.box_in_flight: dict[str, int] = {}
        self.max_box_in_flight: dict[str, int] = {}
        self._next = 1000
        self.transport = httpx.MockTransport(self._handle)

    def _id(self, prefix: str = "") -> str:
        self._next += 1
        return f"{prefix}{self._next:x}"

    # ---------------------------------------------------------------- 浏览器一侧

    def consent(
        self,
        authorize_url: str,
        *,
        email: str = "owner.mail@gmail.com",
        scopes: list[str] | None = None,
        deny: bool = False,
    ) -> dict[str, str]:
        """模拟用户在 Google 授权页点同意：返回回调的查询参数（code、state 或 error）。"""
        params = {k: v[0] for k, v in parse_qs(urlsplit(authorize_url).query).items()}
        assert params["client_id"] in self.clients
        assert params["response_type"] == "code"
        assert params["code_challenge_method"] == "S256"
        if deny:
            return {"error": "access_denied", "state": params["state"]}
        granted = scopes if scopes is not None else params["scope"].split()
        code = "4/0A" + self._id("code")
        self.codes[code] = {
            "client_id": params["client_id"],
            "redirect_uri": params["redirect_uri"],
            "challenge": params["code_challenge"],
            "email": email,
            "scopes": granted,
        }
        self.mailboxes.setdefault(email, Mailbox(email))
        return {"code": code, "state": params["state"], "scope": " ".join(granted)}

    # ---------------------------------------------------------------- 邮件数据

    def add_message(
        self,
        email: str,
        *,
        sender: str = "Alice <alice@example.com>",
        to: str = "owner.mail@gmail.com",
        subject: str = "你好",
        text: str | None = "正文第一行\n第二行",
        html: str | None = None,
        labels: tuple[str, ...] = ("INBOX", "UNREAD"),
        thread_id: str | None = None,
        attachments: tuple[tuple[str, str, bytes], ...] = (),
        message_id_header: str | None = None,
        references: str | None = None,
        when: int | None = None,
        message_id: str | None = None,
        no_date: bool = False,
    ) -> dict[str, Any]:
        box = self.mailboxes.setdefault(email, Mailbox(email))
        message_id = message_id or self._id("m")
        thread = thread_id or self._id("t")
        header_id = message_id_header or f"<{message_id}@mail.example.com>"
        headers = [
            {"name": "From", "value": sender},
            {"name": "To", "value": to},
            {"name": "Subject", "value": subject},
            {"name": "Date", "value": "Thu, 5 Nov 2026 10:00:00 +0000"},
            {"name": "Message-ID", "value": header_id},
        ]
        if references:
            headers.append({"name": "References", "value": references})
        parts: list[dict[str, Any]] = []
        if text is not None:
            data = text.encode()
            parts.append(
                {
                    "partId": "0",
                    "mimeType": "text/plain",
                    "filename": "",
                    "headers": [{"name": "Content-Type", "value": "text/plain; charset=UTF-8"}],
                    "body": {"size": len(data), "data": b64(data)},
                }
            )
        if html is not None:
            data = html.encode()
            parts.append(
                {
                    "partId": str(len(parts)),
                    "mimeType": "text/html",
                    "filename": "",
                    "headers": [{"name": "Content-Type", "value": "text/html; charset=UTF-8"}],
                    "body": {"size": len(data), "data": b64(data)},
                }
            )
        body_part: dict[str, Any] = {
            "partId": "0",
            "mimeType": "multipart/alternative",
            "filename": "",
            "headers": [],
            "body": {"size": 0},
            "parts": parts,
        }
        files: list[dict[str, Any]] = []
        for name, mime, content in attachments:
            attachment_id = "ANGj" + self._id("a")
            box.attachments[attachment_id] = content
            files.append(
                {
                    "partId": str(len(files) + 1),
                    "mimeType": mime,
                    "filename": name,
                    "headers": [{"name": "Content-Type", "value": mime}],
                    "body": {"attachmentId": attachment_id, "size": len(content)},
                }
            )
        payload: dict[str, Any] = {
            "partId": "",
            "mimeType": "multipart/mixed",
            "filename": "",
            "headers": headers,
            "body": {"size": 0},
            "parts": [body_part, *files],
        }
        snippet = (text or html or "")[:100].replace("\n", " ")
        message = {
            "id": message_id,
            "threadId": thread,
            "labelIds": list(labels),
            "snippet": snippet,
            "historyId": "12345",
            "internalDate": str(when or 1_793_000_000_000 + self._next),
            "sizeEstimate": 1000,
            "payload": payload,
        }
        if no_date:
            message.pop("internalDate")
        box.messages[message_id] = message
        return message

    # ---------------------------------------------------------------- HTTP

    async def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = request.url
        if url.host == "oauth2.googleapis.com":
            if url.path == "/token":
                return self._token(request)
            if url.path == "/revoke":
                token = url.params.get("token", "")
                if token in self.refresh_tokens or token in self.access_tokens:
                    self.revoked.append(token)
                    grant = self.refresh_tokens.pop(token, None) or self.access_tokens.pop(token)
                    for key in [k for k, v in self.access_tokens.items() if v is grant]:
                        self.access_tokens.pop(key)
                    return httpx.Response(200, json={})
                return httpx.Response(400, json={"error": "invalid_token"})
        if url.host == "gmail.googleapis.com" and url.path.startswith("/gmail/v1/users/me"):
            return await self._timed(request, url.path.removeprefix("/gmail/v1/users/me"))
        return httpx.Response(404, json={"error": {"code": 404, "message": "Not Found"}})

    async def _timed(self, request: httpx.Request, path: str) -> httpx.Response:
        """记录并发数，按邮箱加延迟（模拟慢邮箱）。"""
        auth = request.headers.get("authorization", "")
        grant = self.access_tokens.get(auth.removeprefix("Bearer "))
        email = grant.email if grant else ""
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        self.box_in_flight[email] = self.box_in_flight.get(email, 0) + 1
        self.max_box_in_flight[email] = max(
            self.max_box_in_flight.get(email, 0), self.box_in_flight[email]
        )
        try:
            delay = self.delays.get(email)
            if delay:
                await asyncio.sleep(delay)
            return self._gmail(request, path)
        finally:
            self.in_flight -= 1
            self.box_in_flight[email] -= 1

    def _token(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["content-type"].startswith("application/x-www-form-urlencoded")
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        if self.clients.get(form.get("client_id", "")) != form.get("client_secret"):
            return httpx.Response(
                401, json={"error": "invalid_client", "error_description": "Unauthorized"}
            )
        grant_type = form.get("grant_type")
        if grant_type == "authorization_code":
            code = self.codes.pop(form.get("code", ""), None)
            verifier = form.get("code_verifier", "")
            challenge = (
                base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
                .rstrip(b"=")
                .decode()
            )
            if (
                code is None
                or code["redirect_uri"] != form.get("redirect_uri")
                or code["challenge"] != challenge
            ):
                return httpx.Response(
                    400, json={"error": "invalid_grant", "error_description": "Bad Request"}
                )
            grant = Grant(code["email"], code["scopes"], "1//0g" + self._id("r"))
            self.refresh_tokens[grant.refresh_token] = grant
            return self._issue(grant, refresh=True)
        if grant_type == "refresh_token":
            found = self.refresh_tokens.get(form.get("refresh_token", ""))
            if found is None:
                return httpx.Response(
                    400,
                    json={"error": "invalid_grant", "error_description": "Token has been expired"},
                )
            return self._issue(found, refresh=False)
        return httpx.Response(400, json={"error": "unsupported_grant_type"})

    def _issue(self, grant: Grant, *, refresh: bool) -> httpx.Response:
        access = "ya29." + self._id("at")
        self.access_tokens[access] = grant
        body: dict[str, Any] = {
            "access_token": access,
            "expires_in": 3599,
            "scope": " ".join(grant.scopes),
            "token_type": "Bearer",
        }
        if refresh:
            body["refresh_token"] = grant.refresh_token
        return httpx.Response(200, json=body)

    def expire_access_tokens(self) -> None:
        self.access_tokens.clear()

    def _gmail(self, request: httpx.Request, path: str) -> httpx.Response:
        auth = request.headers.get("authorization", "")
        grant = self.access_tokens.get(auth.removeprefix("Bearer "))
        if grant is None:
            return _error(
                401,
                "authError",
                "Request had invalid authentication credentials.",
                "UNAUTHENTICATED",
            )
        box = self.mailboxes.setdefault(grant.email, Mailbox(grant.email))
        if path == "/profile":
            return httpx.Response(
                200,
                json={
                    "emailAddress": grant.email,
                    "messagesTotal": len(box.messages),
                    "threadsTotal": len({m["threadId"] for m in box.messages.values()}),
                    "historyId": "12345",
                },
            )
        if path == "/messages/send" and request.method == "POST":
            return self._send(request, grant, box)
        if READ not in grant.scopes:
            return _error(
                403,
                "insufficientPermissions",
                "Request had insufficient authentication scopes.",
                "PERMISSION_DENIED",
            )
        failure = self.read_failures.get(grant.email) or self.read_failure
        if failure == "rate":
            return _error(429, "rateLimitExceeded", "Too many requests", "RESOURCE_EXHAUSTED")
        if failure == "server":
            return _error(503, "backendError", "Backend Error", "UNAVAILABLE")
        params = request.url.params
        if path == "/labels":
            return httpx.Response(
                200,
                json={
                    "labels": [
                        {"id": "INBOX", "name": "INBOX", "type": "system"},
                        {"id": "SENT", "name": "SENT", "type": "system"},
                        {"id": "UNREAD", "name": "UNREAD", "type": "system"},
                        {"id": "Label_1", "name": "客户", "type": "user"},
                    ]
                },
            )
        if path == "/messages":
            return self._list(box, params)
        parts = path.strip("/").split("/")
        if parts[0] == "labels" and len(parts) == 2:
            with_label = [m for m in box.messages.values() if parts[1] in m["labelIds"]]
            return httpx.Response(
                200,
                json={
                    "id": parts[1],
                    "name": parts[1],
                    "type": "system",
                    "messagesTotal": len(with_label),
                    "messagesUnread": sum(1 for m in with_label if "UNREAD" in m["labelIds"]),
                    "threadsTotal": len({m["threadId"] for m in with_label}),
                    "threadsUnread": len(
                        {m["threadId"] for m in with_label if "UNREAD" in m["labelIds"]}
                    ),
                },
            )
        if parts[0] == "messages" and len(parts) == 2:
            found = box.messages.get(parts[1])
            if found is None:
                return _error(404, "notFound", "Requested entity was not found.", "NOT_FOUND")
            return httpx.Response(200, json=self._format(found, params))
        if parts[0] == "messages" and len(parts) == 4 and parts[2] == "attachments":
            content = box.attachments.get(parts[3])
            if parts[1] not in box.messages or content is None:
                return _error(404, "notFound", "Requested entity was not found.", "NOT_FOUND")
            return httpx.Response(200, json={"size": len(content), "data": b64(content)})
        if parts[0] == "threads" and len(parts) == 2:
            items = [m for m in box.messages.values() if m["threadId"] == parts[1]]
            if not items:
                return _error(404, "notFound", "Requested entity was not found.", "NOT_FOUND")
            items.sort(key=lambda m: int(m.get("internalDate") or 0))
            return httpx.Response(
                200,
                json={
                    "id": parts[1],
                    "historyId": "12345",
                    "messages": [self._format(m, params) for m in items],
                },
            )
        return _error(404, "notFound", "Requested entity was not found.", "NOT_FOUND")

    @staticmethod
    def _format(message: dict[str, Any], params: httpx.QueryParams) -> dict[str, Any]:
        if params.get("format") != "metadata":
            return message
        wanted = {h.lower() for h in params.get_list("metadataHeaders")}
        headers = [h for h in message["payload"]["headers"] if h["name"].lower() in wanted]
        out = {k: v for k, v in message.items() if k != "payload"}
        out["payload"] = {"partId": "", "mimeType": "multipart/mixed", "headers": headers}
        return out

    def _list(self, box: Mailbox, params: httpx.QueryParams) -> httpx.Response:
        # 和 Gmail 一样按时间倒序（没有 internalDate 的放最后）；默认不含 SPAM / TRASH
        items = sorted(box.messages.values(), key=lambda m: -int(m.get("internalDate") or 0))
        if params.get("includeSpamTrash") != "true":
            items = [m for m in items if not {"SPAM", "TRASH"} & set(m["labelIds"])]
        for label in params.get_list("labelIds"):
            items = [m for m in items if label in m["labelIds"]]
        query = params.get("q") or ""
        for term in query.split():
            if term.startswith("from:"):
                needle = term[5:].lower()
                items = [
                    m
                    for m in items
                    if any(
                        h["name"] == "From" and needle in h["value"].lower()
                        for h in m["payload"]["headers"]
                    )
                ]
            elif term == "is:unread":
                items = [m for m in items if "UNREAD" in m["labelIds"]]
            else:
                items = [m for m in items if term.lower() in json.dumps(m, ensure_ascii=False)]
        size = int(params.get("maxResults") or 100)
        start = int(params.get("pageToken") or 0)
        page = items[start : start + size]
        body: dict[str, Any] = {
            "messages": [{"id": m["id"], "threadId": m["threadId"]} for m in page],
            "resultSizeEstimate": len(items),
        }
        if start + size < len(items):
            body["nextPageToken"] = str(start + size)
        if not page:
            body.pop("messages")
        return httpx.Response(200, json=body)

    def _send(self, request: httpx.Request, grant: Grant, box: Mailbox) -> httpx.Response:
        if SEND not in grant.scopes:
            return _error(
                403,
                "insufficientPermissions",
                "Request had insufficient authentication scopes.",
                "PERMISSION_DENIED",
            )
        if self.send_failure == "timeout":
            # 真实情况：请求已经发出、Google 可能已经发了，只是响应没回来
            box.sent_bodies.append(json.loads(request.content))
            raise httpx.ReadTimeout("timed out", request=request)
        if self.send_failure == "rate":
            return _error(
                429, "rateLimitExceeded", "User-rate limit exceeded", "RESOURCE_EXHAUSTED"
            )
        if self.send_failure == "server":
            return _error(500, "backendError", "Backend Error", "INTERNAL")
        body = json.loads(request.content)
        box.sent_bodies.append(body)
        raw = body["raw"]
        parsed = message_from_bytes(
            base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)), policy=policy.default
        )
        assert isinstance(parsed, EmailMessage)
        message = parsed
        box.sent.append(message)
        message_id = self._id("s")
        thread = body.get("threadId") or self._id("t")
        return httpx.Response(
            200, json={"id": message_id, "threadId": thread, "labelIds": ["SENT"]}
        )


def callback_query(params: dict[str, str]) -> str:
    return "&".join(f"{k}={v}" for k, v in params.items())


__all__ = [
    "AUTH",
    "CLIENT_ID",
    "CLIENT_SECRET",
    "GMAIL",
    "OAUTH",
    "READ",
    "SEND",
    "SETTINGS",
    "FakeGoogle",
    "callback_query",
]
