"""Docker Engine API 客户端：经 unix socket 发 HTTP（只用标准库）。

用到的接口与字段在锁定的 Docker Engine 29.9（API 1.56）上核验过，见
docs/decisions/2026-11-06-runner-verification.md。所有请求都有超时；拉镜像、构建、exec 这类长操作
用单独的超时。返回的错误统一成 DockerError（HTTP 状态码 + Docker 的 message）。
"""

from __future__ import annotations

import http.client
import io
import json
import socket
import struct
import tarfile
import time
import urllib.parse
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

DEFAULT_SOCKET = "/var/run/docker.sock"


class DockerError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"{status} {message}")
        self.status = status
        self.message = message


class _UnixConnection(http.client.HTTPConnection):
    def __init__(self, path: str, timeout: float) -> None:
        super().__init__("localhost", timeout=timeout)
        self._path = path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect(self._path)
        self.sock = sock


@dataclass
class ExecResult:
    exit_code: int | None  # None：超时或连接中断，结果未知
    output: bytes  # stdout 与 stderr 按到达顺序合在一起
    truncated: bool
    timed_out: bool
    seconds: float


def _url(path: str, query: dict[str, Any] | None) -> str:
    if not query:
        return path
    items = {k: (json.dumps(v) if isinstance(v, dict | list) else str(v)) for k, v in query.items()}
    return f"{path}?{urllib.parse.urlencode(items)}"


def _message(data: bytes) -> str:
    try:
        parsed = json.loads(data or b"{}")
    except ValueError:
        return data[:300].decode("utf-8", "replace")
    if isinstance(parsed, dict):
        return str(parsed.get("message") or parsed)[:500]
    return str(parsed)[:500]


def frames(stream: Iterator[bytes]) -> Iterator[tuple[int, bytes]]:
    """拆 Docker 的多路复用流：每帧 8 字节头（流类型、3 字节 0、4 字节大端长度）+ 内容。"""
    buffer = b""
    for chunk in stream:
        buffer += chunk
        while len(buffer) >= 8:
            kind = buffer[0]
            (size,) = struct.unpack(">I", buffer[4:8])
            if len(buffer) < 8 + size:
                break
            yield kind, buffer[8 : 8 + size]
            buffer = buffer[8 + size :]


def tar_of(files: dict[str, bytes], *, uid: int = 0, gid: int = 0, mode: int = 0o644) -> bytes:
    """把 {相对路径: 内容} 打成 tar（put_archive 用）。

    中间目录也写进去（属主同文件）：否则 Docker 解包时按 root 建目录，uid 1000 写不进去。
    """
    out = io.BytesIO()
    now = int(time.time())
    seen: set[str] = set()
    with tarfile.open(fileobj=out, mode="w") as tar:
        for name, content in files.items():
            parts = name.split("/")[:-1]
            for depth in range(1, len(parts) + 1):
                folder = "/".join(parts[:depth])
                if folder in seen:
                    continue
                seen.add(folder)
                entry = tarfile.TarInfo(folder)
                entry.type = tarfile.DIRTYPE
                entry.mode = 0o755
                entry.uid, entry.gid = uid, gid
                entry.mtime = now
                tar.addfile(entry)
            info = tarfile.TarInfo(name)
            info.size = len(content)
            info.mode = mode
            info.uid, info.gid = uid, gid
            info.mtime = now
            tar.addfile(info, io.BytesIO(content))
    return out.getvalue()


