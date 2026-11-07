"""runner-agent 的纯函数（T-0135 / T-0136）：Docker 多路复用流、tar、查询参数。

与 Docker 的真实交互在 docs/decisions/2026-11-06-runner-verification.md 第 2 节（本地 Docker 实测）
与 CI 的运行服务器验收（T-0141）里验证。
"""

import io
import struct
import tarfile
from collections.abc import Callable
from typing import Any

from app.runner_agent.docker import _url, frames, tar_of


def _frame(kind: int, data: bytes) -> bytes:
    return bytes([kind, 0, 0, 0]) + struct.pack(">I", len(data)) + data


def test_frames_split_across_chunks() -> None:
    raw = _frame(1, b"hello ") + _frame(2, b"oops\n") + _frame(1, b"world\n")
    # 任意切碎：帧头、内容都可能被拆在两块里
    chunks = [raw[i : i + 3] for i in range(0, len(raw), 3)]
    assert list(frames(iter(chunks))) == [(1, b"hello "), (2, b"oops\n"), (1, b"world\n")]


def test_frames_ignore_incomplete_tail() -> None:
    raw = _frame(1, b"done") + _frame(1, b"partial")[:10]
    assert list(frames(iter([raw]))) == [(1, b"done")]


def test_tar_of_sets_owner_and_content() -> None:
    data = tar_of({"a/b.txt": b"x" * 5}, uid=1000, gid=1000)
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        member = tar.getmember("a/b.txt")
        extracted = tar.extractfile(member)
        assert extracted is not None and extracted.read() == b"xxxxx"
        assert (member.uid, member.gid, member.size) == (1000, 1000, 5)


def test_query_encodes_filters_as_json() -> None:
    url = _url("/containers/json", {"all": 1, "filters": {"label": ["myworkspace.env"]}})
    assert url == "/containers/json?all=1&filters=%7B%22label%22%3A+%5B%22myworkspace.env%22%5D%7D"
    assert _url("/version", None) == "/version"


# ---------------------------------------------------------------- 容器参数：宿主保护不随选项变化

BASE: dict[str, object] = {
    "env_id": "0b3c2a8e-0000-4000-8000-000000000001",
    "workspace_id": "0b3c2a8e-0000-4000-8000-000000000002",
    "container": "mw-env-0123456789ab",
    "volume": "mw-vol-0123456789ab",
    "network": "mw-net-0123456789ab",
    "image": "python:3.12-slim",
    "runtime": "gvisor",
    "user": "user",
    "network_mode": "none",
    "domains": [],
    "memory_mb": 2048,
    "cpus": 2,
    "pids": 1024,
    "env": {"A": "1", "HTTP_PROXY": "http://evil", "MYWS_X": "y"},
    "start_command": None,
}


def test_container_config_keeps_host_protections_for_every_choice() -> None:
    from app.runner_agent import containers as c

    for runtime in ("gvisor", "runc", "kvm"):
        for user in ("user", "root"):
            for network in ("none", "allowlist", "all"):
                config = c.container_config(
                    {**BASE, "runtime": runtime, "user": user, "network_mode": network}
                )
                host = config["HostConfig"]
                assert host["Privileged"] is False and host["CapAdd"] == []
                assert host["SecurityOpt"] == ["no-new-privileges:true"]
                assert host["Binds"] == [] and host["NetworkMode"] == "mw-net-0123456789ab"
                assert host["Mounts"] == [
                    {"Type": "volume", "Source": "mw-vol-0123456789ab", "Target": "/workspace"}
                ]
                assert host["IpcMode"] == "private" and host["PidMode"] == ""
                assert host["Memory"] == host["MemorySwap"] == 2048 * 1024 * 1024
                assert "WorkingDir" not in config  # 实测：设了会让空数据卷变成 root 属主
                assert config["Labels"]["myworkspace.runner"] == "1"
                env = config["Env"]
                assert "A=1" in env and "HOME=/workspace/.home" in env
                assert not any(e.startswith("MYWS_") for e in env)
                assert "HTTP_PROXY=http://evil" not in env
                assert ("HTTP_PROXY=http://egress:3128" in env) == (network != "none")
                if user == "user":
                    assert config["User"] == "1000:1000" and host["CapDrop"] == ["ALL"]
                else:
                    assert config["User"] == "0:0" and host["CapDrop"] == []
                assert host["Runtime"] == ("runsc" if runtime == "gvisor" else "runc")
                devices = [d["PathOnHost"] for d in host["Devices"]]
                assert devices == (["/dev/kvm"] if runtime == "kvm" else [])


