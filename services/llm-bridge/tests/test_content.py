"""会话内容落库（T-0236 第二步，docs/v2/LLM_BRIDGE_QUEUE_PLAN.md 第 3 节）。

每条提问、回答、过程都在 messages 里；旧数据回填；官方程序里已有的会话第一次打开时导入；
图片和文件作为附件随消息发出去、模型产出的文件收回来、能下载。
"""

import time
from pathlib import Path
from typing import Any

import pytest

from bridge import attachments
from bridge.drivers.base import Message, NativeSession
from bridge.engine import PREVIEW_CHARS, preview
from bridge.store import Store
from tests.conftest import Bridge

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def _read(path: Path) -> bytes:
    return path.read_bytes()


def _mode(path: Path) -> str:
    return oct(path.stat().st_mode)[-3:]


async def _session(bridge: Bridge, mode: str = "chat") -> str:
    arguments: dict[str, Any] = {"platform": "fake", "mode": mode, "model": "fake-1"}
    if mode == "dev":  # 开发会话要有一个开发环境
        made = await bridge.admin(
            "POST",
            "/admin/environments",
            json={
                "platform": "fake",
                "mode": "dev",
                "name": f"环境 {time.time_ns()}",
                "sandbox": "workspace_write",
            },
        )
        assert made.status_code in (200, 201), made.text
        arguments["environment"] = made.json()["id"]
    created = await bridge.tool("c1", "llm_session_create", arguments)
    return str(created["session_id"])


async def _send(bridge: Bridge, session: str, text: str, rid: str, **extra: Any) -> Any:
    arguments = {"session_id": session, "text": text, "request_id": rid, "wait_seconds": 10}
    return await bridge.tool("c1", "llm_send", {**arguments, **extra})


async def _conversation(bridge: Bridge, session: str) -> dict[str, Any]:
    response = await bridge.admin("GET", f"/admin/sessions/{session}")
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()
    return data


async def _upload(bridge: Bridge, session: str, name: str, data: bytes) -> Any:
    return await bridge.admin(
        "POST", f"/admin/sessions/{session}/attachments", params={"name": name}, content=data
    )


async def test_every_ask_answer_and_step_is_stored(bridge: Bridge) -> None:
    """提问、回答、命令、改动的文件、工具调用和结果都各存一行，会话内容从这些行里拼出来。"""
    session = await _session(bridge, "dev")
    await _send(bridge, session, "work:0", "t1")
    await _send(bridge, session, "read:README.md", "t2")
    rows = bridge.engine.store.list_messages(session)
    assert [(m["role"], m["kind"]) for m in rows] == [
        ("user", "text"),
        ("assistant", "text"),
        ("process", "command"),
        ("process", "files"),
        ("assistant", "text"),
        ("user", "text"),
        ("process", "tool"),
        ("process", "tool_result"),
        ("assistant", "text"),
    ]
    assert [m["seq"] for m in rows] == list(range(1, 10))
    assert rows[0]["by"] == "c1" and rows[0]["delivery"] == "turn"
    assert rows[6]["data"]["name"] == "Read" and "README.md" in rows[6]["text"]

    view = await _conversation(bridge, session)
    asked, first, again, second = view["messages"]
    assert (asked["text"], asked["by"], asked["attachments"]) == ("work:0", "c1", [])
    assert [s["kind"] for s in first["steps"]] == ["message", "command", "files"]
    assert first["text"] == "[fake-1] work:0"
    assert [(s["kind"], s.get("name")) for s in second["steps"]] == [
        ("tool", "Read"),
        ("tool_result", "Read"),
    ]
    assert again["text"] == "read:README.md" and second["text"] == "[fake-1] read:README.md"


async def test_long_tool_results_are_cut_and_marked(bridge: Bridge) -> None:
    session = await _session(bridge)
    turn = await _send(bridge, session, "你好", "t1")
    store = bridge.engine.store
    from bridge.store import MAX_MESSAGE_CHARS, message_of

    made = message_of("tool_result", {"name": "Read", "text": "x" * (MAX_MESSAGE_CHARS + 10)})
    assert made is not None
    role, kind, text, data = made
    assert data["truncated"] is True and text.endswith("（内容太长，后面的没有保存）")
    assert len(text) < MAX_MESSAGE_CHARS + 40
    store.add_message(session, turn["turn_id"], role, kind, text, data=data)
    reply = (await _conversation(bridge, session))["messages"][-1]
    assert reply["steps"][-1]["truncated"] is True


