"""通用响应结构：统一错误格式与分页列表（docs/DEVELOPMENT_SPEC.md 第 6 节）。"""

from typing import Any

from pydantic import BaseModel, Field


class ErrorBody(BaseModel):
    code: str = Field(description="机器可读的错误码，见 Sprint 1 接口契约 ADR 第 7 节")
    message: str = Field(description="给人看的说明，不包含敏感信息")
    request_id: str | None = Field(description="与响应头 X-Request-ID 相同")
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    error: ErrorBody


class Page[ItemT](BaseModel):
    """列表统一返回 items、next_cursor、total_estimate；未知总数时 total_estimate 为 null。"""

    items: list[ItemT]
    next_cursor: str | None = Field(default=None, description="下一页游标；没有更多数据时为 null")
    total_estimate: int | None = None


_ERROR_DESCRIPTIONS = {
    400: "请求无效",
    401: "未登录或会话已失效（UNAUTHORIZED / INVALID_CREDENTIALS）",
    403: "无权限，或 CSRF / 来源校验失败（FORBIDDEN / CSRF_FAILED / SETUP_TOKEN_INVALID）",
    404: "资源不存在，或不属于你可访问的工作空间（NOT_FOUND）",
    409: "状态冲突（例如 SETUP_COMPLETED）",
    422: "参数校验失败（VALIDATION_FAILED）",
    429: "请求过于频繁（RATE_LIMITED，带 Retry-After 响应头）",
    501: "接口已声明、尚未实现（NOT_IMPLEMENTED）",
}


def error_responses(*status_codes: int) -> dict[int | str, dict[str, Any]]:
    """在 OpenAPI 中声明错误响应，前端生成的类型会包含统一错误格式。"""
    return {
        code: {"model": ErrorResponse, "description": _ERROR_DESCRIPTIONS[code]}
        for code in status_codes
    }