def test_container_config_rejects_bad_names_and_values() -> None:
    import pytest

    from app.runner_agent import containers as c

    bad: list[dict[str, object]] = [
        {"network": "host"},
        {"network": "bridge"},
        {"volume": "/var/run/docker.sock"},
        {"runtime": "kata"},
        {"user": "nobody"},
        {"network_mode": "host"},
        {"memory_mb": 10},
        {"start_command": "sleep 1"},
    ]
    for change in bad:
        with pytest.raises(c.ParamError):
            c.container_config({**BASE, **change})
    helper = c.helper_config("img", BASE, [{"Source": "mw-vol-0123456789ab", "Target": "/w"}], None)
    assert helper["HostConfig"]["NetworkMode"] == "none"
    assert helper["HostConfig"]["Privileged"] is False
    with pytest.raises(c.ParamError):
        c.helper_config("img", BASE, [{"Source": "/etc", "Target": "/w"}], None)
    with pytest.raises(c.ParamError):
        c.secret_command("../etc/passwd")
    assert c.exec_workdir("src/app") == "/workspace/src/app"
    with pytest.raises(c.ParamError):
        c.exec_workdir("../..")


# ---------------------------------------------------------------- 出口代理


def _resolver(table: dict[str, list[str]]) -> Callable[..., Any]:
    def resolve(host: str, port: int, type: int = 0) -> list[tuple[object, ...]]:
        if host not in table:
            raise OSError("no such host")
        return [(2, 1, 6, "", (ip, port)) for ip in table[host]]

    return resolve


def test_proxy_policy() -> None:
    from app.runner_agent.proxy import EgressProxy, Policy, parse_blocked

    table = {
        "pypi.org": ["151.101.0.223"],
        "files.pythonhosted.org": ["151.101.64.223"],
        "evil.example.com": ["10.0.0.8"],
        "mixed.example.com": ["151.101.0.1", "192.168.1.5"],
        "metadata.example.com": ["169.254.169.254"],
        "workspace.example.com": ["203.0.113.10"],
        "v6.example.com": ["::ffff:127.0.0.1"],
    }
    proxy = EgressProxy(parse_blocked("203.0.113.10"), resolver=_resolver(table))
    proxy.set_policy("a", Policy("allowlist", ["pypi.org", "*.pythonhosted.org"]))
    proxy.set_policy("b", Policy("all"))
    proxy.set_policy("c", Policy("none"))
    assert proxy.check("a", "pypi.org", 443) == ("151.101.0.223", "")
    assert proxy.check("a", "files.pythonhosted.org", 443)[0] == "151.101.64.223"
    assert proxy.check("a", "example.com", 443)[0] is None
    assert proxy.check("a", "151.101.0.223", 443)[0] is None  # 名单模式不认 IP
    assert proxy.check("b", "example.com", 443)[0] is None  # 解析不了
    assert proxy.check("b", "pypi.org", 80)[0] == "151.101.0.223"
    for host in (
        "evil.example.com",
        "mixed.example.com",
        "metadata.example.com",
        "workspace.example.com",
        "v6.example.com",
        "127.0.0.1",
        "10.1.2.3",
    ):
        assert proxy.check("b", host, 443)[0] is None, host
    assert proxy.check("b", "pypi.org", 25)[0] is None  # 不转发 SMTP
    assert proxy.check("c", "pypi.org", 443)[0] is None
    assert proxy.check("unknown", "pypi.org", 443)[0] is None
    proxy.stats.add("a", "pypi.org", 443, True, 100)
    proxy.stats.add("a", "pypi.org", 443, True, 50)
    proxy.stats.add("a", "evil.example.com", 443, False, 0)
    drained = proxy.stats.drain()
    assert drained["a"][0] == {
        "host": "pypi.org",
        "port": 443,
        "allowed": True,
        "count": 2,
        "bytes": 150,
    }
    assert proxy.stats.drain() == {}


