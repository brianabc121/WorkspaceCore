"""全栈验收脚本共用的工具：经 Caddy 对外入口发请求的“浏览器”、结果汇总、docker compose exec。

只依赖 Python 标准库；被同目录的各 Sprint 验收脚本导入。
"""

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]


class Response:
    def __init__(self, status: int, body: bytes, headers: Any) -> None:
        self.status = status
        self.text = body.decode("utf-8", errors="replace")
        self.headers = headers

    def json(self) -> Any:
        return json.loads(self.text) if self.text else None

    @property
    def code(self) -> str | None:
        try:
            return str(self.json()["error"]["code"])
        except (ValueError, TypeError, KeyError):
            return None

    @property
    def summary(self) -> str:
        """状态码加错误码（有的话），用作检查结果的说明。"""
        return f"{self.status} {self.code}" if self.code else str(self.status)


class Client:
    """一个“浏览器”：自己的 Cookie，写请求自动带 Origin 和 X-CSRF-Token。"""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url
        self.origin = "{0.scheme}://{0.netloc}".format(urlsplit(base_url))
        self.jar = CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def cookie(self, name: str) -> str | None:
        return next((c.value for c in self.jar if c.name == name), None)

    def request(
        self,
        method: str,
        path: str,
        body: Any = None,
        *,
        headers: dict[str, str] | None = None,
        csrf: bool = True,
    ) -> Response:
        all_headers = {"Accept": "application/json", **(headers or {})}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            all_headers["Content-Type"] = "application/json"
        if method != "GET":
            all_headers.setdefault("Origin", self.origin)
            token = self.cookie("ws_csrf")
            if csrf and token:
                all_headers.setdefault("X-CSRF-Token", token)
        # base_url 已由 parse_base_url() 限定为 http(s)
        req = urllib.request.Request(  # noqa: S310
            self.base_url + path, data=data, method=method, headers=all_headers
        )
        try:
            with self.opener.open(req, timeout=15) as resp:
                return Response(resp.status, resp.read(), resp.headers)
        except urllib.error.HTTPError as error:
            return Response(error.code, error.read(), error.headers)


class Report:
    def __init__(self) -> None:
        self.failures = 0

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        print(f"{'通过' if ok else '失败'}  {name}" + (f"（{detail}）" if detail else ""))
        self.failures += 0 if ok else 1
        return ok

    def finish(self) -> int:
        print(f"\n{'全部通过' if self.failures == 0 else f'{self.failures} 项失败'}")
        return 0 if self.failures == 0 else 1


def default_base_url() -> str:
    return f"http://127.0.0.1:{os.environ.get('HTTP_PORT', '8080')}"


def parse_base_url(value: str) -> str:
    base = value.rstrip("/")
    if urlsplit(base).scheme not in {"http", "https"}:
        sys.exit("--base-url 必须是 http:// 或 https:// 地址")
    return base


def env_value(name: str, default: str | None = None) -> str | None:
    """先取环境变量，再取仓库根目录 .env（make env 生成）里的值。"""
    if os.environ.get(name):
        return os.environ[name]
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{name}="):
                return line.split("=", 1)[1].strip()
    return default


def setup_token() -> str:
    token = env_value("SETUP_TOKEN")
    if not token:
        sys.exit("缺少安装凭据：请设置 SETUP_TOKEN 或先运行 make env")
    return token


def compose_exec(service: str, *args: str, env: dict[str, str] | None = None) -> str:
    command = ["docker", "compose", "exec", "-T"]
    for key, value in (env or {}).items():
        command += ["-e", f"{key}={value}"]
    # 固定的 docker compose 命令，参数来自调用脚本自身
    result = subprocess.run(  # noqa: S603
        [*command, service, *args], cwd=ROOT, capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise RuntimeError(f"docker compose exec {service} 失败：{result.stderr.strip()[-500:]}")
    return result.stdout
