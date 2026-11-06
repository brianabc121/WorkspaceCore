"""智能体发图片、文件、视频（T-0119，docs/v2/AGENT_MESSAGING_PLAN.md 第 4 节）。

真实 PostgreSQL + Redis，模拟 OpenIM（含对象存储）。

覆盖：
- 附件权限默认关闭；管理员按实例打开（乐观并发、审计）；
- 内联图片 → 图片元素（102），ex 带工作台签名，媒体记到会话；
- 文字成文件（105）；可执行文件、网页类被拦；文字附件里有私钥被拦；
- 网址只取公网地址；视频（MP4）读出时长、带默认封面（104）；
- before 回调：实例直接发图片（没有工作台签名）拒收 5007，经工作台发的放行，文字照常；
- 管理员下载文件按附件、视频支持 Range；管理员网页也能上传、发送文件。
"""

import base64
import json
import struct
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from app.domain import agent_media
from app.domain.im import admin_im_user_id
from app.domain.im_projection import signed_ex
from app.jobs import worker
from tests import fake_openim
from tests.helpers import run_sql, tool_error
from tests.test_im_accounts_callbacks import GROUP, SINGLE, ImEnv, _worker_ctx, im_env, msg
from tests.test_im_gateway import INTRANET
from tests.test_im_media_api import png
from tests.test_task_claims_api import agent, call, ok

pytestmark = pytest.mark.integration

FILE_HOST = "93.184.216.34"  # 公网地址的 IP 字面量：不做 DNS 解析，由模拟的 transport 应答


@pytest.fixture
async def env(make_client: Any, migrated_database_url: str) -> AsyncIterator[ImEnv]:
    async with im_env(
        make_client, migrated_database_url, openim_object_url=fake_openim.OBJECT_URL
    ) as env:
        fake = env.fake

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == FILE_HOST:
                if request.url.path == "/pic.png":
                    return httpx.Response(200, content=png(20, 10), headers={"Content-Type": "x/y"})
                if request.url.path == "/moved":
                    return httpx.Response(302, headers={"Location": "http://127.0.0.1/secret"})
                return httpx.Response(404)
            return fake.handler(request)

        env.app.state.connectors.transport = httpx.MockTransport(handler)
        yield env


def mp4(seconds: int) -> bytes:
    ftyp = struct.pack(">I", 20) + b"ftypmp42" + b"\x00\x00\x00\x00" + b"mp42"
    mvhd_body = (
        b"\x00\x00\x00\x00" + struct.pack(">IIII", 0, 0, 1000, seconds * 1000) + b"\x00" * 80
    )
    mvhd = struct.pack(">I", 8 + len(mvhd_body)) + b"mvhd" + mvhd_body
    moov = struct.pack(">I", 8 + len(mvhd)) + b"moov" + mvhd
    return ftyp + moov + b"\x00" * 64


async def _agent(env: ImEnv, name: str, *, media: bool = True) -> tuple[dict[str, Any], str]:
    created, token = await agent(env, name)
    await run_sql(
        env.database_url,
        "UPDATE workspace.agent_instances SET im_ready = true WHERE id = $1",
        uuid.UUID(created["id"]),
    )
    if media:
        current = await env.instance(created["id"])
        response = await env.client.put(
            f"/api/v1/agents/instances/{created['id']}/im-media",
            json={"enabled": True, "version": current["version"]},
            headers=env.headers(write=True),
        )
        assert response.status_code == 200, response.text
        assert response.json()["enabled"] is True
    return created, token


async def _open_dm(env: ImEnv, instance_id: str) -> str:
    """管理员打开和实例的私聊（会话记录），之后才能按会话取媒体、发消息。"""
    opened = await env.client.post(
        "/api/v1/im/conversations",
        json={"instance_id": instance_id},
        headers=env.headers(write=True),
    )
    assert opened.status_code in (200, 201), opened.text
    conversation: str = opened.json()["conversation_id"]
    return conversation


def _last(env: ImEnv, conversation: str, sender: str) -> dict[str, Any]:
    sent = [m for m in env.fake.messages.get(conversation, []) if m["sendID"] == sender]
    assert sent, conversation
    out = dict(sent[-1])
    out["content"] = json.loads(base64.b64decode(sent[-1]["content"]))
    return out


