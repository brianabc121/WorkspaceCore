"""runner-agent 访问工作台的接入入口（`/runner-api/v1/*`）。

HTTPS（信任系统 CA 加上接入包里的工作台内部 CA）；外网入口另带客户端证书。
每个请求都有超时；错误分成认证失败（401，等管理员重新签发）、对方暂时不可用（可重试）与其他。
"""

from __future__ import annotations

import http.client
import json
import ssl
import urllib.parse
from typing import Any

from . import VERSION


class WorkspaceError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(f"{status} {code} {message}")
        self.status = status
        self.code = code
        self.message = message

    @property
    def retryable(self) -> bool:
        return self.status == 0 or self.status == 429 or self.status >= 500


class Workspace:
    def __init__(
        self,
        url: str,
        token: str,
        *,
        ca_file: str | None = None,
        cert_file: str | None = None,
        key_file: str | None = None,
    ) -> None:
        parts = urllib.parse.urlsplit(url)
        if parts.scheme not in ("https", "http") or not parts.hostname:
            raise ValueError("工作台地址要是 https://主机[:端口]")
        self.scheme = parts.scheme
        self.host = parts.hostname
        self.port = parts.port or (443 if parts.scheme == "https" else 80)
        self.base = parts.path.rstrip("/")
        self.token = token
        self.context: ssl.SSLContext | None = None
        if parts.scheme == "https":
            context = ssl.create_default_context()
            if ca_file:
                context.load_verify_locations(cafile=ca_file)
            if cert_file and key_file:
                context.load_cert_chain(cert_file, key_file)
            self.context = context

    def _connection(self, timeout: float) -> http.client.HTTPConnection:
        if self.context is not None:
            return http.client.HTTPSConnection(
                self.host, self.port, context=self.context, timeout=timeout
            )
        return http.client.HTTPConnection(self.host, self.port, timeout=timeout)

    def post(self, path: str, body: dict[str, Any], *, timeout: float = 30) -> Any:
        data = json.dumps(body, separators=(",", ":")).encode()
        conn = self._connection(timeout)
        try:
            conn.request(
                "POST",
                f"{self.base}/runner-api/v1{path}",
                body=data,
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Content-Type": "application/json",
                    "User-Agent": f"myworkspace-runner-agent/{VERSION}",
                },
            )
            response = conn.getresponse()
            raw = response.read()
        except (OSError, http.client.HTTPException) as error:
            raise WorkspaceError(0, "NETWORK", str(error)[:300]) from None
        finally:
            conn.close()
        try:
            parsed = json.loads(raw) if raw else {}
        except ValueError:
            parsed = {}
        if response.status >= 400:
            detail: dict[str, Any] = {}
            if isinstance(parsed, dict) and isinstance(parsed.get("error"), dict):
                detail = parsed["error"]
            code = str(detail.get("code") or "HTTP_ERROR")
            message = str(detail.get("message") or raw[:200])
            raise WorkspaceError(response.status, code, message)
        return parsed


__all__ = ["Workspace", "WorkspaceError"]
