"""智能体服务器详情：连接延迟与资源视图（T-0271）。

- 测试连接从同一条 SSH 连接读出内核 TCP_INFO 的往返时间（不另外建连接）；
- 平台的 SSH 资源采样附带挂载的本地硬盘（只用于展示，坏数据丢掉、不影响调度用的快照）；
- 详情接口把 resource_snapshot 整理成强类型的 `resources`（CPU、内存、硬盘与挂载盘）。
"""

import json
import os
import shutil
import socket
import struct
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest

from app.domain import agent_server_resources as resources
from app.domain.agent_server_ssh import connection_latency_ms
from app.domain.agent_servers import _resources_out
from app.models import AgentServer
from tests import test_agent_ssh_install as base
from tests.test_agent_placement import snapshot
from tests.test_agent_ssh_install import FakeHost, Owner, ssh_host

owner = base.owner
fast_online = base.fast_online

# ---------------------------------------------------------------- 连接延迟


class _Socket:
    def __init__(self, raw: bytes | Exception) -> None:
        self.raw = raw

    def getsockopt(self, level: int, option: int, size: int) -> bytes:
        assert level == socket.IPPROTO_TCP and size == 104
        if isinstance(self.raw, Exception):
            raise self.raw
        return self.raw


class _Conn:
    def __init__(self, sock: object) -> None:
        self.sock = sock

    def get_extra_info(self, name: str) -> object:
        assert name == "socket"
        return self.sock


def _tcp_info(rtt_us: int) -> bytes:
    raw = bytearray(104)
    struct.pack_into("=I", raw, 68, rtt_us)
    return bytes(raw)


needs_tcp_info = pytest.mark.skipif(not hasattr(socket, "TCP_INFO"), reason="TCP_INFO 只在 Linux")


@needs_tcp_info
def test_latency_reads_tcp_info_rtt() -> None:
    assert connection_latency_ms(_Conn(_Socket(_tcp_info(42_317)))) == pytest.approx(42.317)  # type: ignore[arg-type]
    assert connection_latency_ms(_Conn(_Socket(_tcp_info(80)))) == pytest.approx(0.08)  # type: ignore[arg-type]


@needs_tcp_info
@pytest.mark.parametrize(
    "sock",
    [
        None,  # 拿不到套接字
        _Socket(OSError("not a TCP socket")),
        _Socket(b"\x01" * 70),  # 结构太短
        _Socket(_tcp_info(0)),  # 内核还没有测量值
        _Socket(_tcp_info(60_000_000)),  # 不合理的值
    ],
)
def test_latency_unknown_never_breaks_the_check(sock: object) -> None:
    assert connection_latency_ms(_Conn(sock)) is None  # type: ignore[arg-type]


@needs_tcp_info
def test_latency_on_a_real_loopback_connection() -> None:
    """核对 tcpi_rtt 的偏移：在真实的 TCP 连接上读出一个很小的正数。"""
    server = socket.create_server(("127.0.0.1", 0))
    client = socket.create_connection(server.getsockname())
    accepted, _ = server.accept()
    try:
        client.sendall(b"ping")
        accepted.recv(4)
        accepted.sendall(b"pong")
        client.recv(4)
        latency = connection_latency_ms(_Conn(client))  # type: ignore[arg-type]
        assert latency is not None and 0 < latency < 1000
    finally:
        for s in (client, accepted, server):
            s.close()


# ---------------------------------------------------------------- 挂载盘


def _mount(**changes: Any) -> dict[str, Any]:
    return {
        "device": "/dev/vda1",
        "fs_type": "ext4",
        "mount": "/",
        "total": 100 << 30,
        "used": 40 << 30,
        "available": 55 << 30,
        **changes,
    }


def test_parse_mounts_keeps_valid_rows() -> None:
    rows = [_mount(), _mount(device="/dev/mapper/data-vol", mount="/data", fs_type="xfs")]
    assert resources.parse_mounts(rows) == rows
    assert resources.parse_mounts([]) == []


@pytest.mark.parametrize(
    "rows",
    [
        "not a list",
        [_mount(device="tmpfs")],
        [_mount(mount="/mnt/with space")],
        [_mount(mount='/x"y')],
        [_mount(used=(100 << 30) + 1)],
        [_mount(available=-1)],
        [_mount(total=0)],
        [_mount(extra="field")],
        [_mount(), _mount(mount="/again")],  # 同一个设备出现两次
        [_mount(device=f"/dev/sd{i}") for i in range(resources.MAX_MOUNTS + 1)],
    ],
)
def test_parse_mounts_drops_malformed_lists(rows: object) -> None:
    assert resources.parse_mounts(rows) == []