async def test_switch_is_off_by_default_and_audited(env: ImEnv) -> None:
    a, token = await _agent(env, "甲", media=False)
    owner = admin_im_user_id(env.owner_id)
    image = base64.b64encode(png()).decode()
    refused = tool_error(
        await call(
            env,
            token,
            "im_send",
            {"target": f"user:{owner}", "attachments": [{"data_base64": image}]},
        )
    )
    assert refused["code"] == "ATTACHMENT_FORBIDDEN"
    listed = ok(await call(env, token, "im_list_targets", {"kind": "direct"}))
    assert listed["attachments_enabled"] is False
    current = await env.instance(a["id"])
    stale = await env.client.put(
        f"/api/v1/agents/instances/{a['id']}/im-media",
        json={"enabled": True, "version": current["version"] + 5},
        headers=env.headers(write=True),
    )
    assert stale.status_code == 409
    await env.client.put(
        f"/api/v1/agents/instances/{a['id']}/im-media",
        json={"enabled": True, "version": current["version"]},
        headers=env.headers(write=True),
    )
    audits = await run_sql(
        env.database_url,
        "SELECT count(*) FROM workspace.audit_logs"
        " WHERE action = 'agents.instance.im_media_enabled'",
    )
    assert audits == 1
    assert (await env.instance(a["id"]))["im_media"]["enabled"] is True


async def test_image_and_text_file_are_sent_with_signed_ex(env: ImEnv) -> None:
    a, token = await _agent(env, "甲")
    owner = admin_im_user_id(env.owner_id)
    conv = "si_" + "_".join(sorted([a["im_user_id"], owner]))
    assert await _open_dm(env, a["id"]) == conv
    sent = ok(
        await call(
            env,
            token,
            "im_send",
            {
                "target": f"user:{owner}",
                "text": "报告在这里",
                "attachments": [
                    {"data_base64": base64.b64encode(png(40, 30)).decode()},
                    {"text_content": "# 周报\n\n一切正常\n", "filename": "weekly.md"},
                ],
            },
        )
    )
    assert sent["status"] == "sent"
    assert [m["kind"] for m in sent["messages"]] == ["text", "image", "file"]
    # 执行记录里不放附件内容，只记长度
    recorded = await run_sql(
        env.database_url,
        "SELECT arguments_display::text FROM workspace.ai_tool_executions"
        " WHERE tool_name = 'im_send' AND status = 'succeeded'",
    )
    assert "一切正常" not in recorded and "iVBOR" not in recorded
    assert "不记录" in recorded and "weekly.md" in recorded
    stored = [m for m in env.fake.messages[conv] if m["sendID"] == a["im_user_id"]]
    assert [m["contentType"] for m in stored] == [101, 102, 105]
    file_msg = _last(env, conv, a["im_user_id"])
    assert file_msg["content"]["fileName"] == "weekly.md"
    assert "/im/obj/m/" in file_msg["content"]["sourceUrl"]
    ex = json.loads(stored[1]["ex"])
    assert set(ex) == {"myws_key", "myws_sig"}
    used = await run_sql(
        env.database_url,
        "SELECT count(*) FROM workspace.im_media WHERE status = 'used' AND conversation_id = $1",
        conv,
    )
    assert used == 2
    # 管理员按附件下载文件
    media_id = file_msg["content"]["uuid"]
    download = await env.client.get(f"/api/v1/im/media/{media_id}", headers=env.headers())
    assert download.status_code == 200
    assert download.headers["content-disposition"].startswith("attachment;")
    assert download.headers["content-type"] == "application/octet-stream"
    assert download.content == "# 周报\n\n一切正常\n".encode()
    # 实例经接入入口按能力地址取同一个文件：也是附件下载
    path = "/im/obj/m/" + file_msg["content"]["sourceUrl"].split("/im/obj/m/")[1]
    fetched = await env.client.get(path, headers=INTRANET)
    assert fetched.status_code == 200 and fetched.content == download.content
    assert fetched.headers["content-disposition"].startswith("attachment;")


