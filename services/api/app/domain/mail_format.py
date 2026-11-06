"""邮件内容的解析与生成（T-0128，docs/v2/MAIL_PLAN.md 第 4 节、M12）。

- 读：Gmail `Message.payload`（`MessagePart` 树，正文是 base64url）→ 头信息、纯文字正文、
  附件清单。优先 `text/plain`；只有 `text/html` 时转成纯文字（丢掉脚本、样式、图片与所有标签，
  只留文字和链接地址），不把邮件的 HTML 交给页面或智能体。
- 写：用标准库 `email.message.EmailMessage` 生成 RFC 2822 邮件（UTF-8），编码成 base64url
  交给 `messages.send` 的 `raw`；回复时带上 `In-Reply-To`、`References`，主题加 “Re: ”
  （核验记录第 4 节）。
"""

import base64
import binascii
import html
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.headerregistry import Address
from email.message import EmailMessage
from email.utils import formatdate, getaddresses, make_msgid, parseaddr
from html.parser import HTMLParser
from typing import Any

BODY_LIMIT = 20_000
_HEADER_NAMES = ("From", "To", "Cc", "Subject", "Date", "Message-ID", "References", "In-Reply-To")


def b64url_decode(data: str) -> bytes:
    padded = data + "=" * (-len(data) % 4)
    try:
        return base64.urlsafe_b64decode(padded.encode("ascii"))
    except (binascii.Error, ValueError):
        return b""


def b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii")


