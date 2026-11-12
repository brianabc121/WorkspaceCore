"""运行服务器接口（T-0136，docs/v2/RUNNER_PLAN.md 第 9 节）：只在接入入口开放。

- `POST /runner-api/v1/poll`：长轮询（最多 25 秒）领指令，同时上报容量；
- `POST /runner-api/v1/commands/{id}/result`：回报一条指令的结果（重复回报只认第一次）；
- `POST /runner-api/v1/report`：定期对账；
- `PUT|GET /runner-api/v1/snapshots/{id}/parts/{n}`：常驻环境快照的分块上传与恢复（T-0139）。
认证：运行服务器凭证（`mwr_`）+ 接入入口写入的入口请求头（外网另需客户端证书指纹），
只在请求直接来自接入入口时采用这些请求头。认证失败一律 401 RUNNER_UNAUTHORIZED。
"""

import asyncio
import uuid
from typing import Annotated

from fastapi import APIRouter, Path, Request, Response, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.agent_api import CERT_HEADER, INGRESS_HEADER, _from_ingress_proxy
from app.api.deps import client_ip
from app.core.errors import ApiError
from app.domain.runner_hosts import RunnerHosts, unauthorized
from app.domain.runner_snapshots import MAX_PARTS, PART_BYTES, SnapshotStore
from app.models import RunnerEnvironment, RunnerHost, RunnerSnapshot
from app.schemas.common import error_responses
from app.schemas.runner import (
    RunnerCommandResult,
    RunnerPoll,
    RunnerPollResponse,
    RunnerReport,
    RunnerReportResult,
    RunnerReportSample,
)

runner_token = HTTPBearer(
    scheme_name="RunnerToken",
    description="运行服务器凭证（接入包里的 MYWS_RUNNER_TOKEN）；外网入口另需客户端证书",
    auto_error=False,
)
RunnerCredentials = Annotated[HTTPAuthorizationCredentials | None, Security(runner_token)]

router = APIRouter(prefix="/runner-api/v1", tags=["runner-api"])
_errors = error_responses(401, 404, 422, 429)
POLL_INTERVAL = 0.5


class ResultAccepted(BaseModel):
    accepted: bool


async def _authenticate(
    request: Request, credentials: HTTPAuthorizationCredentials | None, hosts: RunnerHosts
) -> RunnerHost:
    settings = request.app.state.settings
    trusted = _from_ingress_proxy(request, settings.agent_ingress_proxy_networks)
    return await hosts.authenticate(
        credentials.credentials if credentials else None,
        ingress=request.headers.get(INGRESS_HEADER) if trusted else None,
        cert_fingerprint=request.headers.get(CERT_HEADER) if trusted else None,
        client_ip=client_ip(request, settings),
    )


@router.post(
    "/poll",
    summary="领指令（运行服务器，长轮询）",
    description="上报容量与版本，领走待执行的指令（最多 10 条）；没有指令时最多等 wait_seconds 秒。"
    "需要注入密钥的指令在这时才解密放进 payload.secret_values（不落库）。"
    "响应带 Cache-Control: no-store。",
    responses=_errors,
)
async def poll(
    request: Request, response: Response, credentials: RunnerCredentials, body: RunnerPoll
) -> RunnerPollResponse:
    response.headers["Cache-Control"] = "no-store"
    app = request.app
    ip = client_ip(request, app.state.settings)
    async with app.state.sessionmaker() as db:
        hosts = RunnerHosts(db, app.state.settings, app.state.redis)
        host = await _authenticate(request, credentials, hosts)
        await hosts.check_in(host, body)
        commands = await hosts.take(host, client_ip=ip)
        await db.commit()
        host_id = host.id
    loop = asyncio.get_running_loop()
    until = loop.time() + body.wait_seconds
    while not commands and loop.time() < until:
        await asyncio.sleep(POLL_INTERVAL)
        if await request.is_disconnected():
            break
        async with app.state.sessionmaker() as db:
            hosts = RunnerHosts(db, app.state.settings, app.state.redis)
            current = await db.get(RunnerHost, host_id)
            if current is None or current.status == "disabled":
                raise unauthorized()
            commands = await hosts.take(current, client_ip=ip)
            if commands:
                await db.commit()
    return RunnerPollResponse(commands=commands)


