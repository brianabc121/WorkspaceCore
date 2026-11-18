"""远程开发容器的页面接口（docs/v2/LLM_BRIDGE_REMOTE_ENV_PLAN.md 第 10、12 节）。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

ImageKind = Literal["builtin", "template", "custom"]
NetworkChoice = Literal["models", "models_registries", "all"]
Preset = Literal["small", "medium", "large", "custom"]
ContainerState = Literal[
    "building",
    "creating",
    "running",
    "stopped",
    "archived",
    "deleting",
    "failed",
    "gone",
]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RemoteResources(_In):
    cpus: float = Field(ge=0.5, le=32)
    memory_mb: int = Field(ge=512, le=131072)
    disk_gb: int = Field(ge=5, le=1000)


class LlmBridgeRemoteContainerIn(_In):
    request_id: str | None = Field(
        None,
        min_length=1,
        max_length=100,
        pattern=r"^[\w.\-:]+$",
        description="幂等号：用同一个再提交一次，得到的是同一个容器",
    )
    name: str = Field(min_length=1, max_length=60)
    host_id: UUID = Field(description="放在哪台容器服务器上（只能是 Docker 类的）")
    image_kind: ImageKind = Field(
        "builtin",
        description="builtin：内置“大模型开发环境”；template：容器服务器上的模板再加一层命令行；"
        "custom：自定义镜像再加一层命令行（要求 glibc、有 /bin/sh；缺 coreutils、findutils、"
        "util-linux 里的基础命令时，构建时用镜像自带的包管理器补装）",
    )
    image: str | None = Field(
        None, max_length=300, description="template 时是模板名；custom 时是镜像名"
    )
    preset: Preset = "medium"
    resources: RemoteResources | None = Field(None, description="preset 是 custom 时必填")
    runtime: Literal["gvisor", "runc"] = "gvisor"
    network: NetworkChoice = Field(
        "all",
        description="models：只连模型接口和代码仓库；models_registries：再加常用包仓库；"
        "all：全部外网。订阅账号登录要的域名还没核验完，暂时推荐 all",
    )
    domains: list[str] = Field(default_factory=list, max_length=50)
    secrets: list[str] = Field(
        default_factory=list, max_length=20, description="工作台密钥库里的密钥名称"
    )
    idle_stop_minutes: Literal[0, 30, 120] = Field(30, description="空闲多久停止；0 = 不停")

    @model_validator(mode="after")
    def _check(self) -> LlmBridgeRemoteContainerIn:
        if self.preset == "custom" and self.resources is None:
            raise ValueError("自定义资源时要给 resources")
        if self.image_kind != "builtin" and not (self.image or "").strip():
            raise ValueError("选模板或自定义镜像时要写名字")
        return self


class LlmBridgeRemoteContainerPatch(_In):
    version: int
    idle_stop_minutes: Literal[0, 30, 120] | None = None
    network: NetworkChoice | None = None
    domains: list[str] | None = Field(None, max_length=50)


class LlmBridgeRemoteContainerAction(_In):
    action: Literal["start", "stop", "retry", "update_image"]
    version: int


class LlmBridgeRemoteContainerOut(BaseModel):
    id: UUID
    bridge_id: UUID
    name: str
    host_id: UUID | None
    host_name: str | None
    image_kind: ImageKind
    image: str | None
    state: ContainerState
    state_reason: str | None
    runner_environment_id: UUID | None
    cpus: float
    memory_mb: int
    disk_gb: int
    runtime: str
    network: NetworkChoice
    domains: list[str]
    secrets: list[str]
    idle_stop_minutes: int
    cli_versions: dict[str, str]
    versions_ok: bool = Field(description="容器里的命令行版本和管理器一致")
    updating_image: bool = Field(description="正在按新版本更新镜像")
    image_error: str | None = None
    last_used_at: datetime | None
    created_at: datetime
    version: int


class LlmBridgeRemoteContainerList(BaseModel):
    containers: list[LlmBridgeRemoteContainerOut]


class LlmBridgeRemoteHostOut(BaseModel):
    id: UUID
    name: str
    status: str
    available: bool
    reason: str | None = Field(None, description="不能选的原因")
    cpus: float | None
    memory_mb: int | None
    disk_gb: int | None
    runtimes: list[str]


class LlmBridgeRemoteTargets(BaseModel):
    hosts: list[LlmBridgeRemoteHostOut]
    presets: dict[str, RemoteResources]
    limits: dict[str, float]
    used: dict[str, float]
    versions: dict[str, str] = Field(description="内置镜像里的命令行版本（和管理器一致）")
    network_domains: dict[str, list[str]] = Field(
        description="“只连模型接口”档按平台放行的域名（核验过的）；"
        "registries 是“常用包仓库”档另加的"
    )


class LlmBridgeRemoteLoginIn(_In):
    action: Literal["start", "code", "cancel"] = "start"
    code: str | None = Field(None, max_length=4096, description="Claude 授权页上显示的授权码")


class LlmBridgeRemotePlatformOut(BaseModel):
    state: Literal["injected", "ready", "needs_login", "mismatch"] = Field(
        description="injected：凭据随回合注入（API 密钥、长期令牌），不用登录；"
        "ready：容器里已经登录；needs_login：要在容器里登录一次；mismatch：容器里登录的不是同一个账号"
    )
    detail: str = ""
    identity: str | None = None
    can_login: bool = False


class LlmBridgeRemotePlatforms(BaseModel):
    platforms: dict[str, LlmBridgeRemotePlatformOut] = Field(description="按账号槽位")
