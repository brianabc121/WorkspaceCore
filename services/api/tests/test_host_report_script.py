"""接入包的 bin/host-report.mjs（T-0113；T-0146）：解析 /proc、找默认网卡、cgroup 上限与用量
（v2 与 v1）、数据目录大小、服务器指纹（boot_id 的 HMAC）、上报。

用 Node 直接运行脚本：MYWS_PROC_ROOT / MYWS_SYS_ROOT 指向测试目录里的假文件，本机 HTTP 服务收上报。
没有 node 时跳过。
"""

import hashlib
import hmac
import json
import shutil
import subprocess
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, ClassVar

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "app/agent_bundle/files/bin/host-report.mjs"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="没有 node")

STAT = "cpu  100 5 50 800 20 1 2 0 0 0\ncpu0 50 2 25 400 10 0 1 0 0 0\n"
MEMINFO = (
    "MemTotal:       16477348 kB\nMemFree:          100000 kB\n"
    "MemAvailable:   15894632 kB\nSwapTotal:             0 kB\nSwapFree:              0 kB\n"
)
ROUTE = (
    "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\n"
    "docker0\t000011AC\t00000000\t0001\t0\t0\t0\t0000FFFF\n"
    "eth0\t00000000\t0100A8C0\t0003\t0\t0\t100\t00000000\n"
)
DEV = (
    "Inter-|   Receive                                                |  Transmit\n"
    " face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets\n"
    "    lo: 999 1 0 0 0 0 0 0 999 1 0 0 0 0 0 0\n"
    "  eth0: 123456 100 0 0 0 0 0 0 654321 90 0 0 0 0 0 0\n"
)


def _node(code: str, env: dict[str, str] | None = None) -> Any:
    assert NODE is not None
    result = subprocess.run(  # noqa: S603 - 测试里运行固定的脚本
        [
            NODE,
            "--input-type=module",
            "-e",
            f"import * as m from {json.dumps(SCRIPT.as_uri())};\n{code}",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
        env=env,
    )
    return json.loads(result.stdout)


def test_parsers() -> None:
    parsed = _node(
        "console.log(JSON.stringify({"
        f"cpu: m.cpuTimes({json.dumps(STAT)}),"
        "load: m.loadavg('2.67 0.86 0.32 3/512 1234\\n'),"
        f"mem: m.meminfo({json.dumps(MEMINFO)}),"
        f"iface: m.defaultInterface({json.dumps(ROUTE)}),"
        f"bytes: m.interfaceBytes({json.dumps(DEV)}, 'eth0'),"
        "v2: m.memoryLimit('max\\n', null, 100),"
        "v1: m.memoryLimit(null, '9223372036854771712\\n', 100),"
        "limited: m.memoryLimit('50\\n', null, 100)}))"
    )
    assert parsed["cpu"] == {"total": 978, "idle": 820}
    assert parsed["load"] == [2.67, 0.86, 0.32]
    assert parsed["mem"]["total"] == 16477348 * 1024 and parsed["mem"]["swap_total"] == 0
    assert parsed["iface"] == "eth0"
    assert parsed["bytes"] == {"rx": 123456, "tx": 654321}
    assert parsed["v2"] is None and parsed["v1"] is None and parsed["limited"] == 50


BOOT_ID = "2a89bea7-fcae-4a3d-9d61-9a0cb7bb81f8"


def test_container_usage_v2_and_v1(tmp_path: Path) -> None:
    v2 = tmp_path / "v2/fs/cgroup"
    v2.mkdir(parents=True)
    (v2 / "cgroup.controllers").write_text("cpu memory pids\n")
    (v2 / "cpu.stat").write_text("usage_usec 353727\nuser_usec 200000\nsystem_usec 153727\n")
    (v2 / "memory.current").write_text("30000000\n")
    (v2 / "memory.stat").write_text("anon 18000000\nfile 12000000\ninactive_file 11981696\n")
    (v2 / "pids.current").write_text("7\n")
    v1 = tmp_path / "v1/fs/cgroup"
    for sub_dir in ("cpuacct", "memory", "pids"):
        (v1 / sub_dir).mkdir(parents=True)
    (v1 / "cpuacct/cpuacct.usage").write_text("353727123\n")  # 纳秒
    (v1 / "memory/memory.usage_in_bytes").write_text("30000000\n")
    (v1 / "memory/memory.stat").write_text("cache 12000000\ntotal_inactive_file 11981696\n")
    (v1 / "pids/pids.current").write_text("7\n")
    (tmp_path / "none/fs/cgroup").mkdir(parents=True)
    parsed = _node(
        "console.log(JSON.stringify({"
        f"v2: m.containerUsage({json.dumps(str(tmp_path / 'v2'))}),"
        f"v1: m.containerUsage({json.dumps(str(tmp_path / 'v1'))}),"
        f"none: m.containerUsage({json.dumps(str(tmp_path / 'none'))})}}))"
    )
    expected = {"cpu_usec": 353727, "mem_bytes": 30000000 - 11981696, "pids": 7}
    assert parsed["v2"] == expected and parsed["v1"] == expected and parsed["none"] is None


def test_directory_size_and_host_key(tmp_path: Path) -> None:
    data = tmp_path / "state"
    (data / "agents/main/sessions").mkdir(parents=True)
    (data / "openclaw.json").write_bytes(b"x" * 100)
    (data / "agents/main/sessions/a.jsonl").write_bytes(b"y" * 1000)
    (data / "link").symlink_to(tmp_path / "elsewhere")  # 不跟随符号链接
    parsed = _node(
        "console.log(JSON.stringify({"
        f"all: m.directorySize({json.dumps(str(data))}),"
        f"capped: m.directorySize({json.dumps(str(data))}, 1),"
        f"key: m.hostKey({json.dumps(BOOT_ID + chr(10))}, 'salt'),"
        "noSalt: m.hostKey('" + BOOT_ID + "', ''),"
        "bad: m.hostKey('not a boot id', 'salt')}))"
    )
    assert parsed["all"] == {"bytes": 1100, "truncated": False}
    assert parsed["capped"]["truncated"] is True
    assert parsed["key"] == hmac.new(b"salt", BOOT_ID.encode(), hashlib.sha256).hexdigest()
    assert parsed["noSalt"] is None and parsed["bad"] is None


class _Collector(BaseHTTPRequestHandler):
    seen: ClassVar[list[tuple[str, str, dict[str, Any]]]] = []
    status: ClassVar[int] = 200

    def do_POST(self) -> None:
        length = int(self.headers["content-length"])
        body = json.loads(self.rfile.read(length))
        self.seen.append((self.path, self.headers.get("authorization", ""), body))
        self.send_response(self.status)
        self.end_headers()

    def log_message(self, *args: Any) -> None:
        return


@pytest.fixture
def server() -> Iterator[str]:
    _Collector.seen.clear()
    _Collector.status = 200
    httpd = HTTPServer(("127.0.0.1", 0), _Collector)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}/agent-api"
    httpd.shutdown()