async def test_editing_a_queued_message_updates_what_is_stored(bridge: Bridge) -> None:
    session = await _session(bridge)
    await _send(bridge, session, "sleep:0.5", "t1", wait_seconds=0)
    queued = await _send(bridge, session, "原来的", "t2", wait_seconds=0)
    edited = await bridge.admin(
        "PATCH", f"/admin/turns/{queued['turn_id']}", json={"text": "改过的"}
    )
    assert edited.status_code == 200, edited.text
    asked = bridge.engine.store.user_message(queued["turn_id"])
    assert asked is not None and asked["text"] == "改过的"


def test_existing_turns_and_events_are_backfilled(tmp_path: Path) -> None:
    """升级前的库只有 turns / events：升级时回填进 messages，旧数据不丢，也不重复回填。"""
    path = tmp_path / "old.sqlite3"
    store = Store(path)
    session = store.create_session(
        platform="fake", mode="chat", slot="fake", native_id="n1", model="m", workdir="/w"
    )
    turn = store.create_turn(session["id"], "c1", "r1", "老问题")
    store.add_event(session["id"], turn["id"], "queued", {"by": "甲"})
    store.add_event(session["id"], turn["id"], "command", {"command": "ls"})
    store.add_event(session["id"], turn["id"], "message", {"text": "中间说的"})
    store.update_turn(turn["id"], status="completed", result_text="最后的回答", finished_at=5.0)
    # 回到升级前的样子：没有 messages，也没有回填过的记号
    store._db.execute("DELETE FROM messages")
    store._db.execute("DELETE FROM meta WHERE key = 'messages_backfilled'")
    store.close()

    upgraded = Store(path)
    rows = upgraded.list_messages(session["id"])
    assert [(m["role"], m["kind"], m["text"], m["by"]) for m in rows] == [
        ("user", "text", "老问题", "甲"),
        ("process", "command", "ls", None),
        ("assistant", "text", "中间说的", None),
        ("assistant", "text", "最后的回答", None),
    ]
    upgraded.close()
    assert len(Store(path).list_messages(session["id"])) == 4


def test_session_previews_show_the_latest_real_question(tmp_path: Path) -> None:
    """会话列表的一行摘要：最近一次提问（不算压缩、交接这类内部回合），脱敏、压成一行、截短；清掉内容的会话没有。"""
    store = Store(tmp_path / "p.sqlite3")
    made = lambda n: store.create_session(  # noqa: E731
        platform="fake", mode="chat", slot="fake", native_id=n, model="m", workdir="/w"
    )["id"]
    a, b, c = made("n1"), made("n2"), made("n3")
    store.create_turn(a, "c1", "r1", "第一个问题")
    store.create_turn(a, "c1", "r2", "  第二个\n\n  问题  ")
    store.create_turn(a, "c1", "r3", "请把之前的内容压成摘要", kind="compact")
    store.create_turn(a, "c1", "r4", "写一份交接摘要", kind="summary")
    secret = "token=sk-abcdefghijklmnopqrstuvwxyz0123456789 "
    store.create_turn(b, "c1", "r5", secret + "很长" * 100)
    store.create_turn(c, "c1", "r6", "会被清掉")
    store.purge_session(c)

    found = store.previews([a, b, c, "bs_none"])
    assert found[a] == "  第二个\n\n  问题  "  # 原文；压成一行在 preview() 里做
    assert c not in found and "bs_none" not in found
    assert preview(found[a]) == "第二个 问题"
    long = preview(found[b])
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in long
    assert len(long) == PREVIEW_CHARS and long.endswith("…")
    assert store.previews([]) == {}
    store.close()


