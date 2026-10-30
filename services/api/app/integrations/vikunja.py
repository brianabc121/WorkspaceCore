"""Vikunja 2.7.1 的 API v2 客户端（docs/decisions/2026-10-30-vikunja-2.7.1-verification.md）。

只用 API Token（Bearer）与 v2 端点；描述以 Markdown 读写（`format=markdown`）。
外部响应按核验 ADR 第 7 节归类成 ConnectorError；422 时只保留错误位置与原因，
丢弃 Vikunja 回显的输入值。端点与字段以 infra/vikunja/openapi/vikunja-2.7.1-api-v2.json 为准。
"""

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

import httpx

from app import __version__
from app.integrations.base import (
    ConnectorError,
    InstanceInfo,
    Readable,
    WebhookCapable,
    Writable,
)
from app.integrations.http import ConnectorHttp

logger = logging.getLogger("app.integrations.vikunja")

PROVIDER = "vikunja"
PAGE_SIZE = 50  # 实例的 max_items_per_page 默认值；超过会被静默截断
MAX_PAGES = 400  # 防御性上限：一次全量同步最多读这么多页
# Vikunja 用这个零值表示“没有设置日期”（核验 ADR 第 5.1 节）
_ZERO_DATE = "0001-01-01T00:00:00Z"
_MARKDOWN = {"format": "markdown"}


@dataclass(frozen=True)
class VikunjaProject:
    id: int
    title: str
    archived: bool
    updated: datetime | None


@dataclass(frozen=True)
class VikunjaTask:
    id: int
    project_id: int
    identifier: str | None
    title: str
    description: str
    done: bool
    done_at: datetime | None
    priority: int
    due_at: datetime | None
    labels: tuple[str, ...]
    created: datetime | None
    updated: datetime
    # 乐观并发的 version：updated 截到秒的 UTC 时间。Vikunja 写入响应里的时间带纳秒，
    # 随后读取时只到秒（核验 ADR 第 5.5 节），直接用原始字符串会导致每次修改都报冲突
    version: str


def _datetime(value: Any) -> datetime | None:
    """解析 Vikunja 的时间：零值为 None；统一成 UTC 并截到秒（Vikunja 只按秒保存）。"""
    if not isinstance(value, str) or not value or value.startswith("0001-01-01"):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    aware = parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return aware.astimezone(UTC).replace(microsecond=0)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _invalid() -> ConnectorError:
    return ConnectorError("UNAVAILABLE", "服务返回的数据格式不符合预期", reason="invalid_response")


def parse_project(data: Any) -> VikunjaProject:
    if not isinstance(data, dict) or not isinstance(data.get("id"), int):
        raise _invalid()
    return VikunjaProject(
        id=data["id"],
        title=str(data.get("title") or ""),
        archived=bool(data.get("is_archived")),
        updated=_datetime(data.get("updated")),
    )


def parse_task(data: Any) -> VikunjaTask:
    if (
        not isinstance(data, dict)
        or not isinstance(data.get("id"), int)
        or not isinstance(data.get("project_id"), int)
        or not isinstance(data.get("title"), str)
        or not isinstance(data.get("updated"), str)
    ):
        raise _invalid()
    updated = _datetime(data["updated"])
    if updated is None:
        raise _invalid()
    priority = data.get("priority")
    labels = data.get("labels") or []
    return VikunjaTask(
        id=data["id"],
        project_id=data["project_id"],
        identifier=data.get("identifier") or None,
        title=data["title"],
        description=str(data.get("description") or ""),
        done=bool(data.get("done")),
        done_at=_datetime(data.get("done_at")),
        # API 不校验范围（核验 ADR 第 5.1 节）；越界的值按“未设置”显示
        priority=priority if isinstance(priority, int) and 0 <= priority <= 5 else 0,
        due_at=_datetime(data.get("due_date")),
        labels=tuple(
            str(label["title"])
            for label in labels
            if isinstance(label, dict) and isinstance(label.get("title"), str)
        ),
        created=_datetime(data.get("created")),
        updated=updated,
        version=_iso(updated),
    )


@dataclass(frozen=True)
class VikunjaWebhook:
    id: int
    project_id: int
    target_url: str
    events: tuple[str, ...]


def parse_webhook(data: Any) -> VikunjaWebhook:
    if not isinstance(data, dict) or not isinstance(data.get("id"), int):
        raise _invalid()
    events = data.get("events") or []
    return VikunjaWebhook(
        id=data["id"],
        project_id=int(data.get("project_id") or 0),
        target_url=str(data.get("target_url") or ""),
        events=tuple(str(e) for e in events if isinstance(e, str)),
    )