def _fake_host(tmp_path: Path, server: str) -> dict[str, str]:
    proc = tmp_path / "proc"
    (proc / "net").mkdir(parents=True)
    (proc / "stat").write_text(STAT)
    (proc / "meminfo").write_text(MEMINFO)
    (proc / "loadavg").write_text("0.50 0.40 0.30 1/100 42\n")
    (proc / "uptime").write_text("3600.12 7000.00\n")
    (proc / "net/route").write_text(ROUTE)
    (proc / "net/dev").write_text(DEV)
    (proc / "sys/kernel/random").mkdir(parents=True)
    (proc / "sys/kernel/random/boot_id").write_text(BOOT_ID + "\n")
    sys_root = tmp_path / "sys"
    cgroup = sys_root / "fs/cgroup"
    cgroup.mkdir(parents=True)
    (cgroup / "memory.max").write_text("max\n")
    (cgroup / "cgroup.controllers").write_text("cpu memory pids\n")
    (cgroup / "cpu.stat").write_text("usage_usec 5000000\n")
    (cgroup / "memory.current").write_text("4096\n")
    (cgroup / "memory.stat").write_text("inactive_file 1024\n")
    (cgroup / "pids.current").write_text("3\n")
    env = {
        "PATH": "/usr/bin:/bin",
        "MYWS_AGENT_API": server,
        "MYWS_INSTANCE_TOKEN": "mwa_test",
        "MYWS_PROC_ROOT": str(proc),
        "MYWS_SYS_ROOT": str(sys_root),
        "MYWS_DATA_PATH": str(tmp_path),
        "MYWS_ROOT_PATH": str(tmp_path),
        "MYWS_HOST_SALT": "salt",
        "MYWS_DATA_SIZE": "1",
    }
    return env


def test_main_posts_numbers_only(server: str, tmp_path: Path) -> None:
    env = _fake_host(tmp_path, server)
    assert _node("console.log(JSON.stringify(await m.main()))", env=env) == "ok"
    [(path, auth, body)] = _Collector.seen
    assert path == "/agent-api/v1/host-metrics" and auth == "Bearer mwa_test"
    assert set(body) == {
        "cpu",
        "memory",
        "disks",
        "network",
        "uptime_seconds",
        "container",
        "host_key",
    }
    box = body["container"]
    assert box["cpu_usec"] == 5000000 and box["mem_bytes"] == 3072 and box["pids"] == 3
    assert box["data_bytes"] > 0 and box["data_truncated"] is False
    assert body["host_key"] == hmac.new(b"salt", BOOT_ID.encode(), hashlib.sha256).hexdigest()
    assert body["cpu"]["total_ticks"] == 978 and body["cpu"]["idle_ticks"] == 820
    assert body["cpu"]["load"] == [0.5, 0.4, 0.3]
    assert body["memory"]["limit"] is None and body["memory"]["available"] == 15894632 * 1024
    assert body["network"] == {"rx_bytes": 123456, "tx_bytes": 654321}
    assert body["uptime_seconds"] == 3600
    assert body["disks"]["data"]["total"] > 0 and body["disks"]["root"]["free"] >= 0
    # 只有数字与 CPU 型号：没有网卡名、IP、路径
    flat = json.dumps(body)
    assert "eth0" not in flat and str(tmp_path) not in flat and BOOT_ID not in flat


def test_rate_limited_minute_is_not_a_failure(server: str, tmp_path: Path) -> None:
    """这一分钟已经报过（定时任务刚跑过，又点了“立即上报一次”）：工作台返回 429，脚本正常结束。"""
    env = _fake_host(tmp_path, server)
    _Collector.status = 429
    assert _node("console.log(JSON.stringify(await m.main()))", env=env) == "rate_limited"
    _Collector.status = 500
    failed = _node(
        "try { await m.main(); console.log('null') }"
        " catch (e) { console.log(JSON.stringify(e.message)) }",
        env=env,
    )
    assert failed == "workspace returned 500"
