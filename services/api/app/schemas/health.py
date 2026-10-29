"""健康检查接口的响应结构（见 docs/decisions/2026-10-29-sprint-0-contracts.md 第 3 节）。"""

from typing import Literal

from pydantic import BaseModel

CheckStatus = Literal["ok", "error"]


class HealthResponse(BaseModel):
    status: Literal["ok"]


class ReadinessResponse(BaseModel):
    status: Literal["ok", "unavailable"]
    checks: dict[str, CheckStatus]
