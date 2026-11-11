"""Instance-scoped runner capability contracts."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, SecretStr

Capability = Literal["runner.unlimited_container_count", "runner.cross_agent_communication"]
GrantState = Literal["approved_pending_apply", "active", "revoked", "expired", "apply_failed"]


class RunnerCapabilityRequestInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    capability: Capability
    reason: str = Field(min_length=1, max_length=2000)
    expires_at: AwareDatetime | None = None


class RunnerCapabilityGrantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    instance_id: UUID
    approval_id: UUID
    capability: Capability
    reason: str
    requested_by: UUID | None
    approved_by: UUID
    approved_at: datetime
    state: GrantState
    expires_at: datetime | None
    revoked_by: UUID | None
    revoked_at: datetime | None
    revoke_reason: str | None
    desired_revision: int
    applied_revision: int
    policy_version: int


class RunnerCapabilityPendingOut(BaseModel):
    action_id: UUID
    capability: Capability
    status: Literal["pending_approval"] = "pending_approval"
    reason: str
    expires_at: datetime


class RunnerCapabilitiesOut(BaseModel):
    instance_id: UUID
    held_count: int
    default_limit: int = 5
    unlimited_active: bool
    grants: list[RunnerCapabilityGrantOut]
    pending_approval_ids: list[UUID]
    pending_requests: list[RunnerCapabilityPendingOut]


class RunnerCapabilityRevokeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=1, max_length=2000)
    password: SecretStr | None = None