class _Text(HTMLParser):
    """HTML → 纯文字：块级标签换行，丢掉 script / style / head，链接留下地址。"""

    _BLOCK = frozenset(
        {
            "p",
            "div",
            "br",
            "li",
            "tr",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "table",
            "ul",
            "ol",
            "blockquote",
            "hr",
        }
    )
    _SKIP = frozenset({"script", "style", "head", "title", "noscript", "template"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skip = 0
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._SKIP:
            self.skip += 1
            return
        if tag in self._BLOCK:
            self.out.append("\n")
        if tag == "a":
            href = dict(attrs).get("href") or ""
            if href.startswith(("http://", "https://", "mailto:")):
                self.links.append(href)

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP and self.skip:
            self.skip -= 1
            return
        if tag == "a" and self.links:
            self.out.append(f" <{self.links.pop()}>")
        if tag in self._BLOCK:
            self.out.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.skip:
            self.out.append(data)


def html_to_text(markup: str) -> str:
    parser = _Text()
    try:
        parser.feed(markup)
        parser.close()
    except Exception:  # 坏掉的 HTML 也不能让读信失败
        return re.sub(r"<[^>]+>", " ", html.unescape(markup))
    text = "".join(parser.out)
    text = re.sub(r"[ \t ]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()


@dataclass
class Attachment:
    filename: str
    mime_type: str
    size: int
    attachment_id: str | None


@dataclass
class ParsedMessage:
    message_id: str
    thread_id: str
    headers: dict[str, str]
    snippet: str
    labels: list[str]
    internal_date: datetime | None
    text: str = ""
    truncated: bool = False
    attachments: list[Attachment] = field(default_factory=list)

    @property
    def unread(self) -> bool:
        return "UNREAD" in self.labels


def _headers(part: Mapping[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for header in part.get("headers") or []:
        if not isinstance(header, Mapping):
            continue
        name, value = str(header.get("name") or ""), str(header.get("value") or "")
        for wanted in _HEADER_NAMES:
            if name.lower() == wanted.lower() and wanted not in out:
                out[wanted] = value[:2000]
    return out


def _walk(part: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    yield part
    for child in part.get("parts") or []:
        if isinstance(child, Mapping):
            yield from _walk(child)


def _decode(part: Mapping[str, Any]) -> str:
    body = part.get("body") or {}
    data = body.get("data") if isinstance(body, Mapping) else None
    if not isinstance(data, str) or not data:
        return ""
    return b64url_decode(data).decode("utf-8", errors="replace")


def parse(message: Mapping[str, Any], *, limit: int = BODY_LIMIT) -> ParsedMessage:
    raw_payload = message.get("payload")
    payload: Mapping[str, Any] = raw_payload if isinstance(raw_payload, Mapping) else {}
    internal = message.get("internalDate")
    when = (
        datetime.fromtimestamp(int(internal) / 1000, tz=UTC)
        if isinstance(internal, str | int) and str(internal).isdigit()
        else None
    )
    parsed = ParsedMessage(
        message_id=str(message.get("id") or ""),
        thread_id=str(message.get("threadId") or ""),
        headers=_headers(payload),
        snippet=html.unescape(str(message.get("snippet") or ""))[:500],
        labels=[str(x) for x in message.get("labelIds") or []],
        internal_date=when,
    )
    plain: list[str] = []
    markup: list[str] = []
    for part in _walk(payload):
        mime = str(part.get("mimeType") or "").lower()
        filename = str(part.get("filename") or "")
        raw_body = part.get("body")
        body: Mapping[str, Any] = raw_body if isinstance(raw_body, Mapping) else {}
        if filename:
            parsed.attachments.append(
                Attachment(
                    filename=filename[:255],
                    mime_type=mime[:100] or "application/octet-stream",
                    size=int(body.get("size") or 0),
                    attachment_id=str(body.get("attachmentId"))
                    if body.get("attachmentId")
                    else None,
                )
            )
            continue
        if mime == "text/plain":
            plain.append(_decode(part))
        elif mime == "text/html":
            markup.append(_decode(part))
    text = "\n\n".join(t for t in plain if t.strip())
    if not text and markup:
        text = "\n\n".join(html_to_text(m) for m in markup if m.strip())
    text = text.replace("\r\n", "\n").strip()
    if len(text) > limit:
        parsed.text, parsed.truncated = text[:limit], True
    else:
        parsed.text = text
    return parsed


def addresses(value: str) -> list[str]:
    """头里的地址（“名字 <a@b>”）→ 地址列表（小写，去重）。"""
    out: list[str] = []
    for _, address in getaddresses([value or ""]):
        address = address.strip().lower()
        if address and address not in out:
            out.append(address)
    return out


_ADDRESS = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
)


def valid_address(value: str) -> bool:
    _, address = parseaddr(value)
    return (
        bool(address)
        and address == value.strip()
        and bool(_ADDRESS.match(address))
        and len(address) <= 254
    )


@dataclass(frozen=True)
class Reply:
    """回复时从原信取的东西：会话、Message-ID、References、主题。"""

    thread_id: str
    message_id_header: str
    references: str
    subject: str


def reply_of(parsed: ParsedMessage) -> Reply:
    message_id = parsed.headers.get("Message-ID", "").strip()
    references = " ".join(
        x for x in (parsed.headers.get("References", "").strip(), message_id) if x
    )
    return Reply(
        thread_id=parsed.thread_id,
        message_id_header=message_id,
        references=references[-4000:],
        subject=parsed.headers.get("Subject", ""),
    )


def reply_subject(subject: str) -> str:
    subject = subject.strip()
    return subject if subject.lower().startswith("re:") else f"Re: {subject}" if subject else "Re:"


def build(
    *,
    sender: str,
    sender_name: str | None,
    to: Sequence[str],
    cc: Sequence[str] = (),
    bcc: Sequence[str] = (),
    subject: str,
    body: str,
    reply: Reply | None = None,
    domain: str = "myworkspace.local",
) -> str:
    """生成 RFC 2822 邮件并编码成 base64url（`messages.send` 的 `raw`）。"""
    message = EmailMessage()
    message["From"] = (
        Address(display_name=sender_name or "", addr_spec=sender) if sender_name else sender
    )
    message["To"] = ", ".join(to)
    if cc:
        message["Cc"] = ", ".join(cc)
    if bcc:
        message["Bcc"] = ", ".join(bcc)
    message["Subject"] = reply_subject(reply.subject) if reply and not subject else subject
    message["Date"] = formatdate(localtime=False, usegmt=True)
    message["Message-ID"] = make_msgid(domain=domain)
    if reply and reply.message_id_header:
        message["In-Reply-To"] = reply.message_id_header
        message["References"] = reply.references
    message.set_content(body, subtype="plain", charset="utf-8")
    return b64url_encode(message.as_bytes())


__all__ = [
    "BODY_LIMIT",
    "Attachment",
    "ParsedMessage",
    "Reply",
    "addresses",
    "b64url_decode",
    "b64url_encode",
    "build",
    "html_to_text",
    "parse",
    "reply_of",
    "reply_subject",
    "valid_address",
]
