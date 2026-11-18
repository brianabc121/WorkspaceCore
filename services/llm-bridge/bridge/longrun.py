"""长时间任务：后台作业、预约回来、自动接着做的循环（docs/v2/LLM_BRIDGE_LONGRUN_PLAN.md）。

核心原则是“等待不占回合”：
- 跑很久的命令交给管理器托管（作业，bridge/jobs.py），回答结束了照样跑；
- 作业结束、预约到点时，管理器给同一个会话排一个系统回合（自动回合），消息开头写明
  “不是人发的、不代表任何人的批准”，模型接着做；
- 由作业结束、预约到点连续引出的自动回合算一条“循环”，有轮数、时长、连续失败、每天次数的上限，
  到了就暂停、等人决定（人或调用方对这个会话发一条消息、或者点“继续”就恢复）。

谁能发起：人（页面）、外层智能体（工作台转发的 llm_* 工具）、会话里的模型（`llm-task` 命令，
用只对这一回合有效的令牌，见 session_cli.py）。人类 2026-11-18 决定这些都直接执行、不审批
（docs/AGENTS.md 第 8 节例外十）。
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import secrets
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from bridge import session_cli
from bridge.errors import BridgeError
from bridge.jobs import JobRunner
from bridge.redact import redact

if TYPE_CHECKING:
    from bridge.engine import Engine

logger = logging.getLogger(__name__)
Row = dict[str, Any]

SYSTEM_CALLER = "system"
AUTO_LABEL = "MCP 管理器（自动接着做）"
AUTO_MARK = "【MCP 管理器自动发出，不是人发的消息，不代表任何人的批准】"
TOKEN_DIR = ".llm-task"  # noqa: S105 - 目录名，不是密码
TOKEN_FILE = "session.json"  # noqa: S105
CLI_NAME = "llm-task"
MIN_TIMER_SECONDS = 60
MIN_JOB_SECONDS = 10
MAX_COMMAND_CHARS = 4000
MAX_TEXT_CHARS = 4000
LOG_TAIL_LINES = 40
SESSION_CALLS_PER_MINUTE = 60
JOB_LOG_DAYS = 7  # 结束的作业的日志留几天
JOB_ENV_PASS = (
    "PATH",
    "LANG",
    "LC_ALL",
    "TZ",
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "NO_PROXY",
    "https_proxy",
    "http_proxy",
    "no_proxy",
    "SSL_CERT_FILE",
    "NODE_EXTRA_CA_CERTS",
)

JOB_STATUS = {
    "running": "运行中",
    "succeeded": "成功",
    "failed": "失败",
    "timeout": "超时停止",
    "stopped": "已停止",
    "lost": "结果未知",
}
TIMER_STATUS = {
    "pending": "待发出",
    "fired": "已发出",
    "cancelled": "已取消",
    "skipped": "没有发出",
}
CHAIN_STATUS = {"active": "进行中", "paused": "已暂停"}
ORIGIN_LABEL = {"caller": "调用方", "session": "会话里的模型", "admin": "人"}


def fmt_time(ts: float | None) -> str:
    if not ts:
        return "—"
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) + time.strftime(
        "（%z）", time.localtime(ts)
    )


def fmt_duration(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    seconds = max(0, round(seconds))
    if seconds < 60:
        return f"{seconds} 秒"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} 分钟"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} 小时 {minutes} 分" if minutes else f"{hours} 小时"


class LongRun:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.store = engine.store
        self.runner = JobRunner(engine.settings.data_dir / "jobs", self._job_exited, self._progress)
        self._tokens: dict[str, tuple[str, str]] = {}  # 回合令牌 → (会话, 回合)
        self._calls: dict[str, list[float]] = {}  # 回合令牌 → 最近的调用时间

    def setting(self, key: str) -> Any:
        return self.engine.setting(key)

    # ---- 生命周期 ----
    async def startup(self) -> None:
        self.install_cli()
        # 管理器所在的容器重启后作业进程就没了：仍记为运行中的改成“结果未知”，按设置告诉会话
        for job in self.store.jobs_with_status("running"):
            self.store.update_job(
                job["id"],
                status="lost",
                finished_at=time.time(),
                error="管理器在作业运行中重启，作业被中断",
            )
            self._after_job(job["id"])

    async def shutdown(self) -> None:
        await self.runner.shutdown()

    def install_cli(self) -> Path:
        """把 llm-task 写进 <数据目录>/bin，并放到 PATH 最前面。

        官方程序和它们跑的命令都继承这个 PATH，能直接用。"""
        folder = self.engine.settings.data_dir / "bin"
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / CLI_NAME
        source = Path(session_cli.__file__).read_text(encoding="utf-8")
        target.write_text(f"#!{sys.executable}\n{source}", encoding="utf-8")
        target.chmod(0o755)
        path = os.environ.get("PATH", "")
        if str(folder) not in path.split(os.pathsep):
            os.environ["PATH"] = os.pathsep.join(p for p in (str(folder), path) if p)
        return target

    async def archive(self, session: Row) -> None:
        """会话归档：停掉它的作业（不再接着做），取消它的预约。"""
        for job in self.store.session_jobs(session["id"], limit=1000):
            if job["status"] == "running":
                self.store.update_job(job["id"], follow_up=0, stopped_by="会话归档")
                await self.runner.stop(job["id"])
        for timer in self.store.session_timers(session["id"], limit=1000):
            if timer["status"] == "pending":
                self.store.update_timer(timer["id"], status="cancelled", reason="会话已归档")

    def purge_logs(self) -> int:
        """结束超过 JOB_LOG_DAYS 天的作业：删掉日志文件（记录留着）。"""
        removed = 0
        for job in self.store.finished_jobs_before(time.time() - JOB_LOG_DAYS * 86400):
            path = self.runner.log_path(job["id"])
            if path.exists():
                path.unlink(missing_ok=True)
                removed += 1
        return removed

    # ---- 回合令牌（会话里的 llm-task 用）----
    def open_token(self, session: Row, turn_id: str) -> str | None:
        """回合开始：给开发会话写一个只对这一回合有效的令牌。"""
        if session["mode"] != "dev" or session["origin"] != "bridge":
            return None
        workdir = Path(session["workdir"] or "")
        if not workdir.is_dir():
            return None
        token = secrets.token_urlsafe(24)
        folder = workdir / TOKEN_DIR
        try:
            folder.mkdir(exist_ok=True)
            info = {
                "api": self.engine.settings.session_api_url,
                "token": token,
                "session_id": session["id"],
                "note": "MCP 管理器写的回合令牌：只在这一回合有效，回合结束就删掉",
            }
            (folder / TOKEN_FILE).write_text(json.dumps(info, ensure_ascii=False))
            (folder / TOKEN_FILE).chmod(0o600)
            self._exclude(workdir)
        except OSError:
            logger.warning("写不了会话 %s 的回合令牌", session["id"])
            return None
        self._tokens[token] = (session["id"], turn_id)
        return token

    def close_token(self, token: str | None, session: Row) -> None:
        if not token:
            return
        self._tokens.pop(token, None)
        self._calls.pop(token, None)
        path = Path(session["workdir"] or "") / TOKEN_DIR / TOKEN_FILE
        with contextlib.suppress(OSError, ValueError):
            if json.loads(path.read_text()).get("token") == token:
                path.unlink()

    def token_owner(self, token: str) -> tuple[str, str] | None:
        return self._tokens.get(token)

    def allow_session_call(self, token: str) -> bool:
        """会话里的 llm-task：每个回合令牌每分钟最多调用这么多次，防止失控的循环。"""
        now = time.time()
        recent = [t for t in self._calls.get(token, []) if now - t < 60]
        if len(recent) >= SESSION_CALLS_PER_MINUTE:
            self._calls[token] = recent
            return False
        self._calls[token] = [*recent, now]
        return True

    @staticmethod
    def _exclude(workdir: Path) -> None:
        """令牌目录不进仓库：写进 .git/info/exclude。"""
        exclude = workdir / ".git" / "info" / "exclude"
        if not exclude.parent.is_dir():
            return
        line = f"/{TOKEN_DIR}/"
        current = exclude.read_text() if exclude.exists() else ""
        if line not in current.splitlines():
            exclude.write_text(
                current + ("" if current.endswith("\n") or not current else "\n") + line + "\n"
            )

    # ---- 作业 ----
    def jobs_blocked(self, session: Row) -> str | None:
        """这个会话为什么不能开作业；能开返回 None。"""
        if not self.setting("jobs_enabled"):
            return "管理员关掉了后台作业（运行设置）"
        if session["status"] != "active":
            return "会话已归档"
        if session["mode"] != "dev" or session["origin"] != "bridge":
            return "只有 MCP 管理器建的开发会话能开后台作业"
        env = (
            self.store.get_environment(session["environment_id"])
            if session.get("environment_id")
            else None
        )
        sandbox = ((env or {}).get("config") or {}).get("sandbox") or (
            session["options"] or {}
        ).get("sandbox")
        if sandbox != "exec":
            return "后台作业只能在“可以执行命令”档位的开发环境里开（管理员在开发环境里设置）"
        if not Path(session["workdir"] or "").is_dir():
            return "会话的工作目录已经不在了"
        return None

    async def start_job(
        self,
        session: Row,
        *,
        origin: str,
        caller_id: str,
        by: str,
        command: str,
        name: str = "",
        timeout_seconds: int | None = None,
        follow_up: bool = True,
        note: str = "",
        notify: bool = False,
        turn_id: str | None = None,
    ) -> Row:
        blocked = self.jobs_blocked(session)
        if blocked:
            raise BridgeError("NOT_SUPPORTED", blocked)
        command = command.strip()
        if not command or "\x00" in command or len(command) > MAX_COMMAND_CHARS:
            raise BridgeError("VALIDATION_FAILED", f"命令不能为空，最多 {MAX_COMMAND_CHARS} 个字符")
        if len(note) > 1000:
            raise BridgeError("VALIDATION_FAILED", "留给之后的话最多 1000 个字符")
        limit = int(self.setting("job_max_seconds"))
        timeout = int(timeout_seconds or limit)
        if not MIN_JOB_SECONDS <= timeout <= limit:
            raise BridgeError(
                "VALIDATION_FAILED",
                f"作业时限要在 {MIN_JOB_SECONDS} 秒到 {fmt_duration(limit)} 之间"
                "（管理员可在运行设置里改上限）",
            )
        per_session, total = int(self.setting("jobs_per_session")), int(self.setting("jobs_total"))
        if per_session and self.store.count_running_jobs(session["id"]) >= per_session:
            raise BridgeError(
                "RATE_LIMITED", f"这个会话已经有 {per_session} 个作业在跑（运行设置里的上限）"
            )
        if total and self.store.count_running_jobs() >= total:
            raise BridgeError(
                "RATE_LIMITED", f"这个管理器已经有 {total} 个作业在跑（运行设置里的上限）"
            )
        label = (name.strip() or " ".join(command.split())[:40])[:60]
        job = self.store.create_job(
            session_id=session["id"],
            turn_id=turn_id,
            caller_id=caller_id,
            origin=origin,
            by_label=by[:120],
            name=label,
            command=command,
            status="running",
            follow_up=1 if follow_up else 0,
            note=note.strip(),
            notify=1 if notify else 0,
            timeout_seconds=timeout,
            started_at=time.time(),
        )
        workdir = Path(session["workdir"])
        try:
            pid = await self.runner.start(
                job["id"], command, workdir, self._job_env(job["id"]), timeout
            )
        except OSError as error:
            self.store.update_job(
                job["id"], status="failed", finished_at=time.time(), error=f"启动失败：{error}"
            )
            raise BridgeError("UNAVAILABLE", f"作业启动失败：{error}") from error
        self.store.update_job(job["id"], pid=pid)
        who = f"{ORIGIN_LABEL.get(origin, origin)}：{by}"
        text = f"开了后台作业「{label}」（{job['id']}，{who}）：{redact(command)[:300]}"
        self.store.add_event(session["id"], turn_id, "job_started", {"job_id": job["id"], "by": by})
        self.store.add_message(session["id"], turn_id, "process", "notice", text)
        return self.job_view(self.store.get_job(job["id"]) or job)

    def _job_env(self, job_id: str) -> dict[str, str]:
        env = {k: os.environ[k] for k in JOB_ENV_PASS if k in os.environ}
        home = self.engine.settings.home_dir / "jobs"
        home.mkdir(parents=True, exist_ok=True)
        env["HOME"] = str(home)
        env["LLM_TASK_JOB_ID"] = job_id
        return env

    async def stop_job(self, job: Row, by: str, *, tell_session: bool = False) -> Row:
        """停掉一个作业。默认停掉后不再自动叫会话接着做（tell_session 为真时照常叫）。"""
        if job["status"] != "running":
            raise BridgeError(
                "CONFLICT", f"作业已经结束（{JOB_STATUS.get(job['status'], job['status'])}）"
            )
        self.store.update_job(
            job["id"], stopped_by=by[:120], follow_up=job["follow_up"] if tell_session else 0
        )
        if not await self.runner.stop(job["id"]):
            # 进程已经不在了（例如管理器重启过）：直接记为停止
            self.store.update_job(job["id"], status="stopped", finished_at=time.time())
        return self.job_view(self.store.get_job(job["id"]) or job)

    def job_log(self, job: Row, max_bytes: int = 20000) -> Row:
        size = max(1, min(int(max_bytes), 200_000))
        text, total = self.runner.read_log(job["id"], size)
        return {
            "job_id": job["id"],
            "status": job["status"],
            "text": redact(text),
            "total_bytes": max(total, int(job["log_bytes"] or 0)),
            "truncated": max(total, int(job["log_bytes"] or 0)) > len(text.encode()),
        }

    def _progress(self, job_id: str, total: int, last_output_at: float | None) -> None:
        self.store.update_job(job_id, log_bytes=total, last_output_at=last_output_at)

    async def _job_exited(
        self, job_id: str, status: str, code: int | None, error: str | None
    ) -> None:
        job = self.store.get_job(job_id)
        if job is None:
            return
        self.store.update_job(
            job_id,
            status=status,
            exit_code=code,
            finished_at=time.time(),
            error=error or job["error"],
        )
        self._after_job(job_id)

    def _after_job(self, job_id: str) -> None:
        """作业结束：记一步；要接着做的给会话排一个自动回合。"""
        job = self.store.get_job(job_id)
        session = self.store.get_session(job["session_id"]) if job else None
        if job is None or session is None:
            return
        outcome = self._job_outcome(job)
        self.store.add_event(
            session["id"], None, "job_finished", {"job_id": job_id, "status": job["status"]}
        )
        self.store.add_message(
            session["id"],
            None,
            "process",
            "notice",
            f"后台作业「{job['name']}」（{job_id}）{outcome}",
        )
        if not job["follow_up"]:
            return
        turn_id, reason = self._auto_turn(
            session,
            self._job_message(job, outcome),
            {"kind": "job", "id": job_id, "name": job["name"]},
            f"auto:job:{job_id}",
        )
        if turn_id:
            self.store.update_job(job_id, follow_turn_id=turn_id)
        elif reason:
            self.store.update_job(job_id, error=(job["error"] or reason)[:500])

    def _job_outcome(self, job: Row) -> str:
        used = fmt_duration(
            (job["finished_at"] or time.time()) - (job["started_at"] or job["created_at"])
        )
        code = "" if job["exit_code"] is None else f"，退出码 {job['exit_code']}"
        outcomes = {
            "succeeded": f"已成功结束{code}，用时 {used}",
            "failed": f"已结束（失败{code}），用时 {used}",
            "timeout": f"超过作业时限（{fmt_duration(job['timeout_seconds'])}），已停止",
            "stopped": f"被{job['stopped_by'] or '人'}停止{code}，用时 {used}",
            "lost": "在管理器重启时中断了，不知道做到了哪一步",
        }
        return outcomes.get(job["status"], str(job["status"]))

    def _job_message(self, job: Row, outcome: str) -> str:
        text, _ = self.runner.read_log(job["id"], 16000)
        tail = "\n".join(redact(text).splitlines()[-LOG_TAIL_LINES:]).strip()
        lines = [
            f"{AUTO_MARK}后台作业「{job['name']}」（{job['id']}）{outcome}。",
            f"命令：{redact(job['command'])[:500]}",
        ]
        if tail:
            lines += [f"日志末尾（最多 {LOG_TAIL_LINES} 行）：", "```", tail, "```"]
        else:
            lines.append("（没有输出）")
        lines.append(f"完整日志：`{CLI_NAME} job log {job['id']}`。")
        if job["note"]:
            lines.append(f"开这个作业时留的话：{job['note']}")
        return "\n".join(lines)

    # ---- 预约 ----
    def create_timer(
        self,
        session: Row,
        *,
        origin: str,
        caller_id: str,
        by: str,
        text: str,
        at: float | None = None,
        after_seconds: float | None = None,
        notify: bool = False,
        turn_id: str | None = None,
    ) -> Row:
        if session["status"] != "active":
            raise BridgeError("CONFLICT", "会话已归档，不能预约")
        text = text.strip()
        if not text or len(text) > MAX_TEXT_CHARS:
            raise BridgeError("VALIDATION_FAILED", f"消息不能为空，最多 {MAX_TEXT_CHARS} 个字符")
        if (at is None) == (after_seconds is None):
            raise BridgeError(
                "VALIDATION_FAILED", "at（什么时候）和 after_seconds（多少秒后）要给一个"
            )
        now = time.time()
        run_at = float(at) if at is not None else now + float(after_seconds or 0)
        days = int(self.setting("timer_max_days"))
        if run_at < now + MIN_TIMER_SECONDS - 1:
            raise BridgeError("VALIDATION_FAILED", f"最早 {MIN_TIMER_SECONDS} 秒之后")
        if run_at > now + days * 86400:
            raise BridgeError("VALIDATION_FAILED", f"最远 {days} 天之后（管理员可在运行设置里改）")
        limit = int(self.setting("timers_per_session"))
        if self.store.count_pending_timers(session["id"]) >= limit:
            raise BridgeError(
                "RATE_LIMITED", f"这个会话已经有 {limit} 个待发出的预约；先取消用不着的"
            )
        timer = self.store.create_timer(
            session_id=session["id"],
            turn_id=turn_id,
            caller_id=caller_id,
            origin=origin,
            by_label=by[:120],
            run_at=run_at,
            text=text,
            notify=1 if notify else 0,
        )
        self.store.add_event(
            session["id"], turn_id, "timer_created", {"timer_id": timer["id"], "by": by}
        )
        self.store.add_message(
            session["id"],
            turn_id,
            "process",
            "notice",
            f"预约了 {fmt_time(run_at)} 给这个会话发一条消息（{timer['id']}，"
            f"{ORIGIN_LABEL.get(origin, origin)}：{by}）：{redact(text)[:200]}",
        )
        self.engine.wake_scheduler()
        return self.timer_view(timer)

    def cancel_timer(self, timer: Row, by: str) -> Row:
        if timer["status"] != "pending":
            raise BridgeError(
                "CONFLICT", f"预约已经{TIMER_STATUS.get(timer['status'], timer['status'])}"
            )
        self.store.update_timer(timer["id"], status="cancelled", reason=f"{by}取消了"[:200])
        self.store.add_event(
            timer["session_id"], None, "timer_cancelled", {"timer_id": timer["id"], "by": by}
        )
        return self.timer_view(self.store.get_timer(timer["id"]) or timer)

    def next_due(self) -> float | None:
        return self.store.next_timer_at()

    def fire_due(self) -> int:
        """到点的预约：给会话排自动回合。先把预约记为已发出，再排回合（不会发两次）。"""
        fired = 0
        now = time.time()
        for timer in self.store.due_timers(now):
            self.store.update_timer(timer["id"], status="fired", fired_at=now)
            session = self.store.get_session(timer["session_id"])
            if session is None:
                self.store.update_timer(timer["id"], status="skipped", reason="会话不在了")
                continue
            late = now - timer["run_at"]
            delay = f"（晚了 {fmt_duration(late)}：管理器当时没在运行）" if late > 120 else ""
            text = (
                f"{AUTO_MARK}这是{timer['by_label']}在 {fmt_time(timer['created_at'])} 预约、"
                f"原定 {fmt_time(timer['run_at'])} 发出的消息（{timer['id']}）{delay}：\n"
                f"{timer['text']}"
            )
            turn_id, reason = self._auto_turn(
                session, text, {"kind": "timer", "id": timer["id"]}, f"auto:timer:{timer['id']}"
            )
            if turn_id:
                self.store.update_timer(timer["id"], fired_turn_id=turn_id)
                fired += 1
            else:
                self.store.update_timer(timer["id"], status="skipped", reason=reason[:200])
        return fired

    # ---- 循环 ----
    def reset_chain(self, session_id: str) -> None:
        """人或调用方发了一条消息：自动接着做从头算（暂停的也恢复）。"""
        chain = self.store.get_chain(session_id)
        if chain is not None and (
            chain["rounds"] or chain["failures"] or chain["status"] != "active"
        ):
            self.store.save_chain(
                session_id,
                status="active",
                rounds=0,
                failures=0,
                started_at=time.time(),
                paused_reason=None,
            )

    def chain_control(self, session: Row, action: str, by: str) -> Row:
        session_id = session["id"]
        if action == "resume":
            self.store.save_chain(
                session_id,
                status="active",
                rounds=0,
                failures=0,
                started_at=time.time(),
                paused_reason=None,
            )
            self.store.add_event(session_id, None, "chain_resumed", {"by": by})
            self.store.add_message(session_id, None, "process", "notice", f"{by}恢复了自动接着做")
        elif action == "stop":
            for timer in self.store.session_timers(session_id, limit=1000):
                if timer["status"] == "pending":
                    self.store.update_timer(
                        timer["id"], status="cancelled", reason=f"{by}停止了自动接着做"
                    )
            for job in self.store.session_jobs(session_id, limit=1000):
                if job["status"] == "running" and job["follow_up"]:
                    self.store.update_job(job["id"], follow_up=0)
            while (queued := self.store.queued_auto_turn(session_id)) is not None:
                self.engine.cancel_queued(queued, session)
            self._pause(session_id, f"{by}停止了自动接着做")
        else:
            raise BridgeError("VALIDATION_FAILED", "action 只能是 resume 或 stop")
        return self.chain_view(session_id)

    def after_turn(self, turn_id: str, status: str | None) -> None:
        """自动回合结束：做完了清零连续失败；连续失败到上限就暂停。"""
        turn = self.store.get_turn(turn_id)
        if (
            turn is None
            or turn["caller_id"] != SYSTEM_CALLER
            or not str(turn["request_id"]).startswith("auto:")
        ):
            return
        chain = self.store.get_chain(turn["session_id"])
        if chain is None or status is None:
            return
        if status == "completed":
            self.store.save_chain(turn["session_id"], failures=0)
        elif status in ("failed", "unknown"):
            failures = int(chain["failures"]) + 1
            self.store.save_chain(turn["session_id"], failures=failures)
            limit = int(self.setting("chain_max_failures"))
            if failures >= limit and chain["status"] == "active":
                self._pause(
                    turn["session_id"], f"自动接着做的回合连续失败了 {failures} 次（上限 {limit}）"
                )

    def _guard(self, session_id: str, chain: Row) -> str | None:
        rounds, limit = int(chain["rounds"]), int(self.setting("chain_max_rounds"))
        if rounds >= limit:
            return f"已经连续自动接着做了 {rounds} 轮（上限 {limit}）"
        hours = int(self.setting("chain_max_hours"))
        if time.time() - float(chain["started_at"]) > hours * 3600:
            return f"自动接着做已经持续了 {hours} 小时以上（上限 {hours} 小时）"
        per_day = int(self.setting("auto_turns_per_day"))
        today = self.store.count_auto_turns_since(session_id, time.time() - 86400)
        if today >= per_day:
            return f"这个会话 24 小时内已经自动发起了 {today} 个回合（上限 {per_day}）"
        return None

    def _pause(self, session_id: str, reason: str) -> None:
        self.store.save_chain(session_id, status="paused", paused_reason=reason[:300])
        self.store.add_event(session_id, None, "chain_paused", {"reason": reason})
        self.store.add_message(
            session_id,
            None,
            "process",
            "notice",
            f"自动接着做已暂停：{reason}。对这个会话发一条消息，或在“后台任务”里点“继续”后恢复；"
            "已经在跑的作业不受影响，结束后不再自动叫会话接着做。",
        )

    def _auto_turn(
        self, session: Row, text: str, source: Row, request_id: str
    ) -> tuple[str | None, str]:
        """给会话排一个自动回合；同一个会话已经有一个排着的自动回合时，合并进它。"""
        if session["status"] != "active":
            return None, "会话已归档"
        if not self.engine.platform_enabled(session["platform"]):
            return None, "这个平台已经不由这个管理器控制"
        chain = self.store.get_chain(session["id"]) or self.store.save_chain(session["id"])
        if chain["status"] == "paused":
            return None, f"自动接着做已暂停：{chain['paused_reason'] or ''}"
        queued = self.store.queued_auto_turn(session["id"])
        if queued is not None:
            merged = f"{queued['text']}\n\n{text}"
            self.store.update_turn(queued["id"], text=merged)
            asked = self.store.user_message(queued["id"])
            if asked is not None:
                self.store.set_message_text(asked["id"], merged)
            self.store.add_event(session["id"], queued["id"], "auto_merged", {"source": source})
            return queued["id"], ""
        blocked = self._guard(session["id"], chain)
        if blocked:
            self._pause(session["id"], blocked)
            return None, f"自动接着做已暂停：{blocked}"
        turn = self.store.create_turn(
            session["id"], SYSTEM_CALLER, request_id, text, by=AUTO_LABEL, source=source
        )
        self.store.save_chain(session["id"], rounds=int(chain["rounds"]) + 1)
        self.store.add_event(
            session["id"], turn["id"], "queued", {"by": AUTO_LABEL, "auto": source}
        )
        self.engine.enqueue(turn)
        return turn["id"], ""

    # ---- 查看 ----
    def job_view(self, job: Row) -> Row:
        now = time.time()
        started = job["started_at"] or job["created_at"]
        end = job["finished_at"] or (now if job["status"] == "running" else None)
        view: Row = {
            "id": job["id"],
            "session_id": job["session_id"],
            "name": job["name"],
            "command": redact(job["command"]),
            "status": job["status"],
            "status_label": JOB_STATUS.get(job["status"], job["status"]),
            "origin": job["origin"],
            "by": job["by_label"],
            "follow_up": bool(job["follow_up"]),
            "note": job["note"],
            "timeout_seconds": job["timeout_seconds"],
            "exit_code": job["exit_code"],
            "log_bytes": job["log_bytes"],
            "created_at": job["created_at"],
            "started_at": job["started_at"],
            "finished_at": job["finished_at"],
            "seconds": round(end - started) if end else None,
            "last_output_at": job["last_output_at"],
            "error": job["error"],
            "stopped_by": job["stopped_by"],
            "follow_turn_id": job["follow_turn_id"],
        }
        if job["status"] == "running":
            view["quiet_seconds"] = round(now - (job["last_output_at"] or started))
        return view

    def timer_view(self, timer: Row) -> Row:
        view: Row = {
            "id": timer["id"],
            "session_id": timer["session_id"],
            "text": timer["text"],
            "status": timer["status"],
            "status_label": TIMER_STATUS.get(timer["status"], timer["status"]),
            "origin": timer["origin"],
            "by": timer["by_label"],
            "run_at": timer["run_at"],
            "created_at": timer["created_at"],
            "fired_at": timer["fired_at"],
            "fired_turn_id": timer["fired_turn_id"],
            "reason": timer["reason"],
        }
        if timer["status"] == "pending":
            view["in_seconds"] = max(0, round(timer["run_at"] - time.time()))
        return view

    def chain_view(self, session_id: str) -> Row:
        chain = self.store.get_chain(session_id) or {
            "status": "active",
            "rounds": 0,
            "failures": 0,
            "started_at": None,
            "paused_reason": None,
        }
        return {
            "status": chain["status"],
            "status_label": CHAIN_STATUS.get(chain["status"], chain["status"]),
            "rounds": chain["rounds"],
            "failures": chain["failures"],
            "started_at": chain["started_at"],
            "paused_reason": chain["paused_reason"],
            "auto_turns_today": self.store.count_auto_turns_since(session_id, time.time() - 86400),
            "limits": {
                "rounds": int(self.setting("chain_max_rounds")),
                "hours": int(self.setting("chain_max_hours")),
                "failures": int(self.setting("chain_max_failures")),
                "per_day": int(self.setting("auto_turns_per_day")),
            },
        }

    def tasks(self, session: Row) -> Row:
        running = self.store.running_turn(session["id"])
        activity = self.engine.turn_activity(running["id"]) if running else None
        return {
            "session_id": session["id"],
            # 正在跑的回合在做什么（5.7）：页面的“后台任务”面板一次取全
            "running_turn_id": running["id"] if running else None,
            "activity": activity,
            "jobs": [self.job_view(j) for j in self.store.session_jobs(session["id"])],
            "timers": [self.timer_view(t) for t in self.store.session_timers(session["id"])],
            "chain": self.chain_view(session["id"]),
            "jobs_allowed": self.jobs_blocked(session) is None,
            "jobs_blocked": self.jobs_blocked(session),
        }

    def watch_status(self, task_id: str) -> Row | None:
        """给工作台盯预约 / 作业用（turns_status）。

        scheduled 表示还在等，next_turn_id 是它引出的自动回合。"""
        if task_id.startswith("bw_"):
            timer = self.store.get_timer(task_id)
            if timer is None:
                return {"status": "missing"}
            if timer["status"] in ("cancelled", "skipped"):
                return {
                    "status": "cancelled",
                    "kind": timer["reason"],
                    "session_id": timer["session_id"],
                }
            return {
                "status": "scheduled",
                "next_turn_id": timer["fired_turn_id"],
                "session_id": timer["session_id"],
            }
        if task_id.startswith("bj_"):
            job = self.store.get_job(task_id)
            if job is None:
                return {"status": "missing"}
            if job["status"] == "running" or job["follow_turn_id"]:
                return {
                    "status": "scheduled",
                    "next_turn_id": job["follow_turn_id"],
                    "session_id": job["session_id"],
                }
            final = {"succeeded": "completed", "stopped": "cancelled"}.get(job["status"], "failed")
            return {"status": final, "kind": job["status"], "session_id": job["session_id"]}
        return None
