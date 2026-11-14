"""页面上一键部署 MCP 管理器的请求 / 响应模型（docs/v2/LLM_BRIDGE_PLAN.md 第 20 节）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.modules.llm_bridge.schemas import Platform

_REQUEST_ID = r"^[\w.\-:]+$"


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LlmBridgeDeployIn(_In):
    server_id: UUID = Field(description="部署到哪台智能体服务器（要已经确认主机公钥并初始化）")
    name: str = Field(min_length=1, max_length=40, description="MCP 管理器的名字")
    platforms: list[Platform] = Field(min_length=1, description="它控制的平台")
    request_id: str = Field(
        min_length=1,
        max_length=80,
        pattern=_REQUEST_ID,
        description="幂等号：用同一个再提交一次，得到的是同一个任务",
    )


class LlmBridgeRedeployIn(_In):
    request_id: str = Field(min_length=1, max_length=80, pattern=_REQUEST_ID)


class LlmBridgeUninstallIn(_In):
    request_id: str = Field(min_length=1, max_length=80, pattern=_REQUEST_ID)
    remove_data: bool = Field(
        default=False, description="同时删除数据卷（登录状态和会话记录，不能恢复）"
    )


class LlmBridgeDeployJobOut(BaseModel):
    id: UUID
    kind: str = Field(description="deploy / redeploy / uninstall")
    status: str = Field(description="running / succeeded / failed / interrupted")
    server_id: UUID | None = Field(description="服务器已从工作台移除时为空")
    server_name: str
    bridge_id: UUID | None = Field(description="登记成功后是那个 MCP 管理器；登记被删除后为空")
    name: str
    platforms: list[str]
    mode: str | None = Field(
        description="same_host：和工作台同一台机器，加入内部网络；remote：端口开在私网地址上"
    )
    base_url: str | None
    steps: list[dict[str, Any]] = Field(description="每一步的 key、status、detail")
    log: str | None = Field(default=None, description="脱敏后的输出；只在读单个任务时给出")
    error_code: str | None
    error_message: str | None
    started_at: datetime
    finished_at: datetime | None


class LlmBridgeDeployJobList(BaseModel):
    jobs: list[LlmBridgeDeployJobOut]
    available: bool = Field(description="这个工作台带着 MCP 管理器的构建文件，可以一键部署")
