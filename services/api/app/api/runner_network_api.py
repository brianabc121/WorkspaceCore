"""Host-authenticated policy transport; browser credentials never acknowledge a policy."""

from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from app.api.runner_api import RunnerCredentials, _authenticate
from app.core.errors import ApiError
from app.domain.runner_hosts import RunnerHosts
from app.domain.runner_network import RunnerNetworkService
from app.domain.runner_network_bindings import BindingReceipt, update_binding
from app.schemas.common import error_responses

router = APIRouter(prefix="/runner-api/v1/network", tags=["runner-network-host"])


@router.post("/binding", responses=error_responses(401, 404, 409, 422))
async def runtime_binding(
    request: Request, response: Response, credentials: RunnerCredentials, body: BindingReceipt
) -> dict[str, bool]:
    response.headers["Cache-Control"] = "no-store"
    app = request.app
    async with app.state.sessionmaker() as db:
        host = await _authenticate(
            request, credentials, RunnerHosts(db, app.state.settings, app.state.redis)
        )
        return {"accepted": await update_binding(db, app.state.settings, host, body)}


class DesiredRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PolicyAcknowledgment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    record_id: UUID
    revision: int = Field(ge=1)
    generation: int = Field(ge=1)
    success: bool
    receipt_uid: str = Field(min_length=1, max_length=128)
    resource_version: str = Field(pattern=r"^[0-9]{1,32}$")
    error: str | None = Field(default=None, max_length=200)


class AcknowledgmentResult(BaseModel):
    accepted: bool
    enforcement_scope: Literal["private_service"] = "private_service"


@router.post("/desired", responses=error_responses(401, 404, 409, 422))
async def desired(
    request: Request, response: Response, credentials: RunnerCredentials, body: DesiredRequest
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    app = request.app
    async with app.state.sessionmaker() as db:
        host = await _authenticate(
            request, credentials, RunnerHosts(db, app.state.settings, app.state.redis)
        )
        if host.backend != "kubernetes":
            raise ApiError(409, "NETWORK_BACKEND_UNAVAILABLE", "当前服务器未使用受管网络后端")
        result = await RunnerNetworkService(db, app.state.settings, app.state.redis).desired_policy(
            host.id
        )
        await db.commit()  # generation changes advance desired revisions
        return result


@router.post("/ack", responses=error_responses(401, 404, 409, 422))
async def acknowledge(
    request: Request, response: Response, credentials: RunnerCredentials, body: PolicyAcknowledgment
) -> AcknowledgmentResult:
    response.headers["Cache-Control"] = "no-store"
    app = request.app
    async with app.state.sessionmaker() as db:
        host = await _authenticate(
            request, credentials, RunnerHosts(db, app.state.settings, app.state.redis)
        )
        # A saved Service/API UID is not proof that the cluster's network plugin
        # enforces isolation. Both deployment and node validation must be explicit.
        if (
            not app.state.settings.runner_network_verified
            or host.backend != "kubernetes"
            or (host.runtime_metadata or {}).get("network_validation_revision") != 1
        ):
            raise ApiError(
                409, "NETWORK_VALIDATION_REQUIRED", "运行节点尚未通过跨节点网络与隔离验证"
            )
        service = RunnerNetworkService(db, app.state.settings, app.state.redis)
        accepted = await service.acknowledge(
            host.id,
            body.record_id,
            body.revision,
            generation=body.generation,
            success=body.success,
            error=body.error,
        )
        await db.commit()
        return AcknowledgmentResult(accepted=accepted)