@router.post(
    "/commands/{command_id}/result",
    summary="回报指令结果（运行服务器）",
    description="同一条指令重复回报只认第一次（accepted=false）。",
    responses=_errors,
)
async def command_result(
    request: Request,
    credentials: RunnerCredentials,
    command_id: uuid.UUID,
    body: RunnerCommandResult,
) -> ResultAccepted:
    app = request.app
    async with app.state.sessionmaker() as db:
        hosts = RunnerHosts(db, app.state.settings, app.state.redis)
        host = await _authenticate(request, credentials, hosts)
        accepted = await hosts.result(host, command_id, body)
        await db.commit()
    return ResultAccepted(accepted=accepted)


@router.post("/report/start", response_model=RunnerReportSample, responses=_errors)
async def begin_report(request: Request, credentials: RunnerCredentials) -> RunnerReportSample:
    """Issue the sampling epoch before collecting metrics; never trust agent clocks."""
    app = request.app
    async with app.state.sessionmaker() as db:
        hosts = RunnerHosts(db, app.state.settings, app.state.redis)
        host = await _authenticate(request, credentials, hosts)
        result = await hosts.begin_report(host)
        await db.commit()
    return result


@router.post(
    "/report",
    summary="对账（运行服务器，定期）",
    description="上报带工作台标签的容器、数据卷、镜像与占用、出口访问记录；"
    "返回记录里有、实际没有的环境（lost）和记录外的容器"
    "（stray，运行服务器先停止、24 小时后删除）。",
    responses=error_responses(401, 404, 409, 422, 429),
)
async def report(
    request: Request, credentials: RunnerCredentials, body: RunnerReport
) -> RunnerReportResult:
    app = request.app
    async with app.state.sessionmaker() as db:
        hosts = RunnerHosts(db, app.state.settings, app.state.redis)
        host = await _authenticate(request, credentials, hosts)
        result = await hosts.report(host, body)
        await db.commit()
    return result


# ---------------------------------------------------------------- 快照（T-0139）

PART_HEADROOM = 64 * 1024


async def _snapshot_for(
    db: AsyncSession, host: RunnerHost, snapshot_id: uuid.UUID
) -> RunnerSnapshot:
    """只认这台运行服务器上的环境的快照；别的一律当不存在。"""
    snapshot = await db.get(RunnerSnapshot, snapshot_id)
    env = await db.get(RunnerEnvironment, snapshot.environment_id) if snapshot else None
    if snapshot is None or env is None or env.host_id != host.id:
        raise ApiError(404, "NOT_FOUND", "没有这份快照")
    return snapshot


@router.put(
    "/snapshots/{snapshot_id}/parts/{index}",
    summary="上传快照的一块（运行服务器）",
    description="请求体是原始字节（最多 16 MB）；工作台用这份快照的数据密钥加密后存到对象存储。",
    responses=error_responses(401, 404, 409, 422, 503),
)
async def put_snapshot_part(
    request: Request,
    credentials: RunnerCredentials,
    snapshot_id: uuid.UUID,
    index: Annotated[int, Path(ge=0, lt=MAX_PARTS)],
) -> ResultAccepted:
    app = request.app
    async with app.state.sessionmaker() as db:
        hosts = RunnerHosts(db, app.state.settings, app.state.redis)
        host = await _authenticate(request, credentials, hosts)
        snapshot = await _snapshot_for(db, host, snapshot_id)
        # 先认证、再读请求体（最多 16 MB 加一点余量）
        data = bytearray()
        async for chunk in request.stream():
            data += chunk
            if len(data) > PART_BYTES + PART_HEADROOM:
                raise ApiError(413, "PAYLOAD_TOO_LARGE", "每块最多 16 MB")
        await SnapshotStore(db, app.state.settings).put_part(snapshot, index, bytes(data))
        await db.commit()
    return ResultAccepted(accepted=True)


@router.get(
    "/snapshots/{snapshot_id}/parts/{index}",
    summary="下载快照的一块（运行服务器，重建时恢复）",
    response_class=Response,
    responses={
        200: {"content": {"application/octet-stream": {}}, "description": "解密后的原始字节"},
        **error_responses(401, 404, 409, 422, 503),
    },
)
async def get_snapshot_part(
    request: Request,
    credentials: RunnerCredentials,
    snapshot_id: uuid.UUID,
    index: Annotated[int, Path(ge=0, lt=MAX_PARTS)],
) -> Response:
    app = request.app
    async with app.state.sessionmaker() as db:
        hosts = RunnerHosts(db, app.state.settings, app.state.redis)
        host = await _authenticate(request, credentials, hosts)
        snapshot = await _snapshot_for(db, host, snapshot_id)
        data = await SnapshotStore(db, app.state.settings).get_part(snapshot, index)
    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={"Cache-Control": "no-store"},
    )
