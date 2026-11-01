"""请求上下文中间件：分配 request_id、记录访问日志与请求指标、兜底未处理异常。"""

import logging
import re
import time
from collections.abc import Awaitable, Callable
from uuid import uuid4

from fastapi import FastAPI, Request, Response

from app.core.errors import error_response
from app.core.logging import request_id_var
from app.core.metrics import EXCLUDED_PATHS, UNMATCHED_ROUTE, HttpMetrics

REQUEST_ID_HEADER = "X-Request-ID"
_VALID_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")

logger = logging.getLogger("app.request")

# 容器健康检查每隔几秒请求一次，访问日志降为 DEBUG，避免淹没正常请求；失败（非 2xx）仍按 INFO 记录
_PROBE_PATHS = frozenset({"/healthz", "/readyz"})

# 路径里本身就是凭证的参数：访问日志里换成 ***（规范第 8 节第 6 条）。
# OpenIM 回调地址的口令；实例取图的能力地址（拿到 media_id 就能经接入入口取图）
_SECRET_PATH_PARAMS: dict[str, frozenset[str]] = {
    "/internal/openim/{path_token}/{command}": frozenset({"{path_token}"}),
    "/im/obj/m/{media_id}": frozenset({"{media_id}"}),
}
REDACTED = "***"


async def request_context(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    incoming = request.headers.get(REQUEST_ID_HEADER, "")
    request_id = incoming if _VALID_REQUEST_ID.fullmatch(incoming) else f"req_{uuid4().hex}"
    token = request_id_var.set(request_id)
    started = time.perf_counter()
    try:
        try:
            response = await call_next(request)
        except Exception:
            # 在这里而不是在最外层处理，响应和日志才能带上 request_id
            logger.exception("unhandled error")
            response = error_response(500, "INTERNAL_ERROR", "服务器内部错误")
        response.headers[REQUEST_ID_HEADER] = request_id
        elapsed = time.perf_counter() - started
        path = request.url.path
        route = _route_template(request)
        metrics: HttpMetrics | None = getattr(request.app.state, "http_metrics", None)
        if metrics is not None and path not in EXCLUDED_PATHS:
            metrics.observe(request.method, route, response.status_code, elapsed)
        quiet = path in _PROBE_PATHS and response.status_code < 300
        # 不记录查询参数（可能包含敏感信息）；LOG_REQUEST_PATH=route 时连路径参数也不记录（S5-05）
        if request.app.state.settings.log_request_path == "route":
            logged_path = route
        else:
            logged_path = _redact_path(path, route)
        logger.log(
            logging.DEBUG if quiet else logging.INFO,
            "request",
            extra={
                "method": request.method,
                "path": logged_path,
                "status": response.status_code,
                "duration_ms": round(elapsed * 1000, 2),
            },
        )
        return response
    finally:
        request_id_var.reset(token)


def _route_template(request: Request) -> str:
    """完整的路由模板（例如 /api/v1/tasks/{task_id}）；路由匹配发生在 call_next 里，之后才能读到。

    FastAPI 放进 scope 的是 include 之前的路由（模板里没有 /api/v1 这样的前缀），
    所以用实际路径开头的那几段补上前缀：前缀来自 include_router 的固定 prefix，不含用户输入。
    """
    template = getattr(request.scope.get("route"), "path", None)
    if not isinstance(template, str):
        return UNMATCHED_ROUTE
    own = template.strip("/").split("/") if template.strip("/") else []
    actual = request.scope.get("path", "").strip("/").split("/")
    prefix = actual[: len(actual) - len(own)] if len(actual) > len(own) else []
    return "/" + "/".join([*prefix, *own]) if prefix or own else "/"


def _redact_path(path: str, route: str) -> str:
    """把路径里属于凭证的段换成 ***；路由模板与实际路径按段一一对应（见 _route_template）。"""
    secret = _SECRET_PATH_PARAMS.get(route)
    if not secret:
        return path
    actual = path.split("/")
    template = route.split("/")
    if len(actual) != len(template):
        return route
    return "/".join(REDACTED if t in secret else a for a, t in zip(actual, template, strict=True))


def install_middleware(app: FastAPI) -> None:
    app.middleware("http")(request_context)
