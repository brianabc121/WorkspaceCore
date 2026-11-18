"""容器的 1 号进程（bridge/pid1.py，T-0292 审查）：不可转储、转发停止信号、回收孤儿进程。"""

from __future__ import annotations

import signal
import subprocess
import sys
import time
from pathlib import Path

from bridge import pid1

SCRIPT = Path(pid1.__file__)
ROOT = SCRIPT.parent.parent


def _children(pid: int) -> dict[int, str]:
    """pid 的子进程和它们的状态（R / S / Z …）。"""
    found: dict[int, str] = {}
    for stat in Path("/proc").glob("[0-9]*/stat"):
        try:
            fields = stat.read_text().rsplit(")", 1)[1].split()
        except (OSError, IndexError):
            continue
        if int(fields[1]) == pid:
            found[int(stat.parent.name)] = fields[0]
    return found


def test_harden_makes_the_process_not_dumpable() -> None:
    # 不可转储：同一个用户的其他进程读不了它的 /proc/<pid>/environ（里面有管理令牌）
    code = "from bridge.pid1 import dumpable, harden; print(dumpable(), harden(), dumpable())"
    out = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=True
    )
    assert out.stdout.split() == ["True", "True", "False"]


def test_reaps_orphans_and_passes_the_exit_code() -> None:
    # 主进程还在跑时，它留下的孤儿进程交给 pid1，退出后马上被回收，不留僵尸进程
    script = "(sleep 0.6 &); (sleep 0.6 &); sleep 2; exit 5"
    proc = subprocess.Popen([sys.executable, "-I", str(SCRIPT), "sh", "-c", script])
    try:
        time.sleep(0.3)
        assert len(_children(proc.pid)) >= 3  # sh 和交给它的两个 sleep
        time.sleep(1.0)
        children = _children(proc.pid)
        assert "Z" not in children.values() and len(children) == 1, children
        assert proc.wait(10) == 5
    finally:
        proc.kill()


def test_forwards_stop_signals() -> None:
    proc = subprocess.Popen([sys.executable, "-I", str(SCRIPT), "sleep", "30"])
    try:
        time.sleep(0.3)
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(10) == 128 + signal.SIGTERM
    finally:
        proc.kill()


def test_missing_program_and_usage() -> None:
    missing = subprocess.run(
        [sys.executable, "-I", str(SCRIPT), "/no/such/program"], capture_output=True, text=True
    )
    assert missing.returncode == 127 and "启动不了" in missing.stderr
    assert subprocess.run([sys.executable, "-I", str(SCRIPT)], capture_output=True).returncode == 2