def error_from_response(response: httpx.Response) -> ConnectorError:
    """按核验 ADR 第 7 节归类；不保存响应原文。"""
    status = response.status_code
    if status == 401:
        return ConnectorError("AUTH_FAILED", "Vikunja 拒绝了 Token", status=status)
    if status == 403:
        return ConnectorError(
            "AUTH_FAILED", "Vikunja 拒绝访问该资源", status=status, resource_level=True
        )
    if status == 404:
        return ConnectorError("NOT_FOUND", "Vikunja 中不存在该资源", status=status)
    if status in {400, 422}:
        return ConnectorError(
            "VALIDATION_FAILED", "Vikunja 拒绝了请求内容", status=status, errors=_errors(response)
        )
    if status == 429:
        retry = response.headers.get("Retry-After", "")
        return ConnectorError(
            "RATE_LIMITED",
            "Vikunja 限流",
            status=status,
            retry_after=int(retry) if retry.isdigit() else None,
        )
    return ConnectorError("UNAVAILABLE", "Vikunja 暂时不可用", status=status, reason="http_error")


def _errors(response: httpx.Response) -> list[tuple[str, str]]:
    try:
        body = response.json()
    except ValueError:
        return []
    items = body.get("errors") if isinstance(body, dict) else None
    result = []
    for item in items or []:
        if isinstance(item, dict):
            # 只取位置与原因；item["value"] 是对方回显的输入，丢弃
            result.append((str(item.get("location") or ""), str(item.get("message") or "")))
    return result[:20]