async def test_blocked_types_secrets_and_private_urls(env: ImEnv) -> None:
    _, token = await _agent(env, "甲")
    owner = admin_im_user_id(env.owner_id)
    target = f"user:{owner}"

    async def attempt(attachment: dict[str, Any]) -> dict[str, Any]:
        return tool_error(
            await call(env, token, "im_send", {"target": target, "attachments": [attachment]})
        )

    exe = await attempt(
        {"data_base64": base64.b64encode(b"MZ\x90\x00binary").decode(), "filename": "a.pdf"}
    )
    assert exe["code"] == "ATTACHMENT_TYPE_NOT_ALLOWED"
    html = await attempt(
        {"text_content": "<html><script>x</script></html>", "filename": "page.txt"}
    )
    assert html["code"] == "ATTACHMENT_TYPE_NOT_ALLOWED"
    key = "-----BEGIN " + "PRIVATE KEY-----\nabc\n"  # 拼起来写，避免仓库扫描误报
    secret = await attempt({"text_content": key, "filename": "notes.txt"})
    assert secret["code"] == "ATTACHMENT_SECRET_BLOCKED"
    leaked = await run_sql(
        env.database_url,
        "SELECT count(*) FROM workspace.ai_tool_executions"
        " WHERE arguments_display::text LIKE '%PRIVATE KEY%'",
    )
    assert leaked == 0  # 被拦下的密钥也不进执行记录
    private = await attempt({"url": "http://127.0.0.1/secret"})
    assert private["code"] == "ATTACHMENT_FETCH_FAILED"
    redirected = await attempt({"url": f"http://{FILE_HOST}/moved"})
    assert redirected["code"] == "ATTACHMENT_FETCH_FAILED"
    missing = await attempt({"url": f"http://{FILE_HOST}/nope"})
    assert missing["code"] == "ATTACHMENT_FETCH_FAILED" and "404" in missing["message"]
    two_sources = await attempt({"url": f"http://{FILE_HOST}/pic.png", "ref": "im_media:x"})
    assert two_sources["code"] == "VALIDATION_FAILED"
    fetched = ok(
        await call(
            env,
            token,
            "im_send",
            {"target": target, "attachments": [{"url": f"http://{FILE_HOST}/pic.png"}]},
        )
    )
    assert fetched["status"] == "sent" and fetched["messages"][0]["kind"] == "image"


async def test_video_gets_duration_and_default_cover(env: ImEnv) -> None:
    a, token = await _agent(env, "甲")
    owner = admin_im_user_id(env.owner_id)
    conv = "si_" + "_".join(sorted([a["im_user_id"], owner]))
    await _open_dm(env, a["id"])
    sent = ok(
        await call(
            env,
            token,
            "im_send",
            {
                "target": f"user:{owner}",
                "attachments": [
                    {"data_base64": base64.b64encode(mp4(5)).decode(), "filename": "demo.mp4"}
                ],
            },
        )
    )
    assert sent["messages"][0]["kind"] == "video"
    video = _last(env, conv, a["im_user_id"])
    assert video["contentType"] == 104
    content = video["content"]
    assert content["duration"] == 5 and content["videoType"] == "video/mp4"
    assert content["snapshotWidth"] == 320 and content["snapshotHeight"] == 180
    cover = await run_sql(
        env.database_url,
        "SELECT status FROM workspace.im_media WHERE id = $1",
        uuid.UUID(content["snapshotUUID"]),
    )
    assert cover == "used"
    ranged = await env.client.get(
        f"/api/v1/im/media/{content['videoUUID']}",
        headers={**env.headers(), "Range": "bytes=0-9"},
    )
    assert ranged.status_code == 206 and ranged.content == mp4(5)[:10]
    assert ranged.headers["content-range"].endswith(f"/{len(mp4(5))}")
    assert agent_media.mp4_duration(mp4(90)) == 90


async def test_callback_only_accepts_media_sent_via_workspace(env: ImEnv) -> None:
    a, _ = await _agent(env, "甲")
    owner = admin_im_user_id(env.owner_id)
    direct = msg(a["im_user_id"], recv_id=owner)
    direct["contentType"] = 102
    assert await env.verdict(SINGLE, direct) == 5007
    # 文字照常放行
    text = msg(a["im_user_id"], recv_id=owner)
    assert await env.verdict(SINGLE, text) == 0
    # 经工作台发：ex 带签名，而且对应一条正在发送的记录
    row_id = uuid.uuid4()
    await run_sql(
        env.database_url,
        "INSERT INTO workspace.agent_im_outbox (id, workspace_id, instance_id, target,"
        " conversation_id, client_key, request_hash, content_type, status)"
        " VALUES ($1, $2, $3, $4, 'si_x', 'k-1', 'h', 102, 'sending')",
        row_id,
        uuid.UUID(env.workspace_id),
        uuid.UUID(a["id"]),
        f"user:{owner}",
    )
    secret = env.app.state.settings.openim_secret.get_secret_value()
    direct["ex"] = signed_ex(secret, row_id)
    assert await env.verdict(SINGLE, direct) == 0
    forged = msg(a["im_user_id"], recv_id=owner)
    forged["contentType"] = 105
    forged["ex"] = signed_ex("wrong-secret", row_id)
    assert await env.verdict(SINGLE, forged) == 5007
    group = msg(a["im_user_id"], group_id="g-none")
    group["contentType"] = 104
    assert await env.verdict(GROUP, group) == 5007