async def test_external_sessions_are_imported_once_and_can_be_reimported(bridge: Bridge) -> None:
    """官方程序里已有的会话：第一次打开时读一次存下来，之后读库；“重新导入”再读一次。"""
    fake = bridge.fake
    fake.history["native-1"] = [
        Message("user", "老问题", 100.0),
        Message("assistant", "老回答", 101.0),
    ]
    fake.external = [NativeSession("native-1", "旧会话", None, None, 100.0, 101.0)]
    listed = await bridge.admin("GET", "/admin/sessions", params={"sync": "true"})
    assert listed.status_code == 200, listed.text
    session = next(s for s in listed.json()["sessions"] if s["origin"] == "external")["session_id"]
    assert bridge.engine.store.list_messages(session) == []

    first = await _conversation(bridge, session)
    assert [(m["role"], m["text"], m["imported"]) for m in first["messages"]] == [
        ("user", "老问题", True),
        ("assistant", "老回答", True),
    ]
    # 之后读库：官方程序那边读不到了也照样能看
    reads = 0

    async def broken(native_id: str, workdir: str | None) -> list[Message]:
        nonlocal reads
        reads += 1
        raise AssertionError("不应再去读官方程序")

    original, fake.read = fake.read, broken  # type: ignore[method-assign]
    assert (await _conversation(bridge, session))["messages"] == first["messages"]
    assert reads == 0
    fake.read = original  # type: ignore[method-assign]

    # 经这座桥接着发的回合按回合存；重新导入时官方程序记录里的这一问一答不重复存
    takeover = {"session_id": session, "text": "接着问", "request_id": "x1", "wait_seconds": 10}
    done = await bridge.tool("c1", "llm_send", takeover, scopes="write,account,takeover")
    assert done["status"] == "completed", done
    fake.history["native-1"].append(Message("user", "在官方程序里又问的", 300.0))
    again = await bridge.admin("POST", f"/admin/sessions/{session}/reimport")
    assert again.status_code == 200, again.text
    texts = [m["text"] for m in again.json()["messages"]]
    assert texts == ["老问题", "老回答", "在官方程序里又问的", "接着问", "[None] 接着问"], texts
    own = await _session(bridge)
    assert (await bridge.admin("POST", f"/admin/sessions/{own}/reimport")).status_code == 409


async def test_images_go_to_the_model_and_can_be_downloaded(bridge: Bridge) -> None:
    session = await _session(bridge)
    uploaded = await _upload(bridge, session, "../../截图 1.png", PNG)
    assert uploaded.status_code == 201, uploaded.text
    image = uploaded.json()
    assert (image["name"], image["media_type"], image["size"]) == (
        "截图 1.png",
        "image/png",
        len(PNG),
    )

    # 不收的：别的类型、内容和扩展名对不上、空文件、太大、对话会话里的非图片、别的会话的附件
    for name, data in (
        ("a.exe", b"MZ"),
        ("a.svg", b"<svg/>"),
        ("b.png", b"not a png"),
        ("c.png", b""),
        ("d.png", PNG + b"0" * attachments.MAX_BYTES),
        ("e.txt", b"hello"),
    ):
        refused = await _upload(bridge, session, name, data)
        assert refused.status_code == 422, (name, refused.text)
    other = await _session(bridge)
    wrong = await _send(bridge, other, "看图", "w1", attachments=[image["id"]])
    assert wrong["error"]["code"] == "VALIDATION_FAILED"

    turn = await _send(bridge, session, "看图", "t1", attachments=[image["id"]])
    assert turn["status"] == "completed" and turn["result"].endswith("（收到 1 张图片）"), turn
    ((path, media_type),) = bridge.fake.last_images
    stored_image = bridge.engine.attachment_root / session / image["id"]
    assert media_type == "image/png" and path == str(stored_image)
    assert _read(stored_image) == PNG and _mode(stored_image) == "600"
    asked = (await _conversation(bridge, session))["messages"][0]
    assert [a["id"] for a in asked["attachments"]] == [image["id"]]
    # 一个附件只能随一条消息发出去；发出去之后不能再撤
    reused = await _send(bridge, session, "再看", "t2", attachments=[image["id"]])
    assert reused["error"]["code"] == "CONFLICT"
    gone = await bridge.admin("DELETE", f"/admin/sessions/{session}/attachments/{image['id']}")
    assert gone.status_code == 409

    got = await bridge.admin("GET", f"/admin/sessions/{session}/attachments/{image['id']}")
    assert got.status_code == 200 and got.content == PNG
    assert got.headers["content-type"] == "image/png"
    assert got.headers["content-disposition"].startswith("inline")
    assert got.headers["x-content-type-options"] == "nosniff"
    missing = await bridge.admin("GET", f"/admin/sessions/{other}/attachments/{image['id']}")
    assert missing.status_code == 404
    assert (
        await bridge.client.get(f"/admin/sessions/{session}/attachments/{image['id']}")
    ).status_code in (401, 403)

    # 还没发出去的可以撤掉，文件一起删
    spare = (await _upload(bridge, session, "多余.png", PNG)).json()
    stored = bridge.engine.attachment_root / session / spare["id"]
    assert stored.is_file()
    dropped = await bridge.admin("DELETE", f"/admin/sessions/{session}/attachments/{spare['id']}")
    assert dropped.status_code == 204 and not stored.exists()


