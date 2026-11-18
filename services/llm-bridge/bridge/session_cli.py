"""`llm-task`：会话里的模型用来开后台作业、预约回来的小命令。

见 docs/v2/LLM_BRIDGE_LONGRUN_PLAN.md 5.5。

镜像里它装在 root 拥有的 /usr/local/bin/llm-task（Dockerfile）；开发、测试时管理器启动时把它写成
`<数据目录>/bin/llm-task`，加到 PATH 的最后（longrun.LongRun.install_cli）。
只用标准库：在 Claude Code / Codex / CodeBuddy 跑命令的地方都能直接执行。

它不认识人，只认回合令牌：管理器在每个回合开始时把令牌写进会话工作目录的
`.llm-task/session.json`，回合结束就删掉、作废。所以只有在回合进行中、在这个会话的工作目录里
（或它的子目录里）才能用。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

SESSION_DIR = ".llm-task"  # 管理器在会话工作目录里写回合令牌的地方（只此一处定义）
SESSION_NAME = "session.json"
SESSION_FILE = Path(SESSION_DIR) / SESSION_NAME
_DURATION = re.compile(r"^\+?(\d+(?:\.\d+)?)([smhd])$")
_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}
HELP = """\
用 MCP 管理器托管长时间的事。回答结束后作业照样跑，作业结束或预约到点时，
管理器会给这个会话发一条消息（开头写着“MCP 管理器自动发出”），你接着做。

  llm-task job start [--name 名字] [--timeout 2h] [--note 留给之后的话] [--no-follow-up] -- 命令…
                         开一个后台作业（在会话的工作目录里跑）；结束后默认自动叫你接着做
  llm-task job list      作业列表
  llm-task job show 编号  一个作业的状态
  llm-task job log 编号 [--bytes 20000]   日志的末尾
  llm-task job stop 编号  停掉一个作业
  llm-task at 时间 消息   到点给这个会话发一条消息。时间写 +30m、+2h、+1d，或 2026-11-19T09:00
  llm-task timers        预约列表
  llm-task cancel 编号    取消一个预约
  llm-task status        作业、预约、自动接着做的状态

