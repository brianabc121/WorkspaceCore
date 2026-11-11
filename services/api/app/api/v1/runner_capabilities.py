"""Owner-managed runner capabilities; requests use the existing AI approval center."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Security
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    authenticated,
    csrf_protected,
    get_app_settings,
    get_db,
    get_redis,
    workspace_admin,
    workspace_owner,
)
from app.core.config import Settings
from app.core.errors import ApiError
from app.domain.ai_tools import ToolCallError
from app.domain.auth import AuthContext
from app.domain.policy import WorkspaceAccess, not_found
from app.domain.runner_capabilities import RunnerCapabilityService
from app.models import AgentInstance
from app.schemas.ai_tools import PendingApproval
from app.schemas.runner_capabilities import (
    RunnerCapabilitiesOut,
    RunnerCapabilityGrantOut,
    RunnerCapabilityRequestInput,
    RunnerCapabilityRevokeInput,
)

router = APIRouter(tags=["runner-capabilities"], dependencies=[Security(authenticated)])
Admin = Annotated[WorkspaceAccess, Depends(workspace_admin)]
Owner = Annotated[WorkspaceAccess, Depends(workspace_owner)]
Context = Annotated[AuthContext, Security(authenticated)]


def get_service(
    db: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_app_settings)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> RunnerCapabilityService:
    return RunnerCapabilityService(db, settings, redis)


Service = Annotated[RunnerCapabilityService, Depends(get_service)]


@router.get("/agents/instances/{instance_id}/runner/capabilities")
async def capabilities(instance_id: UUID, access: Admin, service: Service) -> RunnerCapabilitiesOut:
    return await service.list(access, instance_id)


@router.post(
    "/agents/instances/{instance_id}/runner/capabilities/request",
    dependencies=[Depends(csrf_protected)],
)
async def request_capability(
    instance_id: UUID, body: RunnerCapabilityRequestInput, access: Admin, service: Service
) -> PendingApproval:
    instance = await service.db.get(AgentInstance, instance_id)
    if instance is None or instance.workspace_id != access.workspace.id:
        raise not_found()
    try:
        result = await service.request(instance, **body.model_dump())
    except ToolCallError as error:
        status = 403 if error.code.startswith("FORBIDDEN") else 409
        raise ApiError(status, error.code, error.message) from error
    await service.db.commit()
    return result


@router.post(
    "/agents/instances/{instance_id}/runner/capabilities/{grant_id}/revoke",
    dependencies=[Depends(csrf_protected)],
)
async def revoke_capability(
    instance_id: UUID,
    grant_id: UUID,
    body: RunnerCapabilityRevokeInput,
    context: Context,
    access: Owner,
    service: Service,
) -> RunnerCapabilityGrantOut:
    return await service.revoke(context, access, instance_id, grant_id, body)
