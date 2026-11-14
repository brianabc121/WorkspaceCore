from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from bridge.app import create_app
from bridge.config import Settings
from bridge.drivers.fake import FakeDriver
from bridge.engine import Engine

ADMIN = "admin-token-for-tests-0123456789abcdef"
_MCP_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "MCP-Protocol-Version": "2025-06-18",
}


class Bridge:
    """测试里的一座桥：HTTP 客户端加上直接访问 engine 的入口。"""

    def __init__(self, client: httpx.AsyncClient, engine: Engine) -> None:
        self.client = client
        self.engine = engine
        self._id = 0

    @property
    def fake(self) -> FakeDriver:
        driver = self.engine.drivers["fake"]
        assert isinstance(driver, FakeDriver)
        return driver

    def headers(self, caller: str, scopes: str = "write") -> dict[str, str]:
        return {
            "Authorization": f"Bearer {ADMIN}",
            "X-Bridge-Caller": caller,
            "X-Bridge-Caller-Label": caller,
            "X-Bridge-Scopes": scopes,
        }

    async def rpc(
        self, method: str, params: dict[str, Any], headers: dict[str, str]
    ) -> httpx.Response:
        self._id += 1
        body = {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params}
        return await self.client.post("/mcp", json=body, headers={**_MCP_HEADERS, **headers})

    async def tool(
        self,
        caller: str,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        scopes: str = "write",
    ) -> dict[str, Any]:
        """调用工具，返回结果；工具报错时返回 {"error": {...}}。"""
        response = await self.rpc(
            "tools/call", {"name": name, "arguments": arguments or {}}, self.headers(caller, scopes)
        )
        assert response.status_code == 200, response.text
        result = response.json()["result"]
        payload: dict[str, Any] = json.loads(result["content"][0]["text"])
        assert bool(result.get("isError")) == ("error" in payload)
        return payload

    async def admin(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        return await self.client.request(
            method, path, headers={"Authorization": f"Bearer {ADMIN}"}, **kwargs
        )


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / "data", admin_token=ADMIN, enable_fake=True)


@pytest.fixture
async def bridge(settings: Settings) -> AsyncIterator[Bridge]:
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://bridge") as client:
            # 测试里不等“两次开始之间的间隔”（默认 2 秒）；专门测它的用例自己设
            app.state.engine.store.set_setting("min_start_interval_seconds", 0)
            yield Bridge(client, app.state.engine)
