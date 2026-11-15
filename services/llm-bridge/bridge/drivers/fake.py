"""测试用的驱动：不访问任何外部服务。BRIDGE_ENABLE_FAKE=true 时才会登记成平台 fake。

消息约定：`sleep:<秒>` 等这么久再回答；`fail:<错误类别>` 抛出对应的错误；其余原样回显。
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

from bridge.drivers.base import (
    AccountStatus,
    Driver,
    DriverError,
    EventSink,
    LoginChallenge,
    Message,
    Mode,
    ModelInfo,
    NativeSession,
    SessionSpec,
    TurnOutcome,
)


class FakeDriver(Driver):
    slot = "fake"
    login_methods = ("api_key",)

    def __init__(self) -> None:
        self.identity: str | None = "fake@example.com"
        # True：已登录但平台不报告是哪个账号（模拟用密钥登录）
        self.anonymous = False
        self.history: dict[str, list[Message]] = {}
        self.external: list[NativeSession] = []
        self.running = 0
        self.max_running = 0
        self._cancel: dict[str, asyncio.Event] = {}
        # 额度：quota_until 之前的回合都以“额度用完”失败；quota_reports_reset=False 时不给重置时间
        self.last_images: tuple[tuple[str, str], ...] = ()
        # 插入消息：steerable=True 时回合中追加的消息会被收下（记在 steered 里）
        # 登录时平台那边出错（例如连不上平台）：login 抛出这段话
        self.login_error: str | None = None
        self.steerable = False
        self.steered: list[str] = []
        self.quota_until = 0.0
        self.quota_reports_reset = True
        self.current_quota: list[dict[str, Any]] = []
        self.can_query_quota = False

    def supports(self, mode: Mode) -> bool:
        return mode in ("chat", "dev")

    async def status(self) -> AccountStatus:
        if self.anonymous:
            return AccountStatus(True, None, "api_key")
        return AccountStatus(self.identity is not None, self.identity, "api_key")

    async def models(self, mode: Mode) -> list[ModelInfo]:
        return [ModelInfo("fake-1", "Fake 1", default=True), ModelInfo("fake-2", "Fake 2")]

    async def create(self, spec: SessionSpec) -> str:
        native = str(uuid.uuid4())
        self.history[native] = []
        return native

    async def run_turn(self, spec: SessionSpec, text: str, sink: EventSink) -> TurnOutcome:
        assert spec.native_id is not None  # noqa: S101
        cancel = self._cancel.setdefault(spec.native_id, asyncio.Event())
        cancel.clear()
        self.running += 1
        self.max_running = max(self.max_running, self.running)
        try:
            if text.startswith("fail:"):
                raise DriverError(text[5:], "模拟的失败")  # type: ignore[arg-type]
            if self.quota_until > time.time():
                # 模拟额度用完：到 quota_until 之前谁来都被拒绝，并报出额度
                windows = [
                    {
                        "kind": "five_hour",
                        "used_percent": 100.0,
                        "resets_at": self.quota_until if self.quota_reports_reset else None,
                        "status": "exceeded",
                        "plan": "fake",
                    }
                ]
                await sink("quota", {"windows": windows})
                raise DriverError(
                    "rate_limited",
                    "You've hit your usage limit",
                    quota=True,
                    resets_at=self.quota_until if self.quota_reports_reset else None,
                )
            self.current_quota = [
                {
                    "kind": "five_hour",
                    "used_percent": 40.0,
                    "resets_at": time.time() + 3600,
                    "status": "ok",
                    "plan": "fake",
                }
            ]
            await sink("quota", {"windows": self.current_quota})
            if text.startswith("sleep:"):
                waiter = asyncio.create_task(cancel.wait())
                done, _ = await asyncio.wait({waiter}, timeout=float(text[6:]))
                waiter.cancel()
                if done:
                    raise DriverError("platform_error", "回合被中断")
            reply = f"[{spec.model}] {text}"
            if spec.images:
                reply += f"（收到 {len(spec.images)} 张图片）"
            self.last_images = spec.images
            commands: list[str] = []
            files: list[str] = []
            if text.startswith("work:"):
                # 模拟开发回合：先说一句、执行命令、改文件，停一下再给出结果（work:<停几秒>）
                await sink("message", {"text": "先看一下代码"})
                commands, files = ["ls src"], ["src/a.py"]
                await sink("command", {"command": commands[0]})
                await sink("files", {"paths": files})
                waiter = asyncio.create_task(cancel.wait())
                done, _ = await asyncio.wait({waiter}, timeout=float(text[5:] or 0))
                waiter.cancel()
                if done:
                    raise DriverError("platform_error", "回合被中断")
            if text.startswith("stream:"):
                # 模拟逐字输出：分三小段给增量，中间停一下（stream:<每段停几秒>），最后给整段
                for piece in ("第一", "二", "三段"):
                    await sink("delta", {"text": piece})
                    await asyncio.sleep(float(text[7:] or 0))
                await sink("message", {"text": "第一二三段"})
            if text.startswith("read:"):
                # 模拟读文件这类工具调用和它的结果
                await sink("tool", {"name": "Read", "input": '{"file_path": "' + text[5:] + '"}'})
                await sink("tool_result", {"name": "Read", "text": "文件内容", "error": False})
            if text.startswith("write:"):
                # 模拟开发回合在工作目录里写出一个文件（write:<相对路径>）
                name = text[6:].split("\n")[0]
                target = spec.workdir / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("generated\n")
                files = [name]
                await sink("files", {"paths": files})
            history = self.history.setdefault(spec.native_id, [])
            history += [
                Message("user", text, time.time()),
                Message("assistant", reply, time.time()),
            ]
            await sink("message", {"text": reply})
            if text.startswith("tools-after:"):
                # 模拟官方程序在同一条消息里既回答又调工具：回答的事件先到，工具的事件在后
                commands, files = ["ls -la"], ["src/b.py"]
                await sink("command", {"command": commands[0]})
                await sink("files", {"paths": files})
            return TurnOutcome(
                text=reply,
                usage={"input_tokens": 1, "output_tokens": 1},
                commands=commands,
                files=files,
            )
        finally:
            self.running -= 1

    async def quota(self) -> list[dict[str, Any]] | None:
        if not self.can_query_quota:
            return None
        if self.quota_until > time.time():
            return [
                {
                    "kind": "five_hour",
                    "used_percent": 100.0,
                    "resets_at": self.quota_until,
                    "status": "exceeded",
                    "plan": "fake",
                }
            ]
        return self.current_quota

    async def cancel(self, spec: SessionSpec) -> None:
        if spec.native_id in self._cancel:
            self._cancel[spec.native_id].set()

    async def steer(self, spec: SessionSpec, text: str) -> bool:
        if not self.steerable or self.running == 0:
            return False
        self.steered.append(text)
        return True

    async def list_native(self, limit: int) -> list[NativeSession]:
        return self.external[:limit]

    async def read(self, native_id: str, workdir: str | None) -> list[Message]:
        return list(self.history.get(native_id, []))

    async def login(self, method: str, payload: dict[str, str]) -> LoginChallenge:
        if self.login_error:
            raise DriverError("platform_error", self.login_error)
        self.identity = payload.get("api_key") or "fake@example.com"
        return LoginChallenge(state="done")

    async def logout(self) -> None:
        self.identity = None
