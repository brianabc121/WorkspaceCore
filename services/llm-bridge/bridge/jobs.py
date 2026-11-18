"""后台作业的进程（docs/v2/LLM_BRIDGE_LONGRUN_PLAN.md 5.3）。

管理器在会话的工作目录里替它跑一条长命令，和官方程序的进程分开：
回答结束了作业照样跑。
- 进程：`bash -c <命令>`，独立的进程组；停止时先发 TERM，过一会儿还没停再发 KILL，
  整个进程组一起停。不用登录 shell（-l）：不读 ~/.bash_profile 这类启动文件，
  一个作业写进去的东西不会在以后的作业里执行（T-0292 审查）。
- 退出的子孙进程：管理器所在容器的 1 号进程是 bridge/pid1.py，它回收所有孤儿进程，
  放到后台的命令、被停掉的管道都不会留下僵尸进程。
  主进程结束后，留在进程组里的后台子进程也一起停掉（不留孤儿进程）。
- 日志：标准输出和标准错误合在一起写进文件。超过 LOG_CAP 后不再写文件，只在内存里留最近的
  TAIL_KEEP；作业结束时把“中间省略了多少”和最近的内容补到文件末尾。
- “最后一次有输出的时间”只在真有输出时更新（心跳只认真实进展）。
- 管理器所在的容器一重启，作业进程就没了：启动时由 LongRun 把仍记为“运行中”的作业改成“结果未知”。
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

LOG_CAP = 20 * 1024 * 1024  # 日志文件最多写这么多
TAIL_KEEP = 256 * 1024  # 超过上限后在内存里留最近的这么多
CHUNK = 64 * 1024
FLUSH_SECONDS = 5.0  # 进度（日志字节数、最后输出时间）最多隔这么久报一次
STOP_GRACE = 10.0  # 发 TERM 之后等多久再发 KILL
DRAIN_SECONDS = 5.0  # 主进程结束后，再等多久把输出读完
POLL_SECONDS = 0.2  # 隔多久看一次主进程结束了没有

ExitHandler = Callable[[str, str, int | None, str | None], Awaitable[None]]
ProgressHandler = Callable[[str, int, float | None], None]


@dataclass(frozen=True)
class RemoteJob:
    """远程开发容器里的作业（docs/v2/LLM_BRIDGE_REMOTE_ENV_PLAN.md 第 14 节）：
    经工作台在容器里起托管进程（purpose=job，argv=["bash","-c",命令]），输出照样写进管理器里的日志。"""

    hub: Any  # bridge.remote.RemoteHub（不在这里导入，免得循环）
    container: str
    workdir: str
    session_id: str


@dataclass
class _Running:
    job_id: str
    proc: Any  # asyncio.subprocess.Process，或远程进程（bridge.remote.RemoteProcess）
    path: Path
    timeout: float
    written: int = 0
    total: int = 0
    tail: bytearray = field(default_factory=bytearray)
    dropped: int = 0
    last_output_at: float | None = None
    reason: str | None = None  # stopped / timeout：不是自己结束的
    supervisor: asyncio.Task[None] | None = None


class JobRunner:
    def __init__(
        self, log_dir: Path, on_exit: ExitHandler, on_progress: ProgressHandler | None = None
    ) -> None:
        self.log_dir = log_dir
        self._on_exit = on_exit
        self._on_progress = on_progress
        self._running: dict[str, _Running] = {}

    def log_path(self, job_id: str) -> Path:
        return self.log_dir / f"{job_id}.log"

    def is_running(self, job_id: str) -> bool:
        return job_id in self._running

    async def start(
        self,
        job_id: str,
        command: str,
        cwd: Path,
        env: dict[str, str],
        limit_seconds: float,
        remote: RemoteJob | None = None,
    ) -> int:
        """起一个作业，返回进程号（远程作业是 0）。失败抛 OSError。"""
        self.log_dir.mkdir(parents=True, exist_ok=True)
        path = self.log_path(job_id)
        path.write_bytes(b"")
        if remote is not None:
            from bridge.drivers.base import DriverError

            try:
                proc: Any = await remote.hub.spawn(
                    remote.container,
                    purpose="job",
                    platform=None,
                    session_id=remote.session_id,
                    # 标准错误并进标准输出，和本地作业一样写进同一份日志
                    argv=["bash", "-c", f"exec 2>&1\n{command}"],
                    cwd=remote.workdir,
                    env=env,
                    timeout_seconds=int(limit_seconds) + 60,
                )
            except DriverError as error:
                raise OSError(error.message) from error
            running = _Running(job_id=job_id, proc=proc, path=path, timeout=limit_seconds)
            self._running[job_id] = running
            running.supervisor = asyncio.create_task(self._supervise(running), name=f"job-{job_id}")
            return 0
        proc = await asyncio.create_subprocess_exec(
            "/bin/bash",
            "-c",
            command,
            cwd=str(cwd),
            env=env,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,  # 独立的进程组：停的时候整组一起停
        )
        running = _Running(job_id=job_id, proc=proc, path=path, timeout=limit_seconds)
        self._running[job_id] = running
        running.supervisor = asyncio.create_task(self._supervise(running), name=f"job-{job_id}")
        return int(proc.pid)

    async def stop(self, job_id: str, reason: str = "stopped") -> bool:
        """停掉一个正在跑的作业（reason：stopped / timeout）；它不在跑就返回 False。"""
        running = self._running.get(job_id)
        if running is None:
            return False
        running.reason = running.reason or reason
        await self._kill(running)
        if running.supervisor is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.shield(running.supervisor)
        return True

    async def shutdown(self) -> None:
        """管理器关闭：停掉全部作业，但不报“结束”——它们在库里还是运行中，下次启动时记为结果未知。"""
        running = list(self._running.values())
        for item in running:
            if item.supervisor is not None:
                item.supervisor.cancel()
        for item in running:
            self._signal(item, signal.SIGKILL)
        self._running.clear()

    def read_log(self, job_id: str, max_bytes: int) -> tuple[str, int]:
        """日志的最后 max_bytes 字节（文本，可能从半行开始）和一共产生了多少字节。"""
        running = self._running.get(job_id)
        path = self.log_path(job_id)
        data = b""
        with contextlib.suppress(OSError):
            size = path.stat().st_size
            with path.open("rb") as handle:
                handle.seek(max(0, size - max_bytes))
                data = handle.read(max_bytes)
        total = len(data)
        if running is not None:
            total = running.total
            if running.dropped:
                gap = f"\n…（中间省略了 {running.dropped} 字节）…\n".encode()
                data = (data + gap + bytes(running.tail))[-max_bytes:]
        return data.decode("utf-8", errors="replace"), total

    # ---- 内部 ----
    async def _supervise(self, running: _Running) -> None:
        pump = asyncio.create_task(self._pump(running))
        error: str | None = None
        try:
            # 主进程什么时候结束：看退出码。asyncio 的 wait() 要等输出管道全部关闭才返回，
            # 放到后台的子进程还占着输出时会一直等下去（Python 3.12 的 base_subprocess）
            if not await self._exited(running, running.timeout):
                running.reason = running.reason or "timeout"
                await self._kill(running)
            # 主进程结束了：等输出读完；还有子进程占着输出（放到后台的）就整组停掉
            try:
                await asyncio.wait_for(asyncio.shield(pump), DRAIN_SECONDS)
            except TimeoutError:
                self._signal(running, signal.SIGKILL)
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(asyncio.shield(pump), DRAIN_SECONDS)
            self._signal(running, signal.SIGKILL)  # 主进程结束后，留在组里的后台子进程一起停
        except asyncio.CancelledError:
            pump.cancel()
            raise
        except Exception as caught:
            error = f"管理作业的进程出错：{caught}"
        finally:
            if not pump.done():
                pump.cancel()
        self._finish_log(running)
        self._running.pop(running.job_id, None)
        self._report(running)
        code = running.proc.returncode
        status = running.reason or ("succeeded" if code == 0 else "failed")
        await self._on_exit(running.job_id, status, code, error)

    @staticmethod
    async def _exited(running: _Running, seconds: float) -> bool:
        """在 seconds 秒内主进程有没有结束。"""
        deadline = time.monotonic() + seconds
        while running.proc.returncode is None:
            if time.monotonic() >= deadline:
                return False
            await asyncio.sleep(POLL_SECONDS)
        return True

    async def _pump(self, running: _Running) -> None:
        stream = running.proc.stdout
        if stream is None:
            return
        flushed = 0.0
        with running.path.open("ab") as handle:
            while chunk := await stream.read(CHUNK):
                running.total += len(chunk)
                running.last_output_at = time.time()
                room = LOG_CAP - running.written
                if room > 0:
                    handle.write(chunk[:room])
                    handle.flush()
                    running.written += min(room, len(chunk))
                    chunk = chunk[room:]
                if chunk:
                    running.tail += chunk
                    running.dropped += len(chunk)
                    if len(running.tail) > TAIL_KEEP:
                        del running.tail[: len(running.tail) - TAIL_KEEP]
                if time.monotonic() - flushed >= FLUSH_SECONDS:
                    flushed = time.monotonic()
                    self._report(running)

    def _finish_log(self, running: _Running) -> None:
        if not running.dropped:
            return
        kept = len(running.tail)
        with contextlib.suppress(OSError), running.path.open("ab") as handle:
            handle.write(f"\n…（中间省略了 {running.dropped - kept} 字节）…\n".encode())
            handle.write(bytes(running.tail))

    def _report(self, running: _Running) -> None:
        if self._on_progress is not None:
            self._on_progress(running.job_id, running.total, running.last_output_at)

    async def _kill(self, running: _Running) -> None:
        """先 TERM，过 STOP_GRACE 秒主进程还没结束再 KILL（都发给整个进程组）。"""
        if running.proc.returncode is not None:
            return
        self._signal(running, signal.SIGTERM)
        if not await self._exited(running, STOP_GRACE):
            self._signal(running, signal.SIGKILL)

    @staticmethod
    def _signal(running: _Running, sig: signal.Signals) -> None:
        if running.proc.pid is None:  # 远程作业：经工作台给容器里的整个进程组发信号
            if sig == signal.SIGKILL:
                running.proc.kill()
            else:
                running.proc.terminate()
            return
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(running.proc.pid, sig)
