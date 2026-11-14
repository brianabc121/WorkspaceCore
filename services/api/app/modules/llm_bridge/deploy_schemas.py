"""一键部署 MCP 管理器的请求 / 响应模型（docs/v2/LLM_BRIDGE_DEPLOY_PLAN.md 第 6 节）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.modules.llm_bridge.schemas import LlmBridgeInstallOut, Platform

_REQUEST_ID = r"^[\w.\-:]+$"
TargetKind = Literal["agent_server", "runner_host"]
_SUDO = "登录账号用 Docker 要 sudo 密码时填：只用这一次，不保存、不进日志"


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(
        min_length=1,
        max_length=80,
        pattern=_REQUEST_ID,
        description="幂等号：用同一个再提交一次，得到的是同一个任务",
    )
    sudo_password: SecretStr | None = Field(
        default=None, min_length=1, max_length=256, description=_SUDO
    )


class LlmBridgeDeployIn(_In):
    target_kind: TargetKind = Field(
        description="agent_server：智能体服务器；runner_host：容器服务器"
    )
    target_id: UUID = Field(description="那台服务器的 id")
    name: str = Field(min_length=1, max_length=40, description="MCP 管理器的名字")
    platforms: list[Platform] = Field(min_length=1, description="它控制的平台")
    network: Literal["auto", "tunnel"] = Field(
        default="auto",
        description="auto：和工作台同一台服务器时加入工作台的内部网络，否则经 SSH 通道；"
        "tunnel：即使同机也只监听服务器的本机地址、经 SSH 通道访问",
    )


class LlmBridgeRedeployIn(_In):
    pass


class LlmBridgeRestartIn(_In):
    pass


class LlmBridgeUninstallIn(_In):
    remove_data: bool = Field(
        default=False, description="同时删除数据卷（登录状态和会话记录，不能恢复）"
    )
    force: bool = Field(
        default=False,
        description="不连服务器，只删除工作台里的登记和部署记录（服务器连不上或已移除时用）",
    )


class LlmBridgeDeployTargetOut(BaseModel):
    kind: TargetKind
    id: UUID
    name: str
    host: str | None = Field(description="登记的地址；没有用 SSH 登记时为空")
    deployable: bool
    block_reason: str | None = Field(description="不能部署的原因（代码）")
    block_message: str | None = Field(description="不能部署的原因（给人看的）")
    may_need_password: bool = Field(
        description="登录账号不是 root、也不在 docker 组：sudo 要密码时部署要填一次登录密码"
    )
    managers: int = Field(description="这台服务器上已经部署的 MCP 管理器数量")


class LlmBridgeDeployTargetList(BaseModel):
    targets: list[LlmBridgeDeployTargetOut]
    available: bool = Field(description="这个工作台带着 MCP 管理器的构建文件，可以一键部署")


class LlmBridgeDeployJobOut(BaseModel):
    id: UUID
    kind: str = Field(description="deploy / redeploy / restart / uninstall")
    status: str = Field(description="running / succeeded / failed / interrupted")
    target_kind: TargetKind
    target_id: UUID | None = Field(description="服务器已从工作台移除时为空")
    server_name: str
    bridge_id: UUID | None = Field(description="登记成功后是那个 MCP 管理器；登记被删除后为空")
    name: str
    platforms: list[str]
    mode: str | None = Field(
        description="same_host：加入工作台的内部网络；tunnel：只监听服务器本机，经 SSH 通道访问"
    )
    steps: list[dict[str, Any]] = Field(description="每一步的 key、status、detail")
    log: str | None = Field(default=None, description="脱敏后的输出；只在读单个任务时给出")
    error_code: str | None
    error_message: str | None
    started_at: datetime
    finished_at: datetime | None


class LlmBridgeDeployJobList(BaseModel):
    jobs: list[LlmBridgeDeployJobOut]
    available: bool = Field(description="这个工作台带着 MCP 管理器的构建文件，可以一键部署")


class LlmBridgeServerManagerOut(BaseModel):
    """某台服务器上的一个 MCP 管理器（两个服务器页面的入口用）。"""

    bridge_id: UUID
    name: str
    enabled: bool
    reachable: bool | None = Field(description="工作台现在连不连得上它；停用的不探测，为空")
    version: str | None
    platforms: list[str] = Field(description="它控制的平台")
    logged_in: list[str] = Field(description="已经登录的账号（openai / claude / workbuddy）")
    deployment: LlmBridgeInstallOut


class LlmBridgeServerManagerList(BaseModel):
    managers: list[LlmBridgeServerManagerOut]
    available: bool = Field(description="这个工作台可以一键部署")
