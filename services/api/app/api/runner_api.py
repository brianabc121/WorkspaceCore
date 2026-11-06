"""运行服务器接口（T-0136，docs/v2/RUNNER_PLAN.md 第 9 节）：只在接入入口开放。

- `POST /runner-api/v1/poll`：长轮询（最多 25 秒）领指令，同时上报容量；
- `POST /runner-api/v1/commands/{id}/result`：回报一条指令的结果（重复回报只认第一次）；
- `POST /runner-api/v1/report`：每分钟对账。
认证：运行服务器凭证（`mwr_`）+ 接入入口写入的入口请求头（外网另需客户端证书指纹），
只在请求直接来自接入入口时采用这些请求头。认证失败一律 401 RUNNER_UNAUTHORIZED。
"""

import asyncio
import uuid
from typing import Annotated

from fastapi import APIRouter, Request, Response, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from app.api.agent_api import CERT_HEADER, INGRESS_HEADER, _from_ingress_proxy
from app.api.deps import client_ip
from app.domain.runner_hosts import RunnerHosts, unauthorized
from app.models import RunnerHost
from app.schemas.common import error_responses
from app.schemas.runner import (
    RunnerCommandResult,
    RunnerPoll,
    RunnerPollResponse,
    RunnerReport,
    RunnerReportResult,
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


@router.post(
    "/report",
    summary="对账（运行服务器，每分钟）",
    description="上报带工作台标签的容器、数据卷、镜像与占用、出口访问记录；"
    "返回记录里有、实际没有的环境（lost）和记录外的容器"
    "（stray，运行服务器先停止、24 小时后删除）。",
    responses=_errors,
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
