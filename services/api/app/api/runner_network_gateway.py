"""Dedicated operator gateway credential: neither user sessions nor runner tokens suffice."""

import secrets
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict

from app.core.errors import ApiError
from app.domain.runner_network_gateway import GatewayAck, LeaseRequest, NetworkGateway

router = APIRouter(
    prefix="/api/v1/internal/runner-network/gateway",
    tags=["runner-network-gateway"],
    include_in_schema=False,
)
bearer = HTTPBearer(auto_error=False)
Credentials = Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]


class Empty(BaseModel):
    model_config = ConfigDict(extra="forbid")


def authorize(request: Request, response: Response, credentials: Credentials) -> None:
    response.headers["Cache-Control"] = "no-store"
    expected = request.app.state.settings.runner_network_gateway_token.get_secret_value()
    if (
        not expected
        or credentials is None
        or not secrets.compare_digest(credentials.credentials, expected)
    ):
        raise ApiError(401, "GATEWAY_UNAUTHORIZED", "网关凭据无效")


@router.post("/config", dependencies=[Depends(authorize)])
async def gateway_config(request: Request, body: Empty) -> dict[str, Any]:
    async with request.app.state.sessionmaker() as db:
        return await NetworkGateway(db, request.app.state.settings).config()


@router.post("/lease", dependencies=[Depends(authorize)])
async def gateway_lease(request: Request, body: LeaseRequest) -> dict[str, Any]:
    async with request.app.state.sessionmaker() as db:
        return await NetworkGateway(db, request.app.state.settings).lease(body)


@router.post("/ack", dependencies=[Depends(authorize)])
async def gateway_ack(request: Request, body: GatewayAck) -> dict[str, bool]:
    async with request.app.state.sessionmaker() as db:
        return {"accepted": await NetworkGateway(db, request.app.state.settings).acknowledge(body)}


@router.post("/ready", dependencies=[Depends(authorize)])
async def gateway_ready(request: Request, body: Empty) -> dict[str, bool]:
    async with request.app.state.sessionmaker() as db:
        return {"accepted": await NetworkGateway(db, request.app.state.settings).ready()}
