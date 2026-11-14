"""平台额度的查看与额度用完后的自动续跑（T-0237，docs/v2/LLM_BRIDGE_QUOTA_PLAN.md）。"""

import asyncio
import time
from pathlib import Path
from typing import Any

from bridge import quota
from bridge.channels import CHANNELS, FAKE_CHANNELS
from bridge.config import Settings
from bridge.drivers.fake import FakeDriver
from bridge.engine import RESUME_TEXT, SYSTEM_CALLER, Engine
from bridge.store import SCHEMA_VERSION, Store
from tests.conftest import ADMIN, Bridge


async def _session(bridge: Bridge, caller: str = "c1") -> str:
    created = await bridge.tool(
        caller, "llm_session_create", {"platform": "fake", "mode": "chat", "model": "fake-1"}
    )
    return str(created["session_id"])


async def _send(bridge: Bridge, session: str, text: str, rid: str, wait: float = 10) -> Any:
    return await bridge.tool(
        "c1",
        "llm_send",
        {"session_id": session, "text": text, "request_id": rid, "wait_seconds": wait},
    )


async def _until(test: Any, seconds: float = 10) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if test():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("等不到")


def _texts(bridge: Bridge, session: str) -> list[tuple[str, str, str]]:
    return [
        (t["text"], t["status"], t["caller_id"])
        for t in bridge.engine.store.list_turns(session_id=session)
    ]


async def test_quota_interruption_pauses_the_platform_and_resumes_after_reset(
    bridge: Bridge,
) -> None:
    """额度用完：回合记成中断，平台暂停、排队的不去撞；到重置时间后先发“继续”，再处理排队的。"""
    a, b = await _session(bridge), await _session(bridge)
    bridge.fake.quota_until = time.time() + 0.8
    hit = await _send(bridge, a, "跑一个长任务", "t1")
    assert hit["status"] == "interrupted"
    assert hit["failure"]["kind"] == "quota_exceeded"
    assert hit["resume"]["state"] == "pending" and hit["resume"]["at"] >= bridge.fake.quota_until
    # 中断期间：这个会话和别的会话再发的都排队，没有去撞限额
    later = await _send(bridge, a, "后面的", "t2", wait=0)
    other = await _send(bridge, b, "别的会话", "t3", wait=0)
    assert later["status"] == other["status"] == "queued"
    await asyncio.sleep(0.2)
    assert bridge.fake.running == 0
    status = (await bridge.admin("GET", "/admin/status")).json()
    paused = status["quota"]["fake"]
    assert paused["paused_until"] >= bridge.fake.quota_until and paused["waiting_resume"] == 1
    assert paused["pause_estimated"] is False
    (window,) = paused["windows"]
    assert (window["kind"], window["status"], window["used_percent"]) == (
        "five_hour",
        "exceeded",
        100,
    )

    # 到点：系统发的“继续”排在这个会话队列的最前面，然后才是之前排队的
    for turn in (later, other):
        done = await bridge.tool(
            "c1", "llm_turn_get", {"turn_id": turn["turn_id"], "wait_seconds": 10}
        )
        assert done["status"] == "completed", done
    assert _texts(bridge, a) == [
        ("跑一个长任务", "interrupted", "c1"),
        (RESUME_TEXT, "completed", SYSTEM_CALLER),
        ("后面的", "completed", "c1"),
    ]
    native = bridge.engine.store.get_session(a)["native_id"]
    assert [m.text for m in bridge.fake.history[native] if m.role == "user"] == [
        RESUME_TEXT,
        "后面的",
    ]
    original = await bridge.tool(
        "c1", "llm_turn_get", {"turn_id": hit["turn_id"], "wait_seconds": 0}
    )
    resumed = bridge.engine.store.list_turns(session_id=a)[1]
    assert original["resume"] == {
        "state": "done",
        "at": original["resume"]["at"],
        "attempt": 0,
        "resumed_by": resumed["id"],
    }
    assert (resumed["resume_attempt"], resumed["resumes_turn_id"]) == (1, hit["turn_id"])
    # 会话里看得出是系统发的；平台恢复了，额度是新读到的
    view = (await bridge.admin("GET", f"/admin/sessions/{a}")).json()
    asked = [(m["text"], m.get("by")) for m in view["messages"] if m["role"] == "user"]
    assert asked[1] == (RESUME_TEXT, "系统（自动续跑）")
    first_reply = next(m for m in view["messages"] if m["role"] == "assistant")
    assert first_reply["status"] == "interrupted" and first_reply["resume"]["state"] == "done"
    after = (await bridge.admin("GET", "/admin/quota")).json()["platforms"]["fake"]
    assert after["paused_until"] is None and after["waiting_resume"] == 0
    assert after["windows"][0]["status"] == "ok" and after["windows"][0]["used_percent"] == 40
    assert after["tokens_5h"] > 0 and after["can_refresh"] is False