async def test_files_in_dev_sessions_go_in_and_come_out(bridge: Bridge) -> None:
    """开发会话：带来的文件放进工作目录、在提问里注明；模型写出的文件收成附件，能下载。"""
    session = await _session(bridge, "dev")
    note = (await _upload(bridge, session, "需求.txt", b"do this")).json()
    turn = await _send(bridge, session, "write:out/result.txt", "t1", attachments=[note["id"]])
    assert turn["status"] == "completed", turn
    workdir = Path(bridge.engine.store.get_session(session)["workdir"])  # type: ignore[index]
    placed = workdir / "attachments" / f"{note['id'][-6:]}-需求.txt"
    assert placed.read_bytes() == b"do this"
    # 存下来的提问还是人发的原话；给模型的那份后面多一句文件放在哪
    asked, reply = (await _conversation(bridge, session))["messages"]
    assert asked["text"] == "write:out/result.txt"
    assert (
        f"attachments/{placed.name}"
        in bridge.fake.history[
            bridge.engine.store.get_session(session)["native_id"]  # type: ignore[index]
        ][0].text
    )
    (made,) = reply["attachments"]
    assert (made["name"], made["direction"], made["media_type"]) == (
        "result.txt",
        "output",
        "text/plain",
    )
    got = await bridge.admin("GET", f"/admin/sessions/{session}/attachments/{made['id']}")
    assert got.content == b"generated\n"
    assert got.headers["content-disposition"].startswith("attachment")

    # 工作目录外面的文件不收
    outside = await _send(bridge, session, "write:../escaped.txt", "t2")
    assert outside["status"] == "completed"
    assert (await _conversation(bridge, session))["messages"][-1]["attachments"] == []


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("a/b/../c.PNG", "c.PNG"),
        ("..", "file"),
        ("x\x00y:z.txt", "x_y_z.txt"),
        ("  .hidden ", "hidden"),
    ],
)
def test_safe_name(name: str, expected: str) -> None:
    assert attachments.safe_name(name) == expected
    assert len(attachments.safe_name("长" * 300 + ".png")) == 120


def test_stale_uploads_are_swept(tmp_path: Path) -> None:
    store = Store(tmp_path / "s.sqlite3")
    session = store.create_session(
        platform="fake", mode="chat", slot="fake", native_id="n", model="m", workdir="/w"
    )
    row = store.add_attachment(session["id"], "input", "a.png", "image/png", 1, "0" * 64, "p")
    assert store.stale_uploads(time.time() - 60) == []
    assert [r["id"] for r in store.stale_uploads(time.time() + 60)] == [row["id"]]


async def test_a_message_cancelled_while_queued_keeps_its_place(bridge: Bridge) -> None:
    """排队时被取消的消息留在它被取消的位置，不会跑到后面发的消息后面去。"""
    session = await _session(bridge)
    await _send(bridge, session, "sleep:0.4", "t1", wait_seconds=0)
    dropped = await _send(bridge, session, "不发了", "t2", wait_seconds=0)
    await bridge.tool("c1", "llm_turn_cancel", {"turn_id": dropped["turn_id"]})
    done = await _send(bridge, session, "后来的", "t3")
    assert done["status"] == "completed"
    asked = [
        m["text"] for m in (await _conversation(bridge, session))["messages"] if m["role"] == "user"
    ]
    assert asked == ["sleep:0.4", "不发了", "后来的"], asked
