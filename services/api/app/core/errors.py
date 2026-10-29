"""统一错误格式（docs/DEVELOPMENT_SPEC.md 第 6 节）：

{"error": {"code": "...", "message": "...", "request_id": "req_...", "details": {}}}
"""

from collections.abc import Mapping
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import request_id_var

_CODES = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    422: "VALIDATION_FAILED",
    429: "RATE_LIMITED",
}


def error_response(
    status_code: int,
    code: str,
    message: str,
    details: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    body = {
        "error": {
            "code": code,
            "message": message,
            "request_id": request_id_var.get(),
            "details": dict(details or {}),
        }
    }
    return JSONResponse(status_code=status_code, content=body, headers=dict(headers or {}))


async def _http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    message = exc.detail if isinstance(exc.detail, str) else HTTPStatus(exc.status_code).phrase
    return error_response(
        exc.status_code,
        _CODES.get(exc.status_code, "HTTP_ERROR"),
        message,
        headers=exc.headers,
    )


async def _validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    # 只返回位置和原因，不回显用户输入（可能包含密码等敏感内容）
    errors = [
        {"loc": e.get("loc"), "msg": e.get("msg"), "type": e.get("type")} for e in exc.errors()
    ]
    return error_response(
        422, "VALIDATION_FAILED", "请求参数校验失败", details={"errors": jsonable_encoder(errors)}
    )


def install_error_handlers(app: FastAPI) -> None:
    app.exception_handler(StarletteHTTPException)(_http_exception_handler)
    app.exception_handler(RequestValidationError)(_validation_exception_handler)