class Docker:
    def __init__(self, socket_path: str = DEFAULT_SOCKET, timeout: float = 60.0) -> None:
        self.socket_path = socket_path
        self.timeout = timeout

    # ------------------------------------------------------------------ 底层
    def _send(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, Any] | None = None,
        body: Any = None,
        raw: bytes | None = None,
        content_type: str = "application/json",
        timeout: float | None = None,
    ) -> tuple[_UnixConnection, http.client.HTTPResponse]:
        conn = _UnixConnection(self.socket_path, timeout or self.timeout)
        payload = raw if raw is not None else (None if body is None else json.dumps(body).encode())
        headers = {"Content-Type": content_type} if payload is not None else {}
        conn.request(method, _url(path, query), body=payload, headers=headers)
        return conn, conn.getresponse()

    def call(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, Any] | None = None,
        body: Any = None,
        raw: bytes | None = None,
        content_type: str = "application/json",
        timeout: float | None = None,
        ok: tuple[int, ...] = (200, 201, 204, 304),
    ) -> bytes:
        conn, response = self._send(
            method,
            path,
            query=query,
            body=body,
            raw=raw,
            content_type=content_type,
            timeout=timeout,
        )
        try:
            data = response.read()
        finally:
            conn.close()
        if response.status not in ok:
            raise DockerError(response.status, _message(data))
        return data

    def json(self, method: str, path: str, **kwargs: Any) -> Any:
        data = self.call(method, path, **kwargs)
        return json.loads(data) if data else None

    def _stream_json(self, method: str, path: str, **kwargs: Any) -> list[dict[str, Any]]:
        """拉镜像、构建：响应是一行一个 JSON 的进度流，出错时某一行带 error。"""
        conn, response = self._send(method, path, **kwargs)
        lines: list[dict[str, Any]] = []
        try:
            if response.status != 200:
                raise DockerError(response.status, _message(response.read()))
            buffer = b""
            while chunk := response.read(65536):
                buffer += chunk
                *complete, buffer = buffer.split(b"\n")
                for line in complete:
                    if line.strip():
                        lines.append(json.loads(line))
            if buffer.strip():
                lines.append(json.loads(buffer))
        finally:
            conn.close()
        for entry in lines:
            if entry.get("error") or entry.get("errorDetail"):
                detail = entry.get("errorDetail") or {}
                raise DockerError(500, str(entry.get("error") or detail.get("message"))[:500])
        return lines

    # ------------------------------------------------------------------ 系统
    def version(self) -> dict[str, Any]:
        result: dict[str, Any] = self.json("GET", "/version")
        return result

    def info(self) -> dict[str, Any]:
        result: dict[str, Any] = self.json("GET", "/info")
        return result

    def system_df(self) -> dict[str, Any]:
        result: dict[str, Any] = self.json("GET", "/system/df", timeout=120)
        return result

    # ------------------------------------------------------------------ 镜像
    def pull(self, image: str, *, timeout: float = 1800) -> None:
        self._stream_json("POST", "/images/create", query={"fromImage": image}, timeout=timeout)

    def image(self, ref: str) -> dict[str, Any] | None:
        try:
            quoted = urllib.parse.quote(ref, safe="")
            result: dict[str, Any] = self.json("GET", f"/images/{quoted}/json")
        except DockerError as error:
            if error.status == 404:
                return None
            raise
        return result

    def build(
        self,
        dockerfile: str,
        tag: str,
        labels: dict[str, str],
        *,
        network: str | None = None,
        build_args: dict[str, str] | None = None,
        timeout: float = 3600,
    ) -> list[dict[str, Any]]:
        """经典构建器（API 默认）：RUN 步骤的容器连在 network 上（实测支持自定义网络名）。"""
        context = tar_of({"Dockerfile": dockerfile.encode()})
        query: dict[str, Any] = {"t": tag, "labels": labels, "rm": 1, "forcerm": 1, "pull": 1}
        if network:
            query["networkmode"] = network
        if build_args:
            query["buildargs"] = build_args
        return self._stream_json(
            "POST",
            "/build",
            query=query,
            raw=context,
            content_type="application/x-tar",
            timeout=timeout,
        )

    def remove_image(self, ref: str) -> None:
        self.call("DELETE", f"/images/{urllib.parse.quote(ref, safe='')}", ok=(200, 404, 409))

    # ------------------------------------------------------------------ 网络与数据卷
    def create_network(self, name: str, labels: dict[str, str], *, internal: bool) -> str:
        created = self.json(
            "POST",
            "/networks/create",
            body={"Name": name, "Driver": "bridge", "Internal": internal, "Labels": labels},
        )
        return str(created["Id"])

    def connect_network(self, network: str, container: str, aliases: list[str]) -> None:
        self.call(
            "POST",
            f"/networks/{network}/connect",
            body={"Container": container, "EndpointConfig": {"Aliases": aliases}},
            ok=(200, 403),  # 403：已经连着
        )

    def disconnect_network(self, network: str, container: str) -> None:
        self.call(
            "POST",
            f"/networks/{network}/disconnect",
            body={"Container": container, "Force": True},
            ok=(200, 404),
        )

    def remove_network(self, name: str) -> None:
        self.call("DELETE", f"/networks/{name}", ok=(204, 404))

    def create_volume(self, name: str, labels: dict[str, str]) -> None:
        self.call("POST", "/volumes/create", body={"Name": name, "Labels": labels}, ok=(201, 200))

    def remove_volume(self, name: str) -> None:
        self.call("DELETE", f"/volumes/{name}", query={"force": 1}, ok=(204, 404))

    # ------------------------------------------------------------------ 容器
    def create_container(self, name: str, config: dict[str, Any]) -> str:
        created = self.json("POST", "/containers/create", query={"name": name}, body=config)
        return str(created["Id"])

    def start(self, container: str) -> None:
        self.call("POST", f"/containers/{container}/start", ok=(204, 304))

    def stop(self, container: str, *, seconds: int = 10) -> None:
        self.call(
            "POST",
            f"/containers/{container}/stop",
            query={"t": seconds},
            timeout=seconds + 30,
            ok=(204, 304, 404),
        )

    def remove(self, container: str) -> None:
        self.call("DELETE", f"/containers/{container}", query={"force": 1}, ok=(204, 404))

    def wait(self, container: str, *, timeout: float = 600) -> int:
        """等容器退出，返回退出码。"""
        result = self.json("POST", f"/containers/{container}/wait", timeout=timeout)
        return int(result.get("StatusCode", -1))

    def logs(self, container: str, *, limit: int = 2 * 1024 * 1024) -> bytes:
        """已退出的容器的标准输出（多路复用流，只取 stdout）。"""
        conn, response = self._send(
            "GET", f"/containers/{container}/logs", query={"stdout": 1, "stderr": 0}, timeout=120
        )
        out = bytearray()
        try:
            if response.status != 200:
                raise DockerError(response.status, _message(response.read()))

            def chunks() -> Iterator[bytes]:
                while chunk := response.read1(65536):
                    yield chunk

            for kind, data in frames(chunks()):
                if kind == 1:
                    out += data
                if len(out) > limit:
                    break
        finally:
            conn.close()
        return bytes(out[:limit])

    def all_containers(self) -> list[dict[str, Any]]:
        """不加过滤（只用来数一数没有工作台标签的容器，不对它们做任何操作）。"""
        result: list[dict[str, Any]] = self.json("GET", "/containers/json", query={"all": 1})
        return result

    def inspect(self, container: str) -> dict[str, Any] | None:
        try:
            result: dict[str, Any] = self.json("GET", f"/containers/{container}/json")
        except DockerError as error:
            if error.status == 404:
                return None
            raise
        return result

    def containers(self, label: str) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = self.json(
            "GET",
            "/containers/json",
            query={"all": 1, "size": 1, "filters": {"label": [label]}},
            timeout=120,
        )
        return result

    def volumes(self, label: str) -> list[dict[str, Any]]:
        result = self.json("GET", "/volumes", query={"filters": {"label": [label]}})
        return list(result.get("Volumes") or [])

    def networks(self, label: str) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = self.json(
            "GET", "/networks", query={"filters": {"label": [label]}}
        )
        return result

    def stats(self, container: str) -> dict[str, Any]:
        result: dict[str, Any] = self.json(
            "GET", f"/containers/{container}/stats", query={"stream": 0, "one-shot": 1}
        )
        return result

    # ------------------------------------------------------------------ 文件
    def get_archive(self, container: str, path: str, *, limit: int) -> bytes:
        conn, response = self._send(
            "GET", f"/containers/{container}/archive", query={"path": path}, timeout=300
        )
        try:
            if response.status != 200:
                raise DockerError(response.status, _message(response.read()))
            data = response.read(limit + 1)
        finally:
            conn.close()
        if len(data) > limit:
            raise DockerError(413, f"超过 {limit} 字节")
        return data

    def put_archive(self, container: str, path: str, tar: bytes) -> None:
        self.call(
            "PUT",
            f"/containers/{container}/archive",
            query={"path": path},
            raw=tar,
            content_type="application/x-tar",
            timeout=300,
        )

    # ------------------------------------------------------------------ exec
    def exec(
        self,
        container: str,
        cmd: list[str],
        *,
        user: str | None = None,
        workdir: str | None = None,
        env: list[str] | None = None,
        timeout: float = 1800,
        limit: int = 1024 * 1024,
    ) -> ExecResult:
        """在运行中的容器里执行命令并等它结束。

        输出超过 limit 时保留开头四分之一和结尾（中间换成一行省略说明）；
        超时返回 exit_code=None（进程可能还在跑，由调用方停掉容器）。
        """
        body: dict[str, Any] = {"Cmd": cmd, "AttachStdout": True, "AttachStderr": True}
        if user:
            body["User"] = user
        if workdir:
            body["WorkingDir"] = workdir
        if env:
            body["Env"] = env
        created = self.json("POST", f"/containers/{container}/exec", body=body)
        exec_id = str(created["Id"])
        started = time.monotonic()
        output = bytearray()
        truncated = False
        timed_out = False
        # 读超时设成整个时限：一直没有输出的命令也只在超过时限时才算超时
        # （缓冲读在超时后的状态不可靠，所以不重试）
        conn, response = self._send(
            "POST",
            f"/exec/{exec_id}/start",
            body={"Detach": False, "Tty": False},
            timeout=timeout,
        )
        try:
            if response.status != 200:
                raise DockerError(response.status, _message(response.read()))

            def chunks() -> Iterator[bytes]:
                nonlocal timed_out
                while True:
                    if time.monotonic() - started > timeout:
                        timed_out = True
                        return
                    try:
                        chunk = response.read1(65536)
                    except OSError:  # 含超时
                        timed_out = True
                        return
                    if not chunk:
                        return
                    yield chunk

            head_limit = limit // 4
            tail = bytearray()
            for _kind, data in frames(chunks()):
                if len(output) < head_limit:
                    take = head_limit - len(output)
                    output += data[:take]
                    data = data[take:]
                if data:
                    tail += data
                    if len(tail) > limit - head_limit:
                        truncated = True
                        del tail[: len(tail) - (limit - head_limit)]
            if truncated:
                output += "\n...[中间省略]...\n".encode()
            output += tail
        finally:
            conn.close()
        seconds = time.monotonic() - started
        if timed_out:
            return ExecResult(None, bytes(output), truncated, True, seconds)
        detail = self.json("GET", f"/exec/{exec_id}/json")
        code = detail.get("ExitCode")
        exit_code = code if isinstance(code, int) else None
        return ExecResult(exit_code, bytes(output), truncated, False, seconds)
