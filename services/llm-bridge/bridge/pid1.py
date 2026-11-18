"""MCP 管理器容器里的 1 号进程：启动管理器，转发停止信号，回收退出的孤儿进程。

为什么不用 Docker 的 `init: true`（tini）（T-0292 审查）：
- 后台作业、官方程序跑的命令放到后台（`cmd &`）、被停掉的管道，主进程结束后剩下的子进程
  会交给容器的 1 号进程。1 号进程不回收的话，它们退出后一直是僵尸进程，占着 pids_limit，
  多了整个管理器就起不了新进程。所以 1 号进程必须回收它们。
- 管理令牌（BRIDGE_ADMIN_TOKEN）在容器的环境变量里，1 号进程的环境变量也有一份。
  作业、官方程序都和管理器是同一个用户，能读同一个用户的进程的 /proc/<pid>/environ。
  tini 不会挡这个；这里的 1 号进程和管理器都设成“不可转储”（PR_SET_DUMPABLE=0），
  同一个用户的其他进程就读不到它们的环境变量和内存（没有 CAP_SYS_PTRACE，容器里 cap_drop: ALL）。

用法（Dockerfile 的 CMD）：`python -I /app/bridge/pid1.py uvicorn bridge.main:app ...`
不是 1 号进程时（例如直接运行测试）也能用：设成“子进程收割者”，管理器的孤儿进程交给它回收。
只用标准库。
"""

from __future__ import annotations

import contextlib
import ctypes
import os
import signal
import sys
from types import FrameType

PR_SET_DUMPABLE = 4
PR_GET_DUMPABLE = 3
PR_SET_CHILD_SUBREAPER = 36
FORWARDED = (
    signal.SIGTERM,
    signal.SIGINT,
    signal.SIGHUP,
    signal.SIGQUIT,
    signal.SIGUSR1,
    signal.SIGUSR2,
)


def _prctl(option: int, value: int = 0) -> int:
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        return int(libc.prctl(option, value, 0, 0, 0))
    except (OSError, AttributeError):
        return -1


def harden() -> bool:
    """这个进程设成不可转储：同一个用户的其他进程读不了它的环境变量、内存，也不能附加调试。

    只对这个进程有效（exec 别的程序后会恢复）；管理器自己启动时也调用一次（bridge/main.py）。"""
    return _prctl(PR_SET_DUMPABLE, 0) == 0


def dumpable() -> bool:
    return _prctl(PR_GET_DUMPABLE) == 1


def _exit_code(status: int) -> int:
    code = os.waitstatus_to_exitcode(status)
    return 128 - code if code < 0 else code


def run(argv: list[str]) -> int:
    if not argv:
        print("用法：pid1.py 程序 [参数…]", file=sys.stderr)
        return 2
    harden()
    if os.getpid() != 1:
        _prctl(PR_SET_CHILD_SUBREAPER, 1)
    child = os.fork()
    if child == 0:  # 子进程：信号处理还是默认的，直接换成管理器
        try:
            os.execvp(argv[0], argv)  # noqa: S606 - 启动的就是 Dockerfile CMD 里写定的管理器
        except OSError as error:
            print(f"pid1：启动不了 {argv[0]}：{error}", file=sys.stderr)
        os._exit(127)

    def forward(signum: int, _frame: FrameType | None) -> None:
        with contextlib.suppress(ProcessLookupError):
            os.kill(child, signum)

    for signum in FORWARDED:
        signal.signal(signum, forward)
    code = 0
    while True:
        try:
            pid, status = os.waitpid(-1, 0)
        except ChildProcessError:
            return code
        if pid == child:
            code = _exit_code(status)
            break
    # 管理器结束了：把已经退出的收掉，不等还在跑的（容器随后停止，它们一起结束）
    while True:
        try:
            pid, _status = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            break
        if pid == 0:
            break
    return code


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
