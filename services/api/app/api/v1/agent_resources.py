"""Historical resource reconciliation uses the saved, pinned SSH transport only."""

from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import csrf_protected
from app.api.v1.agents import Owner, ServerService, _server_ip
from app.domain.agent_runtime_resources import reconcile_resources
from app.schemas.agents import AgentDeploymentState
from app.schemas.common import error_responses

router = APIRouter(tags=["agent-resources"])


class ResourceReconcileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)


@router.post(
    "/agents/instances/{instance_id}/resources/reconcile",
    dependencies=[Depends(csrf_protected)],
    responses=error_responses(401, 403, 404, 409, 422, 429),
)
async def reconcile(
    access: Owner,
    request: Request,
    service: ServerService,
    instance_id: UUID,
    body: ResourceReconcileRequest,
) -> AgentDeploymentState:
    return await reconcile_resources(
        service, access, instance_id, version=body.version, client_ip=_server_ip(request)
    )