async def test_admin_uploads_and_sends_a_file(env: ImEnv) -> None:
    a, _ = await _agent(env, "甲")
    owner = admin_im_user_id(env.owner_id)
    conv = await _open_dm(env, a["id"])
    assert conv == "si_" + "_".join(sorted([a["im_user_id"], owner]))
    upload = await env.client.post(
        "/api/v1/im/uploads?name=plan.csv",
        content=b"a,b\n1,2\n",
        headers={**env.headers(write=True), "Content-Type": "text/csv"},
    )
    assert upload.status_code == 201, upload.text
    body = upload.json()
    assert body["kind"] == "file" and body["name"] == "plan.csv"
    blocked = await env.client.post(
        "/api/v1/im/uploads?name=run.sh",
        content=b"#!/bin/sh\necho hi\n",
        headers={**env.headers(write=True), "Content-Type": "text/plain"},
    )
    assert blocked.status_code == 422
    sent = await env.client.post(
        f"/api/v1/im/conversations/{conv}/messages",
        json={"kind": "file", "upload_id": body["upload_id"]},
        headers={**env.headers(write=True), "Idempotency-Key": str(uuid.uuid4())},
    )
    assert sent.status_code == 202, sent.text
    out = sent.json()
    assert out["kind"] == "file" and out["attachment"]["name"] == "plan.csv"
    assert out["attachment"]["media_id"] == body["upload_id"]
    wrong_kind = await env.client.post(
        "/api/v1/im/uploads?name=x.csv",
        content=b"x\n",
        headers={**env.headers(write=True), "Content-Type": "text/csv"},
    )
    mismatch = await env.client.post(
        f"/api/v1/im/conversations/{conv}/messages",
        json={"kind": "image", "upload_id": wrong_kind.json()["upload_id"]},
        headers={**env.headers(write=True), "Idempotency-Key": str(uuid.uuid4())},
    )
    assert mismatch.status_code == 422


async def test_file_and_video_keep_their_links_after_projection(env: ImEnv) -> None:
    """OpenIM 的消息补进投影后，文件、视频仍然带 media_id（管理员可下载）：投影不能把内容清空。"""
    a, token = await _agent(env, "甲")
    owner = admin_im_user_id(env.owner_id)
    conv = await _open_dm(env, a["id"])
    sent = ok(
        await call(
            env,
            token,
            "im_send",
            {
                "target": f"user:{owner}",
                "attachments": [
                    {"text_content": "# 周报\n", "filename": "weekly.md"},
                    {"data_base64": base64.b64encode(mp4(3)).decode(), "filename": "demo.mp4"},
                ],
            },
        )
    )
    assert [m["kind"] for m in sent["messages"]] == ["file", "video"]
    await worker.reconcile_im(await _worker_ctx(env))
    listed = await env.client.get(
        f"/api/v1/im/conversations/{conv}/messages", headers=env.headers()
    )
    assert listed.status_code == 200
    mine = [m for m in listed.json()["items"] if m["sender_im_id"] == a["im_user_id"]]
    by_kind = {m["kind"]: m["attachment"] for m in mine}
    assert set(by_kind) == {"file", "video"}
    assert by_kind["file"]["media_id"] and by_kind["file"]["name"] == "weekly.md"
    assert by_kind["video"]["media_id"] and by_kind["video"]["duration_seconds"] == 3
    assert by_kind["video"]["cover_media_id"]
    download = await env.client.get(
        f"/api/v1/im/media/{by_kind['file']['media_id']}", headers=env.headers()
    )
    assert download.status_code == 200 and download.content == "# 周报\n".encode()
