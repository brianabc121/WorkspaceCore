"""Owner-managed network declarations. Runtime ACKs use a separate trust path."""

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
from app.domain.auth import AuthContext
from app.domain.policy import WorkspaceAccess
from app.domain.runner_network import RunnerNetworkService
from app.schemas.runner_network import (
    RunnerIngressInput,
    RunnerIngressOut,
    RunnerIngressUpdate,
    RunnerNetworkOut,
    RunnerPublishedEndpointOut,
    RunnerPublishInput,
    RunnerPublishUpdate,
    RunnerRevokeInput,
    RunnerServiceCreate,
    RunnerServiceOut,
    RunnerServiceUpdate,
)

router = APIRouter(tags=["runner-network"], dependencies=[Security(authenticated)])
Admin = Annotated[WorkspaceAccess, Depends(workspace_admin)]
Owner = Annotated[WorkspaceAccess, Depends(workspace_owner)]
Context = Annotated[AuthContext, Security(authenticated)]


def get_service(
    db: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_app_settings)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> RunnerNetworkService:
    return RunnerNetworkService(db, settings, redis)


Service = Annotated[RunnerNetworkService, Depends(get_service)]


@router.get("/runner/environments/{environment_id}/network")
async def network(environment_id: UUID, access: Admin, service: Service) -> RunnerNetworkOut:
    return await service.list(access, environment_id)


@router.post(
    "/runner/environments/{environment_id}/services",
    status_code=201,
    dependencies=[Depends(csrf_protected)],
)
async def register_service(
    environment_id: UUID,
    body: RunnerServiceCreate,
    context: Context,
    access: Owner,
    service: Service,
) -> RunnerServiceOut:
    return await service.create_service(context, access, environment_id, body)


@router.patch(
    "/runner/environments/{environment_id}/services/{service_id}",
    dependencies=[Depends(csrf_protected)],
)
async def update_service(
    environment_id: UUID,
    service_id: UUID,
    body: RunnerServiceUpdate,
    context: Context,
    access: Owner,
    service: Service,
) -> RunnerServiceOut:
    return await service.update_service(context, access, environment_id, service_id, body)


@router.patch(
    "/runner/environments/{environment_id}/ingress/{rule_id}",
    dependencies=[Depends(csrf_protected)],
)
async def update_ingress(
    environment_id: UUID,
    rule_id: UUID,
    body: RunnerIngressUpdate,
    context: Context,
    access: Owner,
    service: Service,
) -> RunnerIngressOut:
    return RunnerIngressOut.model_validate(
        await service.update_rule(context, access, environment_id, rule_id, body, kind="ingress")
    )


@router.patch(
    "/runner/environments/{environment_id}/published-endpoints/{endpoint_id}",
    dependencies=[Depends(csrf_protected)],
)
async def update_endpoint(
    environment_id: UUID,
    endpoint_id: UUID,
    body: RunnerPublishUpdate,
    context: Context,
    access: Owner,
    service: Service,
) -> RunnerPublishedEndpointOut:
    return RunnerPublishedEndpointOut.model_validate(
        await service.update_rule(
            context, access, environment_id, endpoint_id, body, kind="published"
        )
    )


@router.post(
    "/runner/environments/{environment_id}/services/{service_id}/ingress",
    status_code=201,
    dependencies=[Depends(csrf_protected)],
)
async def allow_source(
    environment_id: UUID,
    service_id: UUID,
    body: RunnerIngressInput,
    context: Context,
    access: Owner,
    service: Service,
) -> RunnerIngressOut:
    return await service.create_ingress(context, access, environment_id, service_id, body)


@router.post(
    "/runner/environments/{environment_id}/services/{service_id}/publish",
    status_code=201,
    dependencies=[Depends(csrf_protected)],
)
async def publish_service(
    environment_id: UUID,
    service_id: UUID,
    body: RunnerPublishInput,
    context: Context,
    access: Owner,
    service: Service,
) -> RunnerPublishedEndpointOut:
    return await service.publish(context, access, environment_id, service_id, body)


@router.post(
    "/runner/environments/{environment_id}/services/{service_id}/revoke",
    dependencies=[Depends(csrf_protected)],
)
async def revoke_service(
    environment_id: UUID,
    service_id: UUID,
    body: RunnerRevokeInput,
    context: Context,
    access: Owner,
    service: Service,
) -> RunnerServiceOut:
    return RunnerServiceOut.model_validate(
        await service.revoke(context, access, environment_id, service_id, body, kind="service")
    )


@router.post(
    "/runner/environments/{environment_id}/ingress/{rule_id}/revoke",
    dependencies=[Depends(csrf_protected)],
)
async def revoke_ingress(
    environment_id: UUID,
    rule_id: UUID,
    body: RunnerRevokeInput,
    context: Context,
    access: Owner,
    service: Service,
) -> RunnerIngressOut:
    return RunnerIngressOut.model_validate(
        await service.revoke(context, access, environment_id, rule_id, body, kind="ingress")
    )


@router.post(
    "/runner/environments/{environment_id}/published-endpoints/{endpoint_id}/revoke",
    dependencies=[Depends(csrf_protected)],
)
async def revoke_endpoint(
    environment_id: UUID,
    endpoint_id: UUID,
    body: RunnerRevokeInput,
    context: Context,
    access: Owner,
    service: Service,
) -> RunnerPublishedEndpointOut:
    return RunnerPublishedEndpointOut.model_validate(
        await service.revoke(context, access, environment_id, endpoint_id, body, kind="published")
    )
