"""请求上下文中间件：分配 request_id、记录访问日志、兜底未处理异常。"""

import logging
import re
import time
from collections.abc import Awaitable, Callable
from uuid import uuid4

from fastapi import FastAPI, Request, Response

from app.core.errors import error_response
from app.core.logging import request_id_var

REQUEST_ID_HEADER = "X-Request-ID"
_VALID_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")

logger = logging.getLogger("app.request")

# 容器健康检查每隔几秒请求一次，访问日志降为 DEBUG，避免淹没正常请求；失败（非 2xx）仍按 INFO 记录
_PROBE_PATHS = frozenset({"/healthz", "/readyz"})


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
        path = request.url.path
        quiet = path in _PROBE_PATHS and response.status_code < 300
        # 只记录路径，不记录查询参数（可能包含敏感信息）
        logger.log(
            logging.DEBUG if quiet else logging.INFO,
            "request",
            extra={
                "method": request.method,
                "path": path,
                "status": response.status_code,
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            },
        )
        return response
    finally:
        request_id_var.reset(token)


def install_middleware(app: FastAPI) -> None:
    app.middleware("http")(request_context)