def test_proxy_serves_connect_and_http_on_the_env_listener() -> None:
    """真实套接字：本机起一个“外网”服务，代理只放行列表里的域名（解析到它）。"""
    import socket
    import threading

    from app.runner_agent import proxy as module
    from app.runner_agent.proxy import EgressProxy, Policy

    upstream = socket.socket()
    upstream.bind(("127.0.0.1", 0))
    upstream.listen(5)
    port = upstream.getsockname()[1]

    def serve() -> None:
        for _ in range(2):
            conn, _ = upstream.accept()
            data = conn.recv(65536)
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok" if data else b"")
            conn.close()

    threading.Thread(target=serve, daemon=True).start()
    original = module.ip_allowed
    module.ip_allowed = lambda value, blocked: value == "127.0.0.1"
    try:
        proxy = EgressProxy([], port=0, resolver=_resolver({"api.example.com": ["127.0.0.1"]}))
        proxy.port = 0
        proxy.listen("e1", "127.0.0.1", Policy("allowlist", ["api.example.com"]))
        server = proxy._listeners["e1"][0]
        address = server.getsockname()
        with socket.create_connection(address, timeout=5) as client:
            client.sendall(f"CONNECT api.example.com:{port} HTTP/1.1\r\n\r\n".encode())
            assert client.recv(1024).startswith(b"HTTP/1.1 200")
            client.sendall(b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")
            assert b"ok" in client.recv(1024)
        with socket.create_connection(address, timeout=5) as client:
            client.sendall(
                f"GET http://api.example.com:{port}/x HTTP/1.1\r\nHost: a\r\n\r\n".encode()
            )
            assert b"200 OK" in client.recv(1024)
        with socket.create_connection(address, timeout=5) as client:
            client.sendall(b"CONNECT other.example.com:443 HTTP/1.1\r\n\r\n")
            assert client.recv(1024).startswith(b"HTTP/1.1 403")
        proxy.close("e1")
    finally:
        module.ip_allowed = original
        upstream.close()


# ---------------------------------------------------------------- 模板与镜像


def test_dockerfile_and_registry_checks() -> None:
    import pytest

    from app.runner_agent.agent import CommandError, check_image, dockerfile_of, registry_host

    assert registry_host("python:3.12") is None
    assert registry_host("ghcr.io/org/img:1") == "ghcr.io"
    assert registry_host("localhost:5000/img") == "localhost"
    assert registry_host("10.0.0.5:5000/img") == "10.0.0.5"
    resolver = _resolver({"ghcr.io": ["140.82.112.33"], "localhost": ["127.0.0.1"]})
    check_image("python:3.12", resolver)
    check_image("ghcr.io/org/img:1", resolver)
    with pytest.raises(CommandError):
        check_image("localhost:5000/img", resolver)
    text = dockerfile_of("python:3.12", ["RUN pip install pytest\nENV A=1", "WORKDIR /app"])
    assert text.splitlines() == [
        "FROM python:3.12",
        "RUN pip install pytest",
        "ENV A=1",
        "WORKDIR /app",
    ]
    with pytest.raises(CommandError):
        dockerfile_of("python:3.12", ["ADD https://example.com/x.tgz /x"])


def test_tar_of_writes_parent_directories_with_the_same_owner() -> None:
    data = tar_of({"a/b/c.txt": b"1", "a/d.txt": b"2"}, uid=1000, gid=1000)
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        members = {m.name: m for m in tar.getmembers()}
    assert members["a"].isdir() and members["a/b"].isdir()
    assert all((m.uid, m.gid) == (1000, 1000) for m in members.values())


def _bundle_compose(**extra: Any) -> str:
    import zipfile

    from app import runner_bundle

    data = runner_bundle.build(
        runner_bundle.RunnerBundleInput(
            host_name="runner-1",
            url="https://agent-gateway.myworkspace.internal:9443",
            token="mwr_x",  # noqa: S106 - 测试值
            block=[],
            **extra,
        )
    )
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return zf.read("myworkspace-runner/compose.yaml").decode()


def test_bundle_maps_ingress_host_without_dns() -> None:
    """外网入口没有域名（内部主机名 + 工作台地址，T-0088）：容器里把主机名指向工作台。"""
    mapped = _bundle_compose(address="203.0.113.7")
    assert 'extra_hosts: ["agent-gateway.myworkspace.internal:203.0.113.7"]' in mapped
    plain = _bundle_compose()
    assert "extra_hosts" not in plain and "@EXTRA_HOSTS@" not in plain
    assert "@EXTRA_HOSTS@" not in mapped


def test_proxy_address_is_fixed_inside_the_env_network() -> None:
    """gVisor 访问不到 Docker 内置 DNS：环境按 IP 找出口代理，地址取网段最后一个可用地址。"""
    from app.runner_agent import containers as c

    assert c.proxy_address("172.24.0.0/16") == "172.24.255.254"
    assert c.proxy_address("192.168.48.0/20") == "192.168.63.254"
    for bad in ("fd00::/64", "10.0.0.0/30"):
        try:
            c.proxy_address(bad)
        except c.ParamError:
            continue
        raise AssertionError(bad)


def test_container_config_points_the_env_at_the_proxy_ip() -> None:
    from app.runner_agent import containers as c

    payload = {
        "env_id": "e1",
        "workspace_id": "w1",
        "container": "mw-env-0123456789ab",
        "volume": "mw-vol-0123456789ab",
        "network": "mw-net-0123456789ab",
        "image": "python:3.12-slim",
        "runtime": "gvisor",
        "user": "user",
        "network_mode": "allowlist",
        "domains": ["pypi.org"],
        "memory_mb": 256,
        "cpus": 1,
        "pids": 256,
        "env": {"HTTPS_PROXY": "http://evil:1"},
    }
    config = c.container_config(payload, proxy="172.24.255.254")
    assert "HTTPS_PROXY=http://172.24.255.254:3128" in config["Env"]
    assert "https_proxy=http://172.24.255.254:3128" in config["Env"]
    assert not any("evil" in item for item in config["Env"])  # 智能体给的代理变量不采用
    assert config["HostConfig"]["ExtraHosts"] == ["egress:172.24.255.254"]
    # 旧网络（自动网段）：仍按别名找代理，不写 hosts
    legacy = c.container_config(payload)
    assert (
        "HTTPS_PROXY=http://egress:3128" in legacy["Env"]
        and "ExtraHosts" not in legacy["HostConfig"]
    )
