"""测试用的内存版 Vikunja 2.7.1（API v2），挂在 httpx.MockTransport 上。

只实现工作台用到的端点，行为按 docs/decisions/2026-10-30-vikunja-2.7.1-verification.md：
Bearer Token、分页字段、零值日期、merge-patch、删除后 404（code 4002）、
Token 无权限时 401（code 11）。
`fault` 可以让下一次匹配的请求失败：先处理再丢失响应（结果未知），或直接返回某个状态码。
"""

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

BASE_URL = "http://vikunja.test"
TOKEN = "tk_" + "a" * 40
ZERO = "0001-01-01T00:00:00Z"
_FILTER = re.compile(r"^(created|updated) >= (\S+)$")


def _stamp(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _json(status: int, body: Any) -> httpx.Response:
    return httpx.Response(status, json=body)


def _nanos(stamp: str) -> str:
    """真实实例的写入响应里，刚写入的时间带纳秒；保存后再读只到秒（核验 ADR 第 5.5 节）。"""
    return stamp.removesuffix("Z") + ".123456789Z" if stamp != ZERO else stamp


@dataclass
class Fault:
    method: str
    path: re.Pattern[str]
    # "lose_response"：照常处理，然后让客户端读超时（请求已生效但结果未知）
    # "timeout_before"：不处理，直接读超时（请求已发出、没有生效）
    # "connect"：连接失败（请求没有发出）
    # 整数：返回这个状态码
    action: str | int
    times: int = 1
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class FakeVikunja:
    token: str = TOKEN
    version: str = "v2.7.1"
    webhooks_allowed: bool = True
    projects: dict[int, dict[str, Any]] = field(default_factory=dict)
    tasks: dict[int, dict[str, Any]] = field(default_factory=dict)
    requests: list[httpx.Request] = field(default_factory=list)
    faults: list[Fault] = field(default_factory=list)
    _next_id: int = 100
    _clock: datetime = field(default_factory=lambda: datetime.now(UTC).replace(microsecond=0))

    # ---------------------------------------------------------------- 测试辅助

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def tick(self) -> str:
        """单调递增的时间戳（秒精度），保证每次修改的 updated 都不同。"""
        now = datetime.now(UTC).replace(microsecond=0)
        self._clock = max(now, self._clock + timedelta(seconds=1))
        return _stamp(self._clock)

    def add_project(self, title: str, *, archived: bool = False) -> dict[str, Any]:
        self._next_id += 1
        project: dict[str, Any] = {
            "id": self._next_id,
            "title": title,
            "is_archived": archived,
            "created": self.tick(),
            "updated": self.tick(),
        }
        self.projects[project["id"]] = project
        return project

    def add_task(self, project_id: int, title: str, **fields: Any) -> dict[str, Any]:
        self._next_id += 1
        stamp = self.tick()
        task: dict[str, Any] = {
            "id": self._next_id,
            "project_id": project_id,
            "identifier": f"#{self._next_id}",
            "title": title,
            "description": "",
            "done": False,
            "done_at": ZERO,
            "priority": 0,
            "due_date": ZERO,
            "labels": None,
            "created": stamp,
            "updated": stamp,
            "deleted_at": None,
        }
        task.update(fields)
        self.tasks[task["id"]] = task
        return task

    def fault(
        self,
        method: str,
        path: str,
        action: str | int,
        *,
        times: int = 1,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.faults.append(Fault(method, re.compile(path), action, times, headers or {}))

    def calls(self, method: str, path: str) -> list[httpx.Request]:
        pattern = re.compile(path)
        return [
            r
            for r in self.requests
            if r.method == method and pattern.fullmatch(r.url.path.removeprefix("/api/v2"))
        ]

    # ---------------------------------------------------------------- 路由

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path.removeprefix("/api/v2")
        fault = self._take_fault(request.method, path)
        if fault is not None:
            if fault.action == "connect":
                raise httpx.ConnectError("connection refused", request=request)
            if fault.action == "timeout_before":
                raise httpx.ReadTimeout("read timed out", request=request)
            if isinstance(fault.action, int):
                return httpx.Response(
                    fault.action, json={"code": 0, "message": "fault"}, headers=fault.headers
                )
        # /info 不需要登录（与真实实例一致）
        if path != "/info" and request.headers.get("Authorization") != f"Bearer {self.token}":
            response = _json(401, {"code": 11, "message": "invalid token"})
        else:
            response = self._route(request, path)
        if fault is not None and fault.action == "lose_response":
            raise httpx.ReadTimeout("response lost", request=request)
        return response

    def _take_fault(self, method: str, path: str) -> Fault | None:
        for fault in self.faults:
            if fault.method == method and fault.path.fullmatch(path) and fault.times > 0:
                fault.times -= 1
                return fault
        return None

    def _route(self, request: httpx.Request, path: str) -> httpx.Response:
        routes: list[tuple[str, str, Callable[..., httpx.Response]]] = [
            ("GET", r"/info", self._info),
            ("GET", r"/projects", self._list_projects),
            ("GET", r"/projects/(\d+)/webhooks", self._webhooks),
            ("GET", r"/projects/(\d+)/tasks", self._list_project_tasks),
            ("POST", r"/projects/(\d+)/tasks", self._create_task),
            ("GET", r"/tasks", self._list_tasks),
            ("GET", r"/tasks/(\d+)", self._get_task),
            ("PATCH", r"/tasks/(\d+)", self._update_task),
            ("DELETE", r"/tasks/(\d+)", self._delete_task),
        ]
        for method, pattern, handler in routes:
            match = re.fullmatch(pattern, path)
            if request.method == method and match:
                return handler(request, *(int(g) for g in match.groups()))
        return _json(404, {"code": 0, "message": "no route"})

    def _info(self, request: httpx.Request) -> httpx.Response:
        return _json(200, {"version": self.version, "max_items_per_page": 50})

    def _page(self, request: httpx.Request, items: list[dict[str, Any]]) -> httpx.Response:
        page = int(request.url.params.get("page", "1"))
        per_page = min(int(request.url.params.get("per_page", "50")), 50)
        total_pages = max(1, -(-len(items) // per_page))
        chunk = items[(page - 1) * per_page : page * per_page]
        return _json(
            200,
            {
                "items": chunk,
                "page": page,
                "per_page": per_page,
                "total": len(items),
                "total_pages": total_pages,
            },
        )

    def _list_projects(self, request: httpx.Request) -> httpx.Response:
        archived = request.url.params.get("is_archived") == "true"
        items = [p for p in self.projects.values() if archived or not p["is_archived"]]
        return self._page(request, sorted(items, key=lambda p: p["id"]))

    def _webhooks(self, request: httpx.Request, project_id: int) -> httpx.Response:
        if not self.webhooks_allowed:
            return _json(401, {"code": 11, "message": "missing permission"})
        return _json(200, [])

    def _filtered(
        self, request: httpx.Request, tasks: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        expression = request.url.params.get("filter")
        if expression:
            match = _FILTER.match(expression)
            if match is None:
                raise ValueError(f"fake does not support filter {expression!r}")
            name, since = match.groups()
            tasks = [t for t in tasks if t[name] >= since]
        return sorted(tasks, key=lambda t: t["id"])

    def _list_tasks(self, request: httpx.Request) -> httpx.Response:
        return self._page(request, self._filtered(request, list(self.tasks.values())))

    def _list_project_tasks(self, request: httpx.Request, project_id: int) -> httpx.Response:
        if project_id not in self.projects:
            return _json(404, {"code": 3001, "message": "project does not exist"})
        tasks = [t for t in self.tasks.values() if t["project_id"] == project_id]
        return self._page(request, self._filtered(request, tasks))

    def _get_task(self, request: httpx.Request, task_id: int) -> httpx.Response:
        task = self.tasks.get(task_id)
        if task is None:
            return _json(404, {"code": 4002, "message": "task does not exist"})
        return _json(200, task)

    def _create_task(self, request: httpx.Request, project_id: int) -> httpx.Response:
        if project_id not in self.projects:
            return _json(404, {"code": 3001, "message": "project does not exist"})
        body = json.loads(request.content)
        if not body.get("title"):
            return _json(
                422,
                {
                    "code": 2002,
                    "errors": [
                        {
                            "location": "body.title",
                            "message": "too short",
                            "value": body.get("title"),
                        }
                    ],
                },
            )
        task = self.add_task(
            project_id,
            body["title"],
            description=body.get("description", ""),
            priority=body.get("priority", 0),
            due_date=body.get("due_date") or ZERO,
        )
        return _json(
            201, {**task, "created": _nanos(task["created"]), "updated": _nanos(task["updated"])}
        )

    def _update_task(self, request: httpx.Request, task_id: int) -> httpx.Response:
        task = self.tasks.get(task_id)
        if task is None:
            return _json(404, {"code": 4002, "message": "task does not exist"})
        if request.headers.get("Content-Type") != "application/merge-patch+json":
            return _json(415, {"code": 0, "message": "unsupported media type"})
        body = json.loads(request.content)
        for key, value in body.items():
            if key in {"due_date", "done_at"} and value is None:
                value = ZERO
            task[key] = value
        if "done" in body:
            task["done_at"] = self.tick() if body["done"] else ZERO
        task["updated"] = self.tick()
        return _json(200, {**task, "done_at": _nanos(task["done_at"])})

    def _delete_task(self, request: httpx.Request, task_id: int) -> httpx.Response:
        if self.tasks.pop(task_id, None) is None:
            return _json(404, {"code": 4002, "message": "task does not exist"})
        return httpx.Response(204)
