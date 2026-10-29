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


class ApiError(Exception):
    """业务代码抛出的错误，由处理器转换成统一错误格式。

    错误码清单见 docs/decisions/2026-10-30-sprint-1-auth-contract.md 第 7 节。
    """

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = dict(details or {})
        self.headers = dict(headers or {})


def not_implemented(task: str) -> ApiError:
    """契约已声明、尚未实现的端点（Sprint 1 期间由对应任务补上）。"""
    return ApiError(501, "NOT_IMPLEMENTED", f"接口尚未实现（{task}）")


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


async def _api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    return error_response(exc.status_code, exc.code, exc.message, exc.details, exc.headers)


def install_error_handlers(app: FastAPI) -> None:
    app.exception_handler(ApiError)(_api_error_handler)
    app.exception_handler(StarletteHTTPException)(_http_exception_handler)
    app.exception_handler(RequestValidationError)(_validation_exception_handler)