async def test_resume_limits_switch_and_manual_control(bridge: Bridge) -> None:
    saved = await bridge.admin(
        "PUT",
        "/admin/settings",
        json={"auto_resume": False, "auto_resume_max": 1, "resume_text": "  接着做  "},
    )
    assert saved.status_code == 200, saved.text
    assert (saved.json()["auto_resume"], saved.json()["resume_text"]) == (False, "接着做")
    for bad in (
        {"resume_text": ""},
        {"resume_text": "x" * 201},
        {"auto_resume_max": 0},
        {"auto_resume": 1},
    ):
        assert (await bridge.admin("PUT", "/admin/settings", json=bad)).status_code == 422

    # 开关关着：到点只恢复平台的队列，不替人发“继续”
    a = await _session(bridge)
    bridge.fake.quota_until = time.time() + 0.4
    hit = await _send(bridge, a, "任务甲", "m1")
    queued = await _send(bridge, a, "排队的", "m2", wait=0)
    done = await bridge.tool(
        "c1", "llm_turn_get", {"turn_id": queued["turn_id"], "wait_seconds": 10}
    )
    assert done["status"] == "completed"
    assert [t[0] for t in _texts(bridge, a)] == ["任务甲", "排队的"]
    skipped = await bridge.tool(
        "c1", "llm_turn_get", {"turn_id": hit["turn_id"], "wait_seconds": 0}
    )
    assert skipped["resume"]["state"] == "skipped"
    # 人点“现在就试”：照样发
    forced = await bridge.admin("POST", f"/admin/turns/{hit['turn_id']}/resume")
    assert forced.status_code == 200 and forced.json()["resume"]["state"] == "forced"
    await _until(lambda: len(_texts(bridge, a)) == 3 and _texts(bridge, a)[-1][1] == "completed")
    assert _texts(bridge, a)[-1] == ("接着做", "completed", SYSTEM_CALLER)
    again = await bridge.admin("POST", f"/admin/turns/{hit['turn_id']}/resume")
    assert again.status_code == 409  # 已经续跑过了

    # 打开开关、最多续 1 次；平台不说什么时候重置（按估计的时间等）；
    # 续跑的那一次又被打断 → 不再自动续
    await bridge.admin("PUT", "/admin/settings", json={"auto_resume": True})
    bridge.engine.settings.quota_fallback_seconds = 0.3
    bridge.fake.quota_reports_reset = False
    b = await _session(bridge)
    bridge.fake.quota_until = time.time() + 3600
    first = await _send(bridge, b, "任务乙", "n1")
    assert first["status"] == "interrupted"
    estimated = (await bridge.admin("GET", "/admin/quota")).json()["platforms"]["fake"]
    assert estimated["pause_estimated"] is True
    await _until(lambda: len(_texts(bridge, b)) == 2 and _texts(bridge, b)[1][1] == "interrupted")
    second = bridge.engine.store.list_turns(session_id=b)[1]
    assert (second["text"], second["resume_attempt"], second["resume_state"]) == (
        "接着做",
        1,
        "exhausted",
    )
    await asyncio.sleep(0.6)
    assert len(_texts(bridge, b)) == 2  # 超过次数：停下等人

    # 人说“不要自动继续”：到点不发；不是被额度打断的回合不能这样操作
    bridge.fake.quota_reports_reset = True
    bridge.fake.quota_until = 0
    await bridge.admin("PUT", "/admin/settings", json={"auto_resume_max": 3})
    await _until(
        lambda: (
            (bridge.engine.store.pauses().get("fake") or {"until": 0})["until"] <= time.time() + 1
        )
    )
    c = await _session(bridge)
    await _until(lambda: not bridge.engine.store.pauses())
    bridge.fake.quota_until = time.time() + 0.5
    third = await _send(bridge, c, "任务丙", "o1")
    cancelled = await bridge.admin("DELETE", f"/admin/turns/{third['turn_id']}/resume")
    assert cancelled.status_code == 200 and cancelled.json()["resume"]["state"] == "cancelled"
    await asyncio.sleep(0.9)
    assert [t[0] for t in _texts(bridge, c)] == ["任务丙"]
    ordinary = await _send(bridge, c, "普通的", "o2")
    assert ordinary["status"] == "completed"
    for response in (
        await bridge.admin("POST", f"/admin/turns/{ordinary['turn_id']}/resume"),
        await bridge.admin("DELETE", f"/admin/turns/{ordinary['turn_id']}/resume"),
    ):
        assert response.status_code == 409
    assert (await bridge.admin("POST", "/admin/turns/bt_none/resume")).status_code == 404