def test_bad_mounts_never_invalidate_the_placement_snapshot() -> None:
    now = datetime.now(UTC)
    good = resources.parse_resources(
        json.dumps({**snapshot(), "mounts": [_mount()]}), received_at=now
    )
    assert good["mounts"] == [_mount()] and good["cores"] == 4
    bad = resources.parse_resources(
        json.dumps({**snapshot(), "mounts": [{"device": "x"}]}), received_at=now
    )
    assert bad["mounts"] == [] and bad["cores"] == 4 and resources.is_fresh(bad, now)
    old = resources.parse_resources(json.dumps(snapshot()), received_at=now)  # 旧脚本没有这一项
    assert old["mounts"] == []


DF_OUTPUT = """\
Filesystem     Type     1024-blocks      Used Available Capacity Mounted on
tmpfs          tmpfs        1638400      1200   1637200       1% /run
/dev/vda1      ext4       102400000  40960000  56320000      43% /
/dev/sda1      ext4       102400000  40960000  56320000      43% /etc/hosts
/dev/vda15     vfat          106832      6186    100646       6% /boot/efi
/dev/loop0     squashfs       65536     65536         0     100% /snap/core/1
overlay        overlay    102400000  40960000  56320000      43% /var/lib/docker/overlay2/x/merged
/dev/vdb       ext4       209715200  10485760 188743680       6% /data
/dev/vdb       ext4       209715200  10485760 188743680       6% /data/bind
/dev/vdc       ext4        10485760   1048576   9437184      11% /mnt/my disk
/dev/vdd       ext4        10485760   1048576   9437184      11% /mnt/quo"te
"""


def _mounts_line() -> str:
    lines = [line for line in resources.PROBE_SCRIPT.splitlines() if line.startswith("mounts=")]
    assert len(lines) == 1
    return lines[0]


def _run_mounts(tmp_path: Path, df: str) -> list[dict[str, Any]]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    fake = bin_dir / "df"
    fake.write_text(df)
    fake.chmod(0o755)
    script = f"set -eu\nexport LC_ALL=C\n{_mounts_line()}\nprintf '[%s]' \"$mounts\"\n"
    out = subprocess.run(
        ["/bin/sh", "-s"],
        input=script,
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": f"{bin_dir}:/usr/bin:/bin"},
        check=True,
    )
    parsed: list[dict[str, Any]] = json.loads(out.stdout)
    return parsed


def test_probe_lists_local_disks_once_with_safe_names(tmp_path: Path) -> None:
    listed = _run_mounts(tmp_path, f"#!/bin/sh\ncat <<'EOF'\n{DF_OUTPUT}EOF\n")
    assert [(m["device"], m["mount"]) for m in listed] == [
        ("/dev/vda1", "/"),
        ("/dev/vda15", "/boot/efi"),
        ("/dev/vdb", "/data"),
    ]
    assert listed[0] == {
        "device": "/dev/vda1",
        "fs_type": "ext4",
        "mount": "/",
        "total": 102400000 * 1024,
        "used": 40960000 * 1024,
        "available": 56320000 * 1024,
    }
    assert resources.parse_mounts(listed) == listed


def test_probe_mounts_on_this_machine_are_accepted(tmp_path: Path) -> None:
    """在本机真实的 df 上跑一遍：输出必须是能通过校验的 JSON（列出的都是本地 /dev 硬盘）。"""
    real_df = shutil.which("df")
    assert real_df
    listed = _run_mounts(tmp_path, f'#!/bin/sh\nexec {real_df} "$@"\n')
    assert resources.parse_mounts(listed) == listed
    assert all(m["device"].startswith("/dev/") and m["fs_type"] != "squashfs" for m in listed)


def test_probe_mount_list_is_capped_and_never_fails(tmp_path: Path) -> None:
    rows = "".join(
        f"/dev/sd{i:02d} ext4 1000 10 990 1% /d{i}\n" for i in range(resources.MAX_MOUNTS + 4)
    )
    many = _run_mounts(tmp_path, f"#!/bin/sh\ncat <<'EOF'\nheader\n{rows}EOF\n")
    assert len(many) == resources.MAX_MOUNTS
    # df 失败（或者没有 timeout / df）：空列表，不让整次采样失败
    assert _run_mounts(tmp_path, "#!/bin/sh\nexit 3\n") == []


# ---------------------------------------------------------------- 详情里的 resources


def _server(snap: dict[str, Any], kind: str = "ssh") -> AgentServer:
    return AgentServer(kind=kind, resource_snapshot=snap)


def _sampled(now: datetime, **changes: Any) -> dict[str, Any]:
    value = resources.parse_resources(json.dumps({**snapshot(), **changes}), received_at=now)
    value["physical_identity_verified"] = True
    value["probe_attempted_at"] = value["sampled_at"]
    return value


