"""测试用的驱动：不访问任何外部服务。BRIDGE_ENABLE_FAKE=true 时才会登记成平台 fake。

消息约定：`sleep:<秒>` 等这么久再回答；`fail:<错误类别>` 抛出对应的错误；其余原样回显。
"""

from __future__ import annotations

import asyncio
import time
import uuid

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
        self.history: dict[str, list[Message]] = {}
        self.external: list[NativeSession] = []
        self.running = 0
        self.max_running = 0
        self._cancel: dict[str, asyncio.Event] = {}

    def supports(self, mode: Mode) -> bool:
        return mode in ("chat", "dev")

    async def status(self) -> AccountStatus:
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
            if text.startswith("sleep:"):
                waiter = asyncio.create_task(cancel.wait())
                done, _ = await asyncio.wait({waiter}, timeout=float(text[6:]))
                waiter.cancel()
                if done:
                    raise DriverError("platform_error", "回合被中断")
            reply = f"[{spec.model}] {text}"
            history = self.history.setdefault(spec.native_id, [])
            history += [
                Message("user", text, time.time()),
                Message("assistant", reply, time.time()),
            ]
            await sink("message", {"text": reply})
            return TurnOutcome(text=reply, usage={"input_tokens": 1, "output_tokens": 1})
        finally:
            self.running -= 1

    async def cancel(self, spec: SessionSpec) -> None:
        if spec.native_id in self._cancel:
            self._cancel[spec.native_id].set()

    async def list_native(self, limit: int) -> list[NativeSession]:
        return self.external[:limit]

    async def read(self, native_id: str, workdir: str | None) -> list[Message]:
        return list(self.history.get(native_id, []))

    async def login(self, method: str, payload: dict[str, str]) -> LoginChallenge:
        self.identity = payload.get("api_key") or "fake@example.com"
        return LoginChallenge(state="done")

    async def logout(self) -> None:
        self.identity = None
