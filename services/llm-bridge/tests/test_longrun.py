"""长时间任务（docs/v2/LLM_BRIDGE_LONGRUN_PLAN.md）：回合进度、预约、作业、循环、llm-task 命令。"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from bridge import session_cli
from bridge.longrun import AUTO_MARK
from tests.conftest import Bridge


async def _chat(bridge: Bridge, caller: str = "owner") -> str:
    made = await bridge.tool(caller, "llm_session_create", {"platform": "fake", "mode": "chat"})
    return str(made["session_id"])


async def _dev(bridge: Bridge, caller: str = "owner", sandbox: str = "exec", **extra: Any) -> str:
    env = await bridge.admin(
        "POST",
        "/admin/environments",
        json={
            "platform": "fake",
            "mode": "dev",
            "name": f"环境-{sandbox}-{uuid.uuid4().hex[:6]}",
            "sandbox": sandbox,
            **extra,
        },
    )
    assert env.status_code == 200, env.text
    made = await bridge.tool(
        caller,
        "llm_session_create",
        {"platform": "fake", "mode": "dev", "environment": env.json()["id"]},
    )
    return str(made["session_id"])


async def _turn_until(
    bridge: Bridge, turn_id: str, check: Any, caller: str = "owner"
) -> dict[str, Any]:
    for _ in range(100):
        view = await bridge.tool(caller, "llm_turn_get", {"turn_id": turn_id})
        if check(view):
            return view
        await asyncio.sleep(0.05)
    raise AssertionError(f"等不到：{view}")


async def test_running_turn_shows_what_it_is_doing(bridge: Bridge) -> None:
    # 回合进行中（5.7）：正在跑的命令、跑了多久、输出末尾、官方程序的后台任务、多久没动静
    session = await _dev(bridge)
    sent = await bridge.tool(
        "owner",
        "llm_send",
        {"session_id": session, "text": "longcmd:2", "request_id": "r1", "wait_seconds": 0},
    )
    view = await _turn_until(
        bridge, sent["turn_id"], lambda v: "activity" in v and "current" in v["activity"]
    )
    activity = view["activity"]
    assert activity["current"]["kind"] == "command" and activity["current"]["text"] == "make test"
    assert activity["current"]["seconds"] >= 0 and activity["quiet_seconds"] >= 0
    assert activity["output_tail"] == "running 1\nrunning 2\npart"
    assert activity["background"][0] | {"started_at": 0} == {
        "task_id": "bg1",
        "status": "running",
        "description": "等 CI",
        "summary": "",
        "started_at": 0,
    }
    done = await _turn_until(bridge, sent["turn_id"], lambda v: v["status"] == "completed")
    assert "activity" not in done
    # 后台任务到回答结束时还没做完：会话记录里留一句提醒
    notices = [
        m["text"]
        for m in bridge.engine.store.turn_messages(sent["turn_id"])
        if m["kind"] == "notice"
    ]
    assert any("后台任务在回答结束时还没做完：等 CI" in text for text in notices), notices


async def test_environment_turn_timeout_overrides_the_setting(bridge: Bridge) -> None:
    # 开发环境单独的单回合时限（5.7）：比管理器的设置优先
    env = await bridge.admin(
        "POST",
        "/admin/environments",
        json={"platform": "fake", "mode": "dev", "name": "短时限", "turn_timeout_seconds": 30},
    )
    assert env.json()["turn_timeout_seconds"] == 30
    too_short = await bridge.admin(
        "POST",
        "/admin/environments",
        json={"platform": "fake", "mode": "dev", "name": "太短", "turn_timeout_seconds": 5},
    )
    assert too_short.status_code == 422
    made = await bridge.tool(
        "owner",
        "llm_session_create",
        {"platform": "fake", "mode": "dev", "environment": env.json()["id"]},
    )
    # 测试里把环境的时限改成 1 秒（接口最少 30 秒）
    store = bridge.engine.store
    row = store.get_environment(env.json()["id"])
    assert row is not None
    store.update_environment(row["id"], config={**row["config"], "turn_timeout_seconds": 1})
    late = await bridge.tool(
        "owner",
        "llm_send",
        {
            "session_id": made["session_id"],
            "text": "sleep:20",
            "request_id": "r1",
            "wait_seconds": 10,
        },
    )
    assert late["status"] == "failed" and late["failure"]["kind"] == "timeout"
    assert bridge.engine.setting("turn_timeout_seconds") == 1800  # 管理器的设置没动


# ---- 预约（LONGRUN 5.2、5.4）----
def _due_now(bridge: Bridge, timer_id: str) -> None:
    """测试里不等真的时间：把预约改成已经到点，叫醒调度协程。"""
    bridge.engine.store.update_timer(timer_id, run_at=time.time() - 1)
    bridge.engine.wake_scheduler()


async def _wait(check: Any, what: str) -> Any:
    for _ in range(200):
        value = check()
        if value:
            return value
        await asyncio.sleep(0.05)
    raise AssertionError(f"等不到：{what}")


async def test_timer_sends_a_marked_message_and_can_be_watched(bridge: Bridge) -> None:
    session = await _chat(bridge)
    made = await bridge.tool(
        "owner",
        "llm_timer_create",
        {"session_id": session, "text": "去看 CI 结果", "after_seconds": 3600},
    )
    timer = made["timer"]
    assert timer["status"] == "pending" and 3500 < timer["in_seconds"] <= 3600
    store = bridge.engine.store
    assert bridge.engine.turns_status([timer["id"]])["turns"][timer["id"]]["status"] == "scheduled"
    _due_now(bridge, timer["id"])
    fired = await _wait(
        lambda: (store.get_timer(timer["id"]) or {}).get("fired_turn_id"), "预约发出"
    )
    turn = store.get_turn(fired)
    assert turn is not None and turn["caller_id"] == "system"
    assert turn["request_id"] == f"auto:timer:{timer['id']}"
    assert turn["text"].startswith(AUTO_MARK) and turn["text"].endswith("去看 CI 结果")
    assert turn["source"] == {"kind": "timer", "id": timer["id"]}
    done = await _turn_until(bridge, fired, lambda v: v["status"] == "completed")
    assert AUTO_MARK in done["result"]  # 模拟平台把收到的话原样回答
    # 工作台盯预约：跟到它引出的回合
    status = bridge.engine.turns_status([timer["id"]])["turns"][timer["id"]]
    assert status == {"status": "scheduled", "next_turn_id": fired, "session_id": session}
    tasks = await bridge.tool("owner", "llm_tasks", {"session_id": session})
    assert tasks["timers"][0]["status"] == "fired" and tasks["chain"]["rounds"] == 1


async def test_timer_rules_and_cancel(bridge: Bridge) -> None:
    session = await _chat(bridge)
    too_soon = await bridge.tool(
        "owner", "llm_timer_create", {"session_id": session, "text": "x", "after_seconds": 10}
    )
    assert too_soon["error"]["code"] == "VALIDATION_FAILED"
    too_far = await bridge.tool(
        "owner",
        "llm_timer_create",
        {"session_id": session, "text": "x", "at": time.time() + 8 * 86400},
    )
    assert too_far["error"]["code"] == "VALIDATION_FAILED" and "7 天" in too_far["error"]["message"]
    both = await bridge.tool(
        "owner",
        "llm_timer_create",
        {"session_id": session, "text": "x", "at": time.time() + 600, "after_seconds": 600},
    )
    assert both["error"]["code"] == "VALIDATION_FAILED"
    ids = []
    for n in range(5):
        made = await bridge.tool(
            "owner",
            "llm_timer_create",
            {"session_id": session, "text": f"第{n}个", "after_seconds": 600},
        )
        ids.append(made["timer"]["id"])
    full = await bridge.tool(
        "owner", "llm_timer_create", {"session_id": session, "text": "第六个", "after_seconds": 600}
    )
    assert full["error"]["code"] == "RATE_LIMITED"
    # 别人的会话：看不到、取消不了
    assert (await bridge.tool("other", "llm_timer_cancel", {"timer_id": ids[0]}))["error"][
        "code"
    ] == "NOT_FOUND"
    cancelled = await bridge.tool("owner", "llm_timer_cancel", {"timer_id": ids[0]})
    assert cancelled["timer"]["status"] == "cancelled"
    again = await bridge.tool("owner", "llm_timer_cancel", {"timer_id": ids[0]})
    assert again["error"]["code"] == "CONFLICT"
    assert bridge.engine.turns_status([ids[0]])["turns"][ids[0]]["status"] == "cancelled"


async def test_due_timers_merge_into_one_queued_turn(bridge: Bridge) -> None:
    # 会话正忙时，同时到点的两个预约合并成一个排着的自动回合（5.4）
    session = await _chat(bridge)
    busy = await bridge.tool(
        "owner",
        "llm_send",
        {"session_id": session, "text": "sleep:1", "request_id": "r1", "wait_seconds": 0},
    )
    first = await bridge.tool(
        "owner", "llm_timer_create", {"session_id": session, "text": "甲", "after_seconds": 600}
    )
    second = await bridge.tool(
        "owner", "llm_timer_create", {"session_id": session, "text": "乙", "after_seconds": 600}
    )
    store = bridge.engine.store
    for timer in (first["timer"], second["timer"]):
        store.update_timer(timer["id"], run_at=time.time() - 1)
    bridge.engine.wake_scheduler()
    turn_a = await _wait(
        lambda: (store.get_timer(first["timer"]["id"]) or {}).get("fired_turn_id"), "甲"
    )
    turn_b = await _wait(
        lambda: (store.get_timer(second["timer"]["id"]) or {}).get("fired_turn_id"), "乙"
    )
    assert turn_a == turn_b
    merged = store.get_turn(turn_a)
    assert merged is not None and "甲" in merged["text"] and "乙" in merged["text"]
    await _turn_until(bridge, busy["turn_id"], lambda v: v["status"] == "completed")
    await _turn_until(bridge, turn_a, lambda v: v["status"] == "completed")
    assert store.get_chain(session)["rounds"] == 1  # 合并的不另算一轮


# ---- 循环护栏（5.6）----
async def test_chain_pauses_at_the_round_limit_and_a_message_resets_it(bridge: Bridge) -> None:
    bridge.engine.update_settings({"chain_max_rounds": 2})
    session = await _chat(bridge)
    store = bridge.engine.store
    fired = []
    for n in range(3):
        made = await bridge.tool(
            "owner",
            "llm_timer_create",
            {"session_id": session, "text": f"第{n}轮", "after_seconds": 600},
        )
        timer_id = made["timer"]["id"]
        _due_now(bridge, timer_id)
        await _wait(
            lambda t=timer_id: (store.get_timer(t) or {})["status"] != "pending", "预约处理"
        )
        row = store.get_timer(timer_id)
        assert row is not None
        if row["fired_turn_id"]:
            await _turn_until(bridge, row["fired_turn_id"], lambda v: v["status"] == "completed")
        fired.append(row)
    assert [r["status"] for r in fired] == ["fired", "fired", "skipped"]
    assert "连续自动接着做了 2 轮" in fired[2]["reason"]
    chain = (await bridge.tool("owner", "llm_tasks", {"session_id": session}))["chain"]
    assert chain["status"] == "paused" and chain["rounds"] == 2
    # 人（调用方）发了一条消息：从头算
    await bridge.tool(
        "owner", "llm_send", {"session_id": session, "text": "继续", "request_id": "r1"}
    )
    assert (
        store.get_chain(session)["status"] == "active" and store.get_chain(session)["rounds"] == 0
    )


async def test_chain_pauses_after_repeated_failures_and_can_be_stopped(bridge: Bridge) -> None:
    bridge.engine.update_settings({"chain_max_failures": 2})
    bridge.fake.fail_prefix = AUTO_MARK  # 自动回合全部失败
    session = await _chat(bridge)
    store = bridge.engine.store
    for n in range(2):
        made = await bridge.tool(
            "owner",
            "llm_timer_create",
            {"session_id": session, "text": f"第{n}轮", "after_seconds": 600},
        )
        _due_now(bridge, made["timer"]["id"])
        turn = await _wait(
            lambda t=made["timer"]["id"]: (store.get_timer(t) or {}).get("fired_turn_id"), "发出"
        )
        await _turn_until(bridge, turn, lambda v: v["status"] == "failed")
    chain = store.get_chain(session)
    assert (
        chain is not None
        and chain["status"] == "paused"
        and "连续失败了 2 次" in chain["paused_reason"]
    )
    resumed = await bridge.tool(
        "owner", "llm_chain_control", {"session_id": session, "action": "resume"}
    )
    assert resumed["chain"]["status"] == "active" and resumed["chain"]["failures"] == 0
    pending = await bridge.tool(
        "owner", "llm_timer_create", {"session_id": session, "text": "之后", "after_seconds": 600}
    )
    stopped = await bridge.tool(
        "owner", "llm_chain_control", {"session_id": session, "action": "stop"}
    )
    assert (
        stopped["chain"]["status"] == "paused"
        and "停止了自动接着做" in stopped["chain"]["paused_reason"]
    )
    assert store.get_timer(pending["timer"]["id"])["status"] == "cancelled"


# ---- 后台作业（5.3）----
async def test_job_runs_in_the_workdir_and_wakes_the_session(bridge: Bridge) -> None:
    session = await _dev(bridge)
    started = await bridge.tool(
        "owner",
        "llm_job_start",
        {
            "session_id": session,
            "name": "测试",
            "command": "echo hello; pwd; echo token=$LLM_TASK_JOB_ID; echo done",
            "note": "失败就修好再跑",
        },
    )
    job = started["job"]
    assert job["status"] == "running" and job["name"] == "测试" and job["follow_up"] is True
    store = bridge.engine.store
    turn_id = await _wait(
        lambda: (store.get_job(job["id"]) or {}).get("follow_turn_id"), "作业结束后接着做"
    )
    row = store.get_job(job["id"])
    assert row is not None and row["status"] == "succeeded" and row["exit_code"] == 0
    turn = store.get_turn(turn_id)
    assert turn is not None and turn["text"].startswith(AUTO_MARK)
    assert "后台作业「测试」" in turn["text"] and "已成功结束" in turn["text"]
    assert "hello" in turn["text"] and "done" in turn["text"] and "失败就修好再跑" in turn["text"]
    workdir = store.get_session(session)["workdir"]
    log = await bridge.tool("owner", "llm_job_log", {"job_id": job["id"]})
    assert workdir in log["text"] and f"token={job['id']}" in log["text"]
    assert bridge.engine.turns_status([job["id"]])["turns"][job["id"]]["next_turn_id"] == turn_id
    await _turn_until(bridge, turn_id, lambda v: v["status"] == "completed")


async def test_jobs_only_in_exec_environments_and_stop(bridge: Bridge) -> None:
    readonly = await _dev(bridge, sandbox="read_only")
    refused = await bridge.tool("owner", "llm_job_start", {"session_id": readonly, "command": "ls"})
    assert (
        refused["error"]["code"] == "NOT_SUPPORTED"
        and "可以执行命令" in refused["error"]["message"]
    )
    chat = await _chat(bridge)
    assert (await bridge.tool("owner", "llm_job_start", {"session_id": chat, "command": "ls"}))[
        "error"
    ]["code"] == "NOT_SUPPORTED"
    session = await _dev(bridge)
    long = await bridge.tool(
        "owner", "llm_job_start", {"session_id": session, "command": "sleep 30"}
    )
    job_id = long["job"]["id"]
    stopped = await bridge.tool("owner", "llm_job_stop", {"job_id": job_id})
    assert stopped["job"]["status"] == "stopped" and stopped["job"]["follow_up"] is False
    row = bridge.engine.store.get_job(job_id)
    assert row is not None and row["follow_turn_id"] is None  # 停掉后不叫会话接着做
    assert bridge.engine.turns_status([job_id])["turns"][job_id]["status"] == "cancelled"
    # 管理员关掉作业
    bridge.engine.update_settings({"jobs_enabled": False})
    off = await bridge.tool("owner", "llm_job_start", {"session_id": session, "command": "ls"})
    assert "关掉了后台作业" in off["error"]["message"]


async def test_admin_routes_and_archive(bridge: Bridge) -> None:
    session = await _dev(bridge)
    job = await bridge.admin(
        "POST", f"/admin/sessions/{session}/jobs", json={"command": "sleep 30", "by": "小王"}
    )
    assert (
        job.status_code == 200
        and job.json()["job"]["origin"] == "admin"
        and job.json()["job"]["by"] == "小王"
    )
    timer = await bridge.admin(
        "POST", f"/admin/sessions/{session}/timers", json={"text": "明天看", "after_seconds": 600}
    )
    assert timer.status_code == 200
    tasks = (await bridge.admin("GET", f"/admin/sessions/{session}/tasks")).json()
    assert tasks["jobs_allowed"] is True and len(tasks["jobs"]) == 1 and len(tasks["timers"]) == 1
    log = await bridge.admin("GET", f"/admin/jobs/{job.json()['job']['id']}/log")
    assert log.status_code == 200 and log.json()["status"] == "running"
    chain = await bridge.admin(
        "POST", f"/admin/sessions/{session}/chain", json={"action": "stop", "by": "小王"}
    )
    assert chain.json()["chain"]["status"] == "paused"
    # 归档：停掉作业、取消预约
    await bridge.tool("owner", "llm_session_archive", {"session_id": session})
    store = bridge.engine.store
    row = store.get_job(job.json()["job"]["id"])
    assert row is not None and row["status"] == "stopped" and row["stopped_by"] == "会话归档"
    assert store.get_timer(timer.json()["timer"]["id"])["status"] == "cancelled"


async def test_restart_marks_running_jobs_lost_and_tells_the_session(bridge: Bridge) -> None:
    session = await _dev(bridge)
    store = bridge.engine.store
    job = store.create_job(
        session_id=session,
        caller_id="owner",
        origin="caller",
        by_label="owner",
        name="上次的",
        command="make",
        status="running",
        timeout_seconds=3600,
        started_at=time.time() - 60,
    )
    await bridge.engine.longrun.startup()
    row = store.get_job(job["id"])
    assert row is not None and row["status"] == "lost" and row["follow_turn_id"]
    turn = store.get_turn(row["follow_turn_id"])
    assert turn is not None and "在管理器重启时中断了" in turn["text"]


# ---- 会话里的 llm-task（5.5）----
async def test_turn_token_lets_the_session_schedule_itself(bridge: Bridge) -> None:
    session = await _dev(bridge)
    store = bridge.engine.store
    workdir = Path(store.get_session(session)["workdir"])
    (workdir / ".git" / "info").mkdir(parents=True)
    sent = await bridge.tool(
        "owner",
        "llm_send",
        {"session_id": session, "text": "sleep:2", "request_id": "r1", "wait_seconds": 0},
    )
    token_file = workdir / ".llm-task" / "session.json"
    info = await _wait(
        lambda: token_file.exists() and json.loads(token_file.read_text()), "令牌文件"
    )
    assert info["session_id"] == session and info["api"] == "http://127.0.0.1:8787"
    assert "/.llm-task/" in (workdir / ".git" / "info" / "exclude").read_text()
    headers = {"Authorization": f"Bearer {info['token']}"}
    made = await bridge.client.post(
        "/session-api/timers",
        json={"text": "两小时后看 CI", "after_seconds": 7200},
        headers=headers,
    )
    assert made.status_code == 200, made.text
    timer = made.json()["timer"]
    assert timer["origin"] == "session" and timer["by"] == "会话里的模型"
    job = await bridge.client.post(
        "/session-api/jobs",
        json={"command": "echo hi", "name": "试", "follow_up": False},
        headers=headers,
    )
    assert job.status_code == 200 and "不会自动叫你" in job.json()["hint"]
    assert job.json()["job"]["origin"] == "session"
    listed = await bridge.client.get("/session-api/tasks", headers=headers)
    assert {t["id"] for t in listed.json()["timers"]} == {timer["id"]}
    # 别的会话的作业：看不到
    other = await _dev(bridge, caller="other")
    other_job = await bridge.tool(
        "other", "llm_job_start", {"session_id": other, "command": "sleep 5"}
    )
    hidden = await bridge.client.get(f"/session-api/jobs/{other_job['job']['id']}", headers=headers)
    assert hidden.status_code == 404
    await bridge.tool("other", "llm_job_stop", {"job_id": other_job["job"]["id"]})
    await _turn_until(bridge, sent["turn_id"], lambda v: v["status"] == "completed")
    # 回合结束：令牌作废，文件删掉
    assert not token_file.exists()
    gone = await bridge.client.get("/session-api/tasks", headers=headers)
    assert gone.status_code == 401
    assert (await bridge.client.get("/session-api/tasks")).status_code == 401


async def test_llm_task_cli_is_installed_and_builds_requests(
    bridge: Bridge, monkeypatch: Any, tmp_path: Path
) -> None:
    script = bridge.engine.settings.data_dir / "bin" / "llm-task"
    assert script.exists() and os.access(script, os.X_OK)
    assert str(script.parent) in os.environ["PATH"].split(os.pathsep)
    calls: list[tuple[str, str, Any]] = []

    def fake_call(method: str, path: str, body: Any = None) -> Any:
        calls.append((method, path, body))
        if path == "/jobs":
            return {
                "job": {
                    "id": "bj_1",
                    "status_label": "运行中",
                    "name": "测试",
                    "command": "make test",
                    "status": "running",
                },
                "hint": "可以结束了",
            }
        return {
            "timer": {
                "id": "bw_1",
                "status_label": "待发出",
                "run_at": time.time(),
                "text": "看 CI",
            }
        }

    monkeypatch.setattr(session_cli, "_call", fake_call)
    assert (
        session_cli.run(["job", "start", "--name", "测试", "--timeout", "2h", "--", "make", "test"])
        == 0
    )
    assert calls[-1] == (
        "POST",
        "/jobs",
        {
            "command": "make test",
            "name": "测试",
            "note": "",
            "follow_up": True,
            "timeout_seconds": 7200,
        },
    )
    assert session_cli.run(["at", "+30m", "看", "CI"]) == 0
    assert calls[-1] == ("POST", "/timers", {"text": "看 CI", "after_seconds": 1800})
    # 不在会话的工作目录里：说清楚为什么用不了
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("LLM_TASK_SESSION_FILE", raising=False)
    monkeypatch.undo()
    monkeypatch.chdir(tmp_path)
    assert session_cli.main(["status"]) == 1


async def test_job_runner_timeout_log_cap_exit_codes_and_orphans(
    tmp_path: Path, monkeypatch: Any
) -> None:
    from bridge import jobs

    monkeypatch.setattr(jobs, "LOG_CAP", 60)
    monkeypatch.setattr(jobs, "TAIL_KEEP", 24)
    monkeypatch.setattr(jobs, "DRAIN_SECONDS", 0.3)
    monkeypatch.setattr(jobs, "STOP_GRACE", 1.0)
    exits: dict[str, tuple[str, int | None]] = {}

    async def on_exit(job_id: str, status: str, code: int | None, error: str | None) -> None:
        exits[job_id] = (status, code)

    runner = jobs.JobRunner(tmp_path / "logs", on_exit)
    env = {"PATH": os.environ["PATH"]}
    # 日志超过上限：留开头和最近的，中间注明省略了多少
    await runner.start("bj_a", "for i in $(seq 1 30); do echo line$i; done", tmp_path, env, 10)
    await _wait(lambda: "bj_a" in exits, "a")
    text = runner.log_path("bj_a").read_text()
    assert text.startswith("line1\n") and "中间省略了" in text and text.rstrip().endswith("line30")
    assert exits["bj_a"] == ("succeeded", 0)
    # 超过作业时限：停掉
    await runner.start("bj_b", "sleep 30", tmp_path, env, 0.3)
    await _wait(lambda: "bj_b" in exits, "b")
    assert exits["bj_b"][0] == "timeout"
    # 失败的退出码
    await runner.start("bj_c", "echo oops; exit 3", tmp_path, env, 10)
    await _wait(lambda: "bj_c" in exits, "c")
    assert exits["bj_c"] == ("failed", 3)
    # 主进程结束了，放到后台、还占着输出的子进程一起停掉
    pid = await runner.start("bj_d", "sleep 30 & echo started", tmp_path, env, 10)
    await _wait(lambda: "bj_d" in exits, "d")
    assert exits["bj_d"] == ("succeeded", 0)

    def group_gone() -> bool:
        try:
            os.killpg(pid, 0)
        except ProcessLookupError:
            return True
        return False

    await _wait(group_gone, "后台子进程被停掉")
    # 正在跑的作业：日志能边跑边看
    await runner.start("bj_e", "echo first; sleep 30", tmp_path, env, 10)
    await _wait(lambda: "first" in runner.read_log("bj_e", 1000)[0], "边跑边看")
    assert await runner.stop("bj_e") is True
    assert exits["bj_e"][0] == "stopped"
    assert await runner.stop("bj_e") is False


def test_cli_driver_tells_the_model_about_llm_task(tmp_path: Path) -> None:
    from bridge.drivers.base import JOBS_INSTRUCTIONS, SessionSpec
    from bridge.drivers.cli_stream import CliStreamDriver, claude_profile, codebuddy_profile

    spec = SessionSpec(
        mode="dev", model="opus", workdir=tmp_path, sandbox="exec", native_id="n1", jobs=True
    )
    claude = CliStreamDriver(claude_profile("claude"), tmp_path / "c", tmp_path / "s")
    args = claude._args(spec)
    assert args[args.index("--append-system-prompt") + 1] == JOBS_INSTRUCTIONS
    assert claude._background_env(spec) == {"CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "1"}
    buddy = CliStreamDriver(codebuddy_profile("codebuddy"), tmp_path / "b", tmp_path / "s")
    assert buddy._background_env(spec) == {"CODEBUDDY_CODE_DISABLE_BACKGROUND_TASKS": "1"}
    # 不能开作业的会话：不说、不关
    plain = SessionSpec(mode="dev", model="opus", workdir=tmp_path, sandbox="exec", native_id="n1")
    assert "--append-system-prompt" not in claude._args(plain)
    assert claude._background_env(plain) == {}