async def test_other_failures_are_not_resumed(bridge: Bridge) -> None:
    """普通的限速、平台出错、人点的停止，都不算额度中断，不会自动重发。"""
    a = await _session(bridge)
    for kind in ("rate_limited", "platform_error"):
        failed = await _send(bridge, a, f"fail:{kind}", f"f-{kind}")
        assert failed["status"] == "failed" and "resume" not in failed
    assert not bridge.engine.store.pauses()
    assert (await _send(bridge, a, "照常", "ok"))["status"] == "completed"


async def test_pending_resume_survives_a_restart(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path, admin_token=ADMIN, enable_fake=True, resume_margin_seconds=0
    )
    store = Store(settings.db_path)
    session = store.create_session(
        platform="fake",
        mode="chat",
        slot="fake",
        native_id="n1",
        model="fake-1",
        caller_id="c1",
        caller_label="c1",
        workdir=str(tmp_path / "w"),
    )
    hit = store.create_turn(session["id"], "c1", "r1", "被打断的")
    store.update_turn(
        hit["id"],
        status="interrupted",
        started_at=time.time() - 1,
        finished_at=time.time(),
        error_kind="quota_exceeded",
        resume_at=time.time() + 0.3,
        resume_state="pending",
    )
    store.set_pause("fake", time.time() + 0.3, False, "额度用完")
    waiting = store.create_turn(session["id"], "c1", "r2", "重启前排队的")
    engine = Engine(store, {"fake": FakeDriver()}, CHANNELS + FAKE_CHANNELS, settings)
    await engine.startup()
    done = await engine.wait_turn(waiting["id"], 10)
    assert done["status"] == "completed"
    turns = store.list_turns(session_id=session["id"])
    assert [(t["text"], t["status"]) for t in turns] == [
        ("被打断的", "interrupted"),
        (RESUME_TEXT, "completed"),
        ("重启前排队的", "completed"),
    ]
    assert store.get_turn(hit["id"])["resume_state"] == "done" and not store.pauses()
    assert store._one("SELECT value FROM meta WHERE key = 'schema_version'")["value"] == str(
        SCHEMA_VERSION
    )
    await engine.shutdown()
    store.close()