def test_resources_view_from_a_trusted_sample() -> None:
    now = datetime.now(UTC)
    disks = [
        _mount(),
        _mount(device="/dev/vdb", mount="/data", total=200 << 30, used=0, available=190 << 30),
    ]
    out = _resources_out(_server(_sampled(now, mounts=disks)), now)
    assert out is not None and out.sample is not None
    assert out.error is None and not out.stale
    sample = out.sample
    assert (sample.cores, sample.cpu_percent, sample.load1) == (4, 10, 0.2)
    assert (sample.mem_total, sample.mem_used, sample.mem_available) == (
        16 << 30,
        4 << 30,
        12 << 30,
    )
    assert [d.mount for d in sample.disks] == ["/", "/data"]
    assert sample.storage_total == 300 << 30
    assert sample.storage_used == 40 << 30
    assert sample.storage_available == 245 << 30


def test_resources_view_without_disk_list_uses_the_docker_partition() -> None:
    now = datetime.now(UTC)
    out = _resources_out(_server(_sampled(now)), now)
    assert out is not None and out.sample is not None
    assert out.sample.disks == []
    assert (out.sample.storage_total, out.sample.storage_used, out.sample.storage_available) == (
        100 << 30,
        20 << 30,
        80 << 30,
    )


def test_resources_view_states() -> None:
    now = datetime.now(UTC)
    # 自动识别的服务器、还没采样过：null
    assert _resources_out(_server(_sampled(now), kind="auto"), now) is None
    assert _resources_out(_server({}), now) is None
    # 只尝试过、失败了：没有数值，标出原因
    failed = _resources_out(
        _server({"probe_attempted_at": now.isoformat(), "probe_error": "unavailable"}), now
    )
    assert failed is not None and failed.sample is None and failed.stale
    assert failed.error == "unavailable" and failed.attempted_at == now
    conflict = _resources_out(
        _server({**_sampled(now), "probe_error": "PHYSICAL_HOST_CONFLICT"}), now
    )
    assert conflict is not None and conflict.error == "physical_host_conflict"
    assert conflict.sample is not None  # 上一次成功的数值照常显示
    # 超过 3 分钟没有新的成功采样：过期
    later = now + timedelta(minutes=4)
    old = _resources_out(_server(_sampled(now)), later)
    assert old is not None and old.stale and old.sample is not None
    # 不完整的旧格式：不编数值
    broken = _resources_out(_server({"source": "trusted_ssh", "sampled_at": now.isoformat()}), now)
    assert broken is not None and broken.sample is None


# ---------------------------------------------------------------- 接口（真实数据库 + 假服务器）


@pytest.mark.integration
async def test_refresh_returns_cpu_memory_and_mounted_disks(owner: Owner, tmp_path: Path) -> None:
    """初始化之前没有资源；初始化后“刷新资源”用保存的密码登录采样，详情里给出强类型的 resources。"""
    host = FakeHost(tmp_path)
    # 假服务器对采样脚本回固定的 JSON（脚本本身的 df 过滤见上面两个测试）；加上两块挂载盘
    disks = [_mount(), _mount(device="/dev/vdb", mount="/data", total=200 << 30, used=0)]
    cast(dict[str, Any], host.resources)["mounts"] = disks
    async with ssh_host(host) as (port, _):
        instance_id, _ = await owner.prepare(port, initialize=False)
        server_id = owner.servers[instance_id]
        path = f"/api/v1/agents/servers/{server_id}"
        before = await owner.client.get(path, headers=owner.headers())
        assert before.json()["server"]["resources"] is None
        # 还没初始化：不能采样（不登录服务器）
        early = await owner.client.post(f"{path}/resources/refresh", headers=owner.headers())
        assert early.status_code == 503 and early.json()["error"]["code"] == "METRICS_STALE"
        logins = len(host.logins)
        job = await owner.initialize(server_id)
        assert job["status"] == "succeeded", job
        refreshed = await owner.client.post(f"{path}/resources/refresh", headers=owner.headers())
    assert refreshed.status_code == 200, refreshed.text
    assert len(host.logins) > logins
    out = refreshed.json()["server"]["resources"]
    assert out["error"] is None and out["stale"] is False and out["attempted_at"]
    sample = out["sample"]
    assert (sample["cores"], sample["cpu_percent"], sample["load1"]) == (16, 5, 0.2)
    assert (sample["mem_total"], sample["mem_used"], sample["mem_available"]) == (
        32 << 30,
        8 << 30,
        24 << 30,
    )
    assert sample["disks"] == disks
    assert (sample["storage_total"], sample["storage_used"]) == (300 << 30, 40 << 30)
    # GET 详情不登录服务器，返回同一份采样
    detail = await owner.client.get(path, headers=owner.headers())
    assert detail.json()["server"]["resources"] == out
