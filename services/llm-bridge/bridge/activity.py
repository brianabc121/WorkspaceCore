"""回合进行中的动静（docs/v2/LLM_BRIDGE_LONGRUN_PLAN.md 5.7）。

只在内存里：回合结束就丢掉（管理器重启时进行中的回合本来就记为“结果未知”）。
- 最后一次有动静的时间：驱动报来的任何事件都算（逐字增量、命令输出也算）；
- 正在做什么：开始了、结果还没回来的命令或工具调用，带开始时间；
- 命令输出的最后几十行（Codex 有流式输出；Claude / CodeBuddy 的命令输出要等跑完才有）；
- 官方程序报的后台任务（Claude / CodeBuddy 的 `task_*` 事件）。
调用方据此分清“在等一个长命令”和“卡住了”，不用误取消。
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from bridge.redact import redact

TAIL_LINES = 40  # 命令输出最多留多少行
LINE_CHARS = 400  # 每行最多多长
BACKGROUND_LIMIT = 20  # 最多记多少个后台任务

# 只用于“正在做什么”、不写进会话记录的事件
LIVE_ONLY = frozenset({"command_started", "tool_started", "output", "step_done"})


@dataclass
class TurnActivity:
    last_at: float = field(default_factory=time.time)
    current: dict[str, Any] | None = None
    tail: deque[str] = field(default_factory=lambda: deque(maxlen=TAIL_LINES))
    partial: str = ""
    background: dict[str, dict[str, Any]] = field(default_factory=dict)

    def note(self, kind: str, payload: dict[str, Any]) -> None:
        """记下驱动报来的一个事件。"""
        now = time.time()
        self.last_at = now
        step = str(payload.get("step") or "")
        if kind in ("command_started", "command") and "exit_code" not in payload:
            # Codex 在命令开始时报 command_started；Claude / CodeBuddy 的 command 就是开始
            text = str(payload.get("command") or "")
            if text:
                self._start("command", text, step, now)
        elif kind in ("tool_started", "tool"):
            name = str(payload.get("name") or "")
            if name:
                self._start("tool", name, step, now)
        elif kind == "step_done" or (kind == "command" and "exit_code" in payload):
            if self.current is not None and (not step or self.current.get("step") in ("", step)):
                self.current = None
        elif kind == "message":
            self.current = None  # 模型开口说话了：之前的命令、工具调用已经有结果
        elif kind == "output":
            self._output(str(payload.get("text") or ""))
        elif kind == "background":
            self._background(payload, now)

    def _start(self, kind: str, text: str, step: str, now: float) -> None:
        self.current = {"kind": kind, "text": text[:300], "step": step, "since": now}
        if kind == "command":
            self.tail.clear()
            self.partial = ""

    def _output(self, text: str) -> None:
        lines = (self.partial + text).split("\n")
        self.partial = lines.pop()[-LINE_CHARS:]
        for line in lines:
            self.tail.append(line[:LINE_CHARS])

    def _background(self, payload: dict[str, Any], now: float) -> None:
        task_id = str(payload.get("task_id") or "")
        if not task_id:
            return
        task = self.background.get(task_id)
        if task is None:
            if len(self.background) >= BACKGROUND_LIMIT:
                return
            task = self.background[task_id] = {"task_id": task_id, "since": now}
        for key in ("status", "description", "summary", "task_type"):
            if payload.get(key):
                task[key] = payload[key]
        task["updated_at"] = now

    def unfinished(self) -> list[dict[str, Any]]:
        """回答结束时还没做完的后台任务（官方程序退出时会停掉它们）。"""
        return [t for t in self.background.values() if t.get("status") in (None, "running")]

    def view(self, now: float | None = None) -> dict[str, Any]:
        now = time.time() if now is None else now
        out: dict[str, Any] = {
            "last_event_at": round(self.last_at, 3),
            "quiet_seconds": max(0, round(now - self.last_at)),
        }
        if self.current is not None:
            out["current"] = {
                "kind": self.current["kind"],
                "text": redact(self.current["text"]),
                "started_at": round(self.current["since"], 3),
                "seconds": max(0, round(now - self.current["since"])),
            }
        lines = [*self.tail, *([self.partial] if self.partial else [])]
        if lines:
            out["output_tail"] = redact("\n".join(lines[-TAIL_LINES:]))
        if self.background:
            out["background"] = [
                {
                    "task_id": t["task_id"],
                    "status": t.get("status") or "running",
                    "description": redact(str(t.get("description") or "")),
                    "summary": redact(str(t.get("summary") or "")),
                    "started_at": round(t["since"], 3),
                }
                for t in self.background.values()
            ]
        return out