async def test_platforms_that_can_be_asked_are_checked_before_resuming(bridge: Bridge) -> None:
    """能随时查额度的平台（Codex）：到点先查，没回来就按新的重置时间接着等；页面的“刷新”实查一次。"""
    bridge.fake.can_query_quota = True
    a = await _session(bridge)
    bridge.fake.quota_until = time.time() + 0.3
    bridge.fake.quota_reports_reset = False  # 失败时没给重置时间：按估计的时间去查
    bridge.engine.settings.quota_fallback_seconds = 0.1
    hit = await _send(bridge, a, "任务", "q1")
    assert hit["status"] == "interrupted"
    bridge.fake.quota_until = time.time() + 0.9  # 到了估计的时间，平台说还没重置
    await asyncio.sleep(0.4)
    assert len(_texts(bridge, a)) == 1
    refreshed = (await bridge.admin("GET", "/admin/quota", params={"refresh": True})).json()
    fake = refreshed["platforms"]["fake"]
    assert fake["can_refresh"] is True and fake["windows"][0]["status"] == "exceeded"
    assert abs(fake["paused_until"] - bridge.fake.quota_until) < 0.5
    await _until(lambda: len(_texts(bridge, a)) == 2 and _texts(bridge, a)[1][1] == "completed")


def test_quota_shapes_and_reset_parsing() -> None:
    codex = quota.from_codex(
        {
            "rateLimits": {
                "planType": "plus",
                "rateLimitReachedType": "primary",
                "primary": {"usedPercent": 100, "resetsAt": 1900000000, "windowDurationMins": 300},
                "secondary": {
                    "usedPercent": 41,
                    "resetsAt": 1900500000,
                    "windowDurationMins": 10080,
                },
            }
        }
    )
    assert [
        (w["kind"], w["used_percent"], w["resets_at"], w["status"], w["plan"]) for w in codex
    ] == [
        ("five_hour", 100, 1900000000, "exceeded", "plus"),
        ("weekly", 41, 1900500000, "ok", "plus"),
    ]
    assert quota.from_codex({}) == [] and quota.from_codex({"rateLimits": {"primary": None}}) == []

    claude = quota.from_rate_limit_event(
        {
            "type": "rate_limit_event",
            "rate_limit_info": {
                "status": "allowed_warning",
                "rateLimitType": "five_hour",
                "resetsAt": 1900000000,
                "utilization": 0.87,
            },
        }
    )
    assert claude == [
        {
            "kind": "five_hour",
            "used_percent": 87.0,
            "resets_at": 1900000000.0,
            "status": "warning",
            "plan": None,
        }
    ]
    buddy = quota.from_rate_limit_event(
        {"kind": "seven_day", "percentUsed": 23.5, "resetsAt": "2030-03-17T17:46:40Z"}
    )
    assert (buddy[0]["kind"], buddy[0]["used_percent"], buddy[0]["status"]) == (
        "weekly",
        23.5,
        "ok",
    )
    assert buddy[0]["resets_at"] == 1900000000.0
    # 读不出来就是没有，不猜
    for nothing in ({}, {"rate_limit_info": {}}, {"rate_limit_info": {"foo": 1}}, "x"):
        assert quota.from_rate_limit_event(nothing) == []

    now = 1_800_000_000.0
    assert quota.parse_reset("limit reached, resets at 1900000000", now) == 1900000000
    assert quota.parse_reset("Your limit resets in 3h 20m", now) == now + 3 * 3600 + 20 * 60
    assert quota.parse_reset("resets in 45 minutes", now) == now + 45 * 60
    assert quota.parse_reset("重置于 2030-03-17T17:46:40Z", now) == 1900000000
    assert quota.parse_reset("something else broke", now) is None
    assert quota.is_quota_text("You've hit your usage limit") and quota.is_quota_text(
        "5-hour limit reached"
    )
    for ordinary in ("429 too many requests", "server overloaded", "rate limit, slow down"):
        assert not quota.is_quota_text(ordinary)