加 --json 输出原始结果。"""


class CliError(Exception):
    pass


def _session_file() -> Path:
    named = os.environ.get("LLM_TASK_SESSION_FILE")
    if named:
        return Path(named)
    here = Path.cwd().resolve()
    for folder in (here, *here.parents):
        if (folder / SESSION_FILE).is_file():
            return folder / SESSION_FILE
    raise CliError(
        "找不到这个会话的回合令牌（.llm-task/session.json）。"
        "llm-task 只能在 MCP 管理器的开发会话里、"
        "回合进行中、在会话的工作目录里用。"
    )


def _call(method: str, path: str, body: dict[str, Any] | None = None) -> Any:
    info = json.loads(_session_file().read_text(encoding="utf-8"))
    url = str(info["api"]).rstrip("/") + "/session-api" + path
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)  # noqa: S310 - 本机地址
    request.add_header("Authorization", f"Bearer {info['token']}")
    request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            return json.loads(response.read() or b"null")
    except urllib.error.HTTPError as error:
        try:
            detail = json.loads(error.read())
            message = detail.get("error", {}).get("message") or detail.get("detail")
        except ValueError:
            message = None
        raise CliError(str(message or f"MCP 管理器拒绝了：HTTP {error.code}")) from error
    except (urllib.error.URLError, OSError) as error:
        raise CliError(
            f"连不上 MCP 管理器（{url}）：{error}。Codex 的沙箱不允许联网时连不上本机地址，"
            "请管理员在这个开发环境里打开“允许联网”。"
        ) from error


def _when(text: str) -> dict[str, Any]:
    match = _DURATION.match(text.strip())
    if match:
        return {"after_seconds": round(float(match[1]) * _UNITS[match[2]])}
    try:
        return {"at": time.mktime(time.strptime(text.strip(), "%Y-%m-%dT%H:%M"))}
    except ValueError as error:
        raise CliError("时间写成 +30m、+2h、+1d，或 2026-11-19T09:00（本机时间）") from error


def _seconds(text: str | None) -> int | None:
    if not text:
        return None
    match = _DURATION.match(text.strip())
    if not match:
        raise CliError("时长写成 90s、30m、2h、1d")
    return round(float(match[1]) * _UNITS[match[2]])


def _at(ts: float | None) -> str:
    return time.strftime("%m-%d %H:%M", time.localtime(ts)) if ts else "—"


def _show_job(job: dict[str, Any]) -> str:
    line = f"{job['id']}  {job['status_label']}  「{job['name']}」  {job['command']}"
    extra = []
    if job.get("exit_code") is not None:
        extra.append(f"退出码 {job['exit_code']}")
    if job.get("seconds") is not None:
        extra.append(f"已用 {job['seconds']} 秒")
    if job.get("quiet_seconds") is not None and job["status"] == "running":
        extra.append(f"{job['quiet_seconds']} 秒没有输出")
    return line + (f"（{'，'.join(extra)}）" if extra else "")


def _show_timer(timer: dict[str, Any]) -> str:
    return f"{timer['id']}  {timer['status_label']}  {_at(timer['run_at'])}  {timer['text'][:80]}"


def _print(result: Any, as_json: bool, render: Any) -> None:
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(render(result))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="llm-task",
        description=HELP,
        add_help=True,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--json", action="store_true", help="输出原始结果")
    sub = parser.add_subparsers(dest="what")
    job = sub.add_parser("job", help="后台作业")
    jobs = job.add_subparsers(dest="action")
    start = jobs.add_parser("start", help="开一个后台作业")
    start.add_argument("--name", default="")
    start.add_argument("--timeout", default=None)
    start.add_argument("--note", default="")
    start.add_argument("--no-follow-up", action="store_true")
    start.add_argument("command", nargs=argparse.REMAINDER)
    jobs.add_parser("list", help="作业列表")
    for name in ("show", "stop"):
        jobs.add_parser(name).add_argument("job_id")
    log = jobs.add_parser("log", help="日志的末尾")
    log.add_argument("job_id")
    log.add_argument("--bytes", type=int, default=20000)
    at = sub.add_parser("at", help="到点给这个会话发一条消息")
    at.add_argument("when")
    at.add_argument("text", nargs="+")
    sub.add_parser("timers", help="预约列表")
    sub.add_parser("cancel", help="取消一个预约").add_argument("timer_id")
    sub.add_parser("status", help="作业、预约、自动接着做的状态")
    return parser


def run(argv: list[str]) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    as_json = bool(args.json)
    if args.what == "job" and args.action == "start":
        parts = [p for p in args.command if p != "--"] if args.command else []
        if not parts:
            raise CliError(
                "要跑的命令写在 -- 后面，例如：llm-task job start --name 测试 -- make test"
            )
        command = parts[0] if len(parts) == 1 else shlex.join(parts)
        body = {
            "command": command,
            "name": args.name,
            "note": args.note,
            "follow_up": not args.no_follow_up,
            "timeout_seconds": _seconds(args.timeout),
        }
        result = _call("POST", "/jobs", body)
        _print(result, as_json, lambda r: "已开始：" + _show_job(r["job"]) + "\n" + r["hint"])
    elif args.what == "job" and args.action == "list":
        result = _call("GET", "/jobs")
        _print(result, as_json, lambda r: "\n".join(_show_job(j) for j in r["jobs"]) or "没有作业")
    elif args.what == "job" and args.action == "show":
        result = _call("GET", f"/jobs/{args.job_id}")
        _print(result, as_json, lambda r: _show_job(r["job"]))
    elif args.what == "job" and args.action == "log":
        result = _call("GET", f"/jobs/{args.job_id}/log?max_bytes={max(1, args.bytes)}")
        _print(result, as_json, lambda r: r["text"])
    elif args.what == "job" and args.action == "stop":
        result = _call("POST", f"/jobs/{args.job_id}/stop", {})
        _print(result, as_json, lambda r: "已停止：" + _show_job(r["job"]))
    elif args.what == "at":
        body = {"text": " ".join(args.text), **_when(args.when)}
        result = _call("POST", "/timers", body)
        _print(result, as_json, lambda r: "已预约：" + _show_timer(r["timer"]))
    elif args.what == "timers":
        result = _call("GET", "/timers")
        render = lambda r: "\n".join(_show_timer(t) for t in r["timers"]) or "没有预约"  # noqa: E731
        _print(result, as_json, render)
    elif args.what == "cancel":
        result = _call("POST", f"/timers/{args.timer_id}/cancel", {})
        _print(result, as_json, lambda r: "已取消：" + _show_timer(r["timer"]))
    elif args.what == "status":
        result = _call("GET", "/tasks")

        def render(r: dict[str, Any]) -> str:
            chain = r["chain"]
            lines = [f"自动接着做：{chain['status_label']}（第 {chain['rounds']} 轮）"]
            lines += [_show_job(j) for j in r["jobs"]] or ["没有作业"]
            lines += [_show_timer(t) for t in r["timers"]] or ["没有预约"]
            return "\n".join(lines)

        _print(result, as_json, render)
    else:
        parser.print_help()
        return 2
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        return run(sys.argv[1:] if argv is None else argv)
    except CliError as error:
        print(f"llm-task：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
