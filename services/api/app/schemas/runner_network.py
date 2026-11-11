"""Strict declarations: destinations and workload identity always come from the database."""

from datetime import UTC, datetime, timedelta
from ipaddress import ip_network
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, SecretStr, field_validator

State = Literal["approved_pending_apply", "active", "revoked", "expired", "apply_failed"]


class RunnerServiceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=r"^[a-z][a-z0-9-]{0,39}$")
    port: int = Field(ge=1, le=65535)
    protocol: Literal["TCP", "UDP"] = "TCP"


class RunnerRevokeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: SecretStr | None = None


class LeaseInput(RunnerRevokeInput):
    expires_at: AwareDatetime

    @field_validator("expires_at")
    @classmethod
    def finite_lease(cls, value: datetime) -> datetime:
        now = datetime.now(UTC)
        if not now < value <= now + timedelta(days=30):
            raise ValueError("有效期必须在未来且不超过30天；网关运行租约另限60秒")
        return value


class RunnerIngressInput(LeaseInput):
    source_instance_id: UUID


class RunnerPublishInput(LeaseInput):
    source_cidrs: list[str] = Field(min_length=1, max_length=32)

    @field_validator("source_cidrs")
    @classmethod
    def valid_sources(cls, values: list[str]) -> list[str]:
        result = []
        for value in values:
            network = ip_network(value, strict=True)
            # IPv6 is explicitly unsupported until equivalent enforcement exists.
            if (
                network.version != 4
                or network.prefixlen == 0
                or network.is_loopback
                or network.is_link_local
                or network.is_multicast
                or network.overlaps(ip_network("10.0.0.0/8"))
                or network.overlaps(ip_network("172.16.0.0/12"))
                or network.overlaps(ip_network("192.168.0.0/16"))
            ):
                raise ValueError("需要明确IPv4公网来源；私网、无限来源和IPv6暂不支持")
            result.append(str(network))
        return list(dict.fromkeys(result))


class NetworkRecordOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    workspace_id: UUID
    environment_id: UUID
    owner_instance_id: UUID
    host_id: UUID
    state: State
    desired_revision: int
    applied_revision: int
    desired_generation: int
    applied_generation: int | None
    apply_error: str | None
    expires_at: datetime | None
    revoked_by: UUID | None
    revoked_at: datetime | None
    created_at: datetime
    accepting_new_connections: bool = False
    connect_host: str | None = None
    connect_port: int | None = None


class RunnerServiceOut(NetworkRecordOut):
    name: str
    port: int
    protocol: Literal["TCP", "UDP"]
    desired_dns: str | None
    visibility: Literal["private"] = "private"


class RunnerIngressOut(NetworkRecordOut):
    service_id: UUID
    source_instance_id: UUID


class RunnerPublishedEndpointOut(NetworkRecordOut):
    service_id: UUID
    source_cidrs: list[str]


class RunnerNetworkOut(BaseModel):
    environment_id: UUID
    workspace_id: UUID
    owner_instance_id: UUID | None
    host_id: UUID
    runtime_status: Literal["unknown", "pending_apply", "active", "apply_failed", "unsupported"]
    runtime_error: str | None
    runtime_ip: str | None = None
    services: list[RunnerServiceOut]
    ingress_rules: list[RunnerIngressOut]
    published_endpoints: list[RunnerPublishedEndpointOut]