class VikunjaClient:
    """实现 Readable、Writable 与 WebhookCapable（app/integrations/base.py）。"""

    provider = PROVIDER

    def __init__(self, http: ConnectorHttp, base_url: str, token: str) -> None:
        self._http = http
        self._api = f"{base_url.rstrip('/')}/api/v2"
        self._headers = {
            "Authorization": f"Bearer {token}",
            "User-Agent": f"my-workspace/{__version__}",
        }

    async def _call(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | list[tuple[str, Any]] | None = None,
        json: Any = None,
        headers: Mapping[str, str] | None = None,
        content_type: str | None = None,
    ) -> httpx.Response:
        response = await self._http.request(
            method,
            self._api + path,
            headers={**self._headers, **(headers or {})},
            params=params,
            json=json,
            content_type=content_type,
        )
        if response.status_code >= 400:
            raise error_from_response(response)
        return response

    async def _json(self, method: str, path: str, **kwargs: Any) -> Any:
        response = await self._call(method, path, **kwargs)
        try:
            return response.json()
        except ValueError as error:
            raise _invalid() from error

    # ---------------------------------------------------------------- 实例与权限

    async def info(self) -> InstanceInfo:
        data = await self._json("GET", "/info")
        version = data.get("version") if isinstance(data, dict) else None
        return InstanceInfo(version=str(version).removeprefix("v") if version else None)

    async def can_manage_webhooks(self, project_id: int) -> bool:
        """Token 是否有 projects_webhooks 权限：没有时 Vikunja 返回 401（核验 ADR 第 3 节）。"""
        try:
            await self._call("GET", f"/projects/{project_id}/webhooks")
        except ConnectorError as error:
            if error.kind == "AUTH_FAILED":
                return False
            raise
        return True

    # ---------------------------------------------------------------- 读取

    async def _pages(self, path: str, params: list[tuple[str, Any]]) -> list[Any]:
        items: list[Any] = []
        for page in range(1, MAX_PAGES + 1):
            data = await self._json(
                "GET", path, params=[*params, ("page", page), ("per_page", PAGE_SIZE)]
            )
            if not isinstance(data, dict) or not isinstance(data.get("items") or [], list):
                raise _invalid()
            items.extend(data.get("items") or [])
            total_pages = data.get("total_pages")
            if not isinstance(total_pages, int) or page >= total_pages:
                return items
        raise ConnectorError("UNAVAILABLE", "数据量超过同步上限", reason="too_many_pages")

    async def list_projects(self, *, include_archived: bool = True) -> list[VikunjaProject]:
        params = [("is_archived", "true")] if include_archived else []
        return [parse_project(item) for item in await self._pages("/projects", params)]

    async def first_project_page(self) -> list[VikunjaProject]:
        data = await self._json("GET", "/projects", params={"page": 1, "per_page": 1})
        if not isinstance(data, dict):
            raise _invalid()
        return [parse_project(item) for item in data.get("items") or []]

    async def first_task_page(self) -> list[VikunjaTask]:
        """测试连接：确认 Token 有任务读权限。"""
        data = await self._json("GET", "/tasks", params={"page": 1, "per_page": 1, **_MARKDOWN})
        if not isinstance(data, dict):
            raise _invalid()
        return [parse_task(item) for item in data.get("items") or []]

    async def list_tasks(self, *, updated_since: datetime | None = None) -> list[VikunjaTask]:
        """全部项目的任务（含已完成）；updated_since 用于增量同步（>=，调用方自行去重）。"""
        params: list[tuple[str, Any]] = [("sort_by", "id"), ("order_by", "asc")]
        if updated_since is not None:
            params.append(("filter", f"updated >= {_iso(updated_since)}"))
        params.extend(_MARKDOWN.items())
        return [parse_task(item) for item in await self._pages("/tasks", params)]

    async def get_task(self, task_id: int) -> VikunjaTask:
        return parse_task(await self._json("GET", f"/tasks/{task_id}", params=_MARKDOWN))

    async def get_project(self, project_id: int) -> VikunjaProject:
        return parse_project(await self._json("GET", f"/projects/{project_id}"))

    async def find_created_tasks(
        self, project_id: int, *, title: str, created_since: datetime
    ) -> list[VikunjaTask]:
        """结果未知的创建：在项目里找发出之后创建、标题相同的任务（核验 ADR 第 5.4 节）。"""
        params: list[tuple[str, Any]] = [
            ("filter", f"created >= {_iso(created_since)}"),
            *_MARKDOWN.items(),
        ]
        tasks = [
            parse_task(item) for item in await self._pages(f"/projects/{project_id}/tasks", params)
        ]
        return [task for task in tasks if task.title == title]

    # ---------------------------------------------------------------- 写入（从不自动重试）

    async def create_task(
        self,
        project_id: int,
        *,
        title: str,
        description: str,
        priority: int,
        due_at: datetime | None,
    ) -> VikunjaTask:
        body: dict[str, Any] = {"title": title, "description": description, "priority": priority}
        if due_at is not None:
            body["due_date"] = _iso(due_at)
        data = await self._json(
            "POST", f"/projects/{project_id}/tasks", params=_MARKDOWN, json=body
        )
        return parse_task(data)

    async def update_task(self, task_id: int, changes: Mapping[str, Any]) -> VikunjaTask:
        """merge-patch：只发送要改的字段；due_date 为 None 表示清除。"""
        body: dict[str, Any] = {}
        for key, value in changes.items():
            if key == "due_at":
                body["due_date"] = _iso(value) if value is not None else None
            else:
                body[key] = value
        data = await self._json(
            "PATCH",
            f"/tasks/{task_id}",
            json=body,
            # PATCH 的格式参数放在请求头（merge-patch 会忽略查询参数，核验 ADR 第 4 节）
            headers={"X-Vikunja-Format": "markdown"},
            content_type="application/merge-patch+json",
        )
        return parse_task(data)

    async def delete_task(self, task_id: int) -> None:
        await self._call("DELETE", f"/tasks/{task_id}")

    # ---------------------------------------------------------------- Webhook（核验 ADR 第 6 节）

    async def list_webhooks(self, project_id: int) -> list[VikunjaWebhook]:
        items = await self._pages(f"/projects/{project_id}/webhooks", [])
        return [parse_webhook(item) for item in items]

    async def create_webhook(
        self, project_id: int, *, target_url: str, events: Sequence[str], secret: str
    ) -> VikunjaWebhook:
        """secret 只写不读：Vikunja 用它对每次投递的请求体做 HMAC-SHA256。"""
        data = await self._json(
            "POST",
            f"/projects/{project_id}/webhooks",
            json={"target_url": target_url, "events": list(events), "secret": secret},
        )
        return parse_webhook(data)


def task_source_url(public_url: str, task_id: int) -> str:
    return f"{public_url.rstrip('/')}/tasks/{task_id}"


def project_source_url(public_url: str, project_id: int) -> str:
    return f"{public_url.rstrip('/')}/projects/{quote(str(project_id))}"


# 领域层按能力使用 Vikunja Connector；VikunjaClient 满足这些 Protocol 由测试里的类型检查保证
ProjectReader = Readable[VikunjaProject, VikunjaTask]
TaskConnector = Writable[VikunjaProject, VikunjaTask]
WebhookRegistrar = WebhookCapable[VikunjaWebhook]
