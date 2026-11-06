"""邮件解析与生成（T-0128，app/domain/mail_format.py）：不需要数据库。"""

import base64
import hashlib
from email import message_from_bytes, policy

from app.domain import mail_format as fmt
from app.integrations import gmail


def _part(mime: str, text: str, **extra: object) -> dict[str, object]:
    data = base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")
    return {"mimeType": mime, "filename": "", "body": {"data": data, "size": len(text)}, **extra}


def test_parse_prefers_plain_and_lists_attachments() -> None:
    message = {
        "id": "m1",
        "threadId": "t1",
        "labelIds": ["INBOX", "UNREAD"],
        "snippet": "Hi &amp; bye",
        "internalDate": "1793000000000",
        "payload": {
            "mimeType": "multipart/mixed",
            "headers": [
                {"name": "from", "value": "A <a@example.com>"},
                {"name": "Subject", "value": "主题"},
                {"name": "X-Other", "value": "ignored"},
            ],
            "parts": [
                {
                    "mimeType": "multipart/alternative",
                    "parts": [
                        _part("text/plain", "纯文字\r\n第二行"),
                        _part("text/html", "<p>HTML 版本</p>"),
                    ],
                },
                {
                    "mimeType": "application/pdf",
                    "filename": "a.pdf",
                    "body": {"attachmentId": "ATT1", "size": 42},
                },
            ],
        },
    }
    parsed = fmt.parse(message)
    assert parsed.text == "纯文字\n第二行" and parsed.truncated is False
    assert parsed.headers == {"From": "A <a@example.com>", "Subject": "主题"}
    assert parsed.snippet == "Hi & bye" and parsed.unread is True
    assert parsed.internal_date is not None and parsed.internal_date.year == 2026
    [attachment] = parsed.attachments
    assert (attachment.filename, attachment.size, attachment.attachment_id) == ("a.pdf", 42, "ATT1")


def test_html_only_is_converted_and_long_text_truncated() -> None:
    html = (
        "<html><head><title>t</title><style>.x{}</style></head><body>"
        "<script>steal()</script><h1>标题</h1><p>第一段 <a href='https://e.com/x'>链接</a>"
        "<a href='javascript:alert(1)'>坏链接</a></p><img src='https://track/p.gif'>"
        "<ul><li>一</li><li>二</li></ul>&lt;不是标签&gt;</body></html>"
    )
    parsed = fmt.parse({"payload": _part("text/html", html)})
    assert "steal" not in parsed.text and ".x" not in parsed.text and "track" not in parsed.text
    assert "javascript" not in parsed.text
    assert "第一段 链接 <https://e.com/x>" in parsed.text
    assert "标题" in parsed.text and "一\n" in parsed.text and "<不是标签>" in parsed.text

    long = fmt.parse({"payload": _part("text/plain", "字" * 30)}, limit=10)
    assert long.text == "字" * 10 and long.truncated is True
    broken = fmt.parse({"payload": {"mimeType": "text/plain", "body": {"data": "@@@"}}})
    assert broken.text == ""
    assert fmt.parse({}).text == "" and fmt.parse({"payload": "bad"}).attachments == []


def test_addresses_and_validation() -> None:
    assert fmt.addresses("A <A@X.com>, b@y.com, a@x.com") == ["a@x.com", "b@y.com"]
    assert fmt.valid_address("a.b+tag@example.co.uk")
    for bad in (
        "no-at",
        "a@b",
        "A <a@example.com>",
        "a@example.com\r\nBcc: x@y.com",
        "a@-bad.com",
        "a@" + "x" * 250 + ".com",
    ):
        assert not fmt.valid_address(bad), bad


def test_reply_subject_and_build() -> None:
    assert fmt.reply_subject("Re: 你好") == "Re: 你好"
    assert fmt.reply_subject("RE: x") == "RE: x"
    assert fmt.reply_subject(" 询价 ") == "Re: 询价"
    assert fmt.reply_subject("") == "Re:"

    parsed = fmt.ParsedMessage(
        message_id="m1",
        thread_id="t9",
        headers={"Message-ID": "<id@x>", "References": "<r1@x>", "Subject": "询价"},
        snippet="",
        labels=[],
        internal_date=None,
    )
    reply = fmt.reply_of(parsed)
    assert reply.thread_id == "t9" and reply.references == "<r1@x> <id@x>"
    raw = fmt.build(
        sender="me@gmail.com",
        sender_name="我",
        to=["a@example.com"],
        cc=["c@example.com"],
        bcc=["b@example.com"],
        subject="",
        body="你好\n第二行",
        reply=reply,
        domain="gmail.com",
    )
    message = message_from_bytes(base64.urlsafe_b64decode(raw), policy=policy.default)
    assert message["Subject"] == "Re: 询价"
    assert message["From"].addresses[0].addr_spec == "me@gmail.com"
    assert message["From"].addresses[0].display_name == "我"
    assert message["To"] == "a@example.com" and message["Bcc"] == "b@example.com"
    assert message["In-Reply-To"] == "<id@x>" and message["References"] == "<r1@x> <id@x>"
    assert message["Message-ID"].endswith("@gmail.com>")
    assert message.get_content_type() == "text/plain"
    assert message.get_content().strip() == "你好\n第二行"


def test_pkce_and_authorize_url() -> None:
    verifier, challenge = gmail.pkce_pair()
    assert 43 <= len(verifier) <= 128 and len(challenge) == 43 and "=" not in challenge
    digest = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=")
    assert digest.decode() == challenge
    url = gmail.authorize_url(
        auth_url="https://accounts.google.com/o/oauth2/v2/auth",
        client_id="cid",
        redirect_uri="https://ws.example.com/api/v1/mail/oauth/callback",
        state="s",
        code_challenge=challenge,
    )
    assert "access_type=offline" in url and "prompt=consent" in url
    assert "gmail.readonly" in url and "gmail.send" in url and "login_hint" not in url
