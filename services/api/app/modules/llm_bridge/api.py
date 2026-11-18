"""大模型会话桥的页面接口：都挂在 /api/v1/llm_bridge 下，宿主自动加启用检查。

- 查看（会话桥列表、状态、环境、会话、实例权限）：admin 及以上；
- 修改（登记会话桥、登录各平台、环境、设置、实例权限）：只有 owner。
登录、环境、设置、会话都是转给会话桥的管理接口（services/llm-bridge 的 /admin/*）。
"""

from datetime import datetime
from typing import Annotated, Any, Literal
from urllib.parse import quote, urlencode
from uuid import UUID

from fastapi import Depends, Path, Query, Response, status

from app.api.deps import csrf_protected
from app.core.errors import ApiError
from app.domain import agent_ops, secret_scan
from app.domain.runner import RunnerError
from app.models import RunnerHost, User
from app.modules.llm_bridge.client import LOGIN_READ_TIMEOUT
from app.modules.llm_bridge.deps import Admin, Owner, Service, router
from app.modules.llm_bridge.remote import RemoteError
from app.modules.llm_bridge.remote import repo_host as remote_repo_host
from app.modules.llm_bridge.remote_api import Redis_, create_container, remote_error, remote_of
from app.modules.llm_bridge.remote_files import RemoteFiles
from app.modules.llm_bridge.schemas import (
    LlmBridgeAccountStateOut,
    LlmBridgeChainIn,
    LlmBridgeChainResult,
    LlmBridgeConnectivityOut,
    LlmBridgeEnvironmentIn,
    LlmBridgeEnvironmentList,
    LlmBridgeEnvironmentOut,
    LlmBridgeEnvSessionList,
    LlmBridgeFileList,
    LlmBridgeFileOut,
    LlmBridgeGrantIn,
    LlmBridgeGrantList,
    LlmBridgeGrantOut,
    LlmBridgeIn,
    LlmBridgeIndexList,
    LlmBridgeIndexOwnerList,
    LlmBridgeJobIn,
    LlmBridgeJobLogOut,
    LlmBridgeJobResult,
    LlmBridgeJobStopIn,
    LlmBridgeList,
    LlmBridgeLocalIn,
    LlmBridgeLocalOut,
    LlmBridgeLoginCodeIn,
    LlmBridgeLoginIn,
    LlmBridgeLoginOut,
    LlmBridgeOut,
    LlmBridgePatch,
    LlmBridgeQuotaList,
    LlmBridgeResetOut,
    LlmBridgeSearchOut,
    LlmBridgeSessionList,
    LlmBridgeSettings,
    LlmBridgeSettingsPatch,
    LlmBridgeStatusOut,
    LlmBridgeTasksOut,
    LlmBridgeTimerIn,
    LlmBridgeTimerResult,
    LlmBridgeTurnList,
    LlmBridgeUsageOut,
    Platform,
)
from app.modules.llm_bridge.service import login_out
from app.schemas.common import error_responses

SlotPath = Annotated[Literal["openai", "claude", "workbuddy"], Path(description="账号槽位")]
EnvId = Annotated[str, Path(pattern=r"^be_[0-9a-f]{20}$")]
SessionId = Annotated[str, Path(pattern=r"^bs_[0-9a-f]{20}$")]
_write = [Depends(csrf_protected)]
_read_errors = error_responses(401, 403, 404, 503)
_write_errors = error_responses(401, 403, 404, 409, 422, 503)


# ---- 会话桥登记 ----
@router.get("/llm_bridge/bridges", summary="登记的会话桥（admin）", responses=_read_errors)
async def llm_bridge_list_bridges(access: Admin, service: Service) -> LlmBridgeList:
    rows = await service.bridges(access.workspace.id)
    installs = await service.installs(access.workspace.id)
    counts = await service.session_counts(rows)
    return LlmBridgeList(
        bridges=[service.bridge_out(r, installs.get(r.id), counts.get(r.id)) for r in rows]
    )


@router.post(
    "/llm_bridge/bridges",
    summary="登记一座会话桥，也就是新增一个 MCP 管理器（owner）",
    description="令牌加密保存，不回传。填了 platforms（控制的平台）就先写进会话桥，"
    "同时验证地址和令牌：503 EXTERNAL_UNAVAILABLE / BRIDGE_AUTH_FAILED 时不登记；"
    "409 BRIDGE_OUTDATED：会话桥版本太旧，不支持选择平台。409 NAME_TAKEN：已经有同名的。",
    status_code=status.HTTP_201_CREATED,
    dependencies=_write,
    responses=_write_errors,
)
async def llm_bridge_create_bridge(
    access: Owner, service: Service, body: LlmBridgeIn
) -> LlmBridgeOut:
    return await service.create_bridge(access, body)


@router.get(
    "/llm_bridge/local",
    summary="本机部署：随工作台一起启动的 MCP 管理器能不能添加（admin）",
    description="available 为 false：这个工作台没有随带它。"
    "bridge_id 有值：这个工作空间已经添加了。",
    responses=_read_errors,
)
async def llm_bridge_local_state(access: Admin, service: Service) -> LlmBridgeLocalOut:
    return await service.local_state(access.workspace.id)


@router.post(
    "/llm_bridge/local",
    summary="本机部署：把随工作台启动的 MCP 管理器添加进来（owner）",
    description="不用填地址、令牌，也不登录服务器：地址和令牌由工作台自己掌握，不经过页面。"
    "409 LOCAL_UNAVAILABLE：没有随带；LOCAL_EXISTS：已经添加过；"
    "LOCAL_TAKEN：已经被另一个工作空间添加。503：它还没启动好。",
    status_code=status.HTTP_201_CREATED,
    dependencies=_write,
    responses=_write_errors,
)
async def llm_bridge_add_local(
    access: Owner, service: Service, body: LlmBridgeLocalIn
) -> LlmBridgeOut:
    return await service.add_local(access, body)


@router.patch(
    "/llm_bridge/bridges/{bridge_id}",
    summary="修改会话桥的名字、地址、令牌或停用它（owner）",
    dependencies=_write,
    responses=_write_errors,
)
async def llm_bridge_update_bridge(
    access: Owner, service: Service, bridge_id: UUID, body: LlmBridgePatch
) -> LlmBridgeOut:
    return await service.update_bridge(access, bridge_id, body)


@router.delete(
    "/llm_bridge/bridges/{bridge_id}",
    summary="删除会话桥的登记（owner；不影响会话桥容器本身）",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=_write,
    responses=_write_errors,
)
async def llm_bridge_delete_bridge(access: Owner, service: Service, bridge_id: UUID) -> Response:
    await service.delete_bridge(access, bridge_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/llm_bridge/bridges/{bridge_id}/status",
    summary="会话桥的状态：各平台的登录情况、通道、排队、设置（admin）",
    description="连不上会话桥时 reachable 为 false，error 说明原因。",
    responses=_read_errors,
)
async def llm_bridge_bridge_status(
    access: Admin, service: Service, bridge_id: UUID
) -> LlmBridgeStatusOut:
    return await service.status(await service.bridge(access.workspace.id, bridge_id))


@router.get(
    "/llm_bridge/bridges/{bridge_id}/connectivity",
    summary="MCP 管理器所在的服务器到各平台能不能用、延迟多少（admin）",
    description="由管理器去访问各平台的地址（不带凭据）：ok 可用；blocked 连得上但被平台拒绝"
    "（这台服务器的 IP 或地区不被允许）；unreachable 连不上。结果一分钟内复用，"
    "refresh=true 时重新测。旧版本的管理器 supported 为 false。",
    responses=_read_errors,
)
async def llm_bridge_bridge_connectivity(
    access: Admin,
    service: Service,
    bridge_id: UUID,
    refresh: Annotated[bool, Query(description="重新测一次")] = False,
) -> LlmBridgeConnectivityOut:
    row = await service.bridge(access.workspace.id, bridge_id)
    return await service.connectivity(row, refresh=refresh)


@router.get(
    "/llm_bridge/bridges/{bridge_id}/quota",
    summary="各平台的额度：每个窗口已用多少、什么时候重置；是否因为额度暂停着（admin）",
    description="平台给的是已用百分比和重置时间，不是剩余 token 数。"
    "refresh=true 时让管理器向能随时查的平台（Codex）实查一次；"
    "Claude 只在跑回合时顺带报出来，显示的是最近一次看到的；"
    "CodeBuddy 的官方程序不给额度和重置时间（reports_quota=false），只有管理器自己记的用量。",
    responses=_read_errors,
)
async def llm_bridge_bridge_quota(
    access: Admin,
    service: Service,
    bridge_id: UUID,
    refresh: Annotated[bool, Query(description="向平台实查一次")] = False,
) -> LlmBridgeQuotaList:
    return await service.quota(
        await service.bridge(access.workspace.id, bridge_id), refresh=refresh
    )


@router.put(
    "/llm_bridge/bridges/{bridge_id}/settings",
    summary="修改会话桥的设置：控制的平台、并发、时限、每小时发送次数等（owner）",
    dependencies=_write,
    responses=_write_errors,
)
async def llm_bridge_update_settings(
    access: Owner, service: Service, bridge_id: UUID, body: LlmBridgeSettingsPatch
) -> LlmBridgeSettings:
    row = await service.bridge(access.workspace.id, bridge_id)
    values = body.model_dump(exclude_none=True)
    data = await service.admin_write(
        access, row, "settings_updated", "PUT", "/settings", values, values
    )
    return service.settings_out(data)


# ---- 各平台的登录 ----
@router.post(
    "/llm_bridge/bridges/{bridge_id}/accounts/{slot}/login",
    summary="登录一个平台（owner）",
    description="device_code：返回网址和验证码，用户在自己的浏览器里完成，之后轮询登录状态；"
    "web：Claude 的网页授权登录，返回网址后用“提交授权码”交回授权页显示的授权码；"
    "api_key / oauth_token：填的值保存在会话桥的数据卷里，不保存在工作台；"
    "existing：使用已经在容器里完成的登录。403：Claude 用订阅登录前要先在设置里确认风险。",
    dependencies=_write,
    responses=_write_errors,
)
async def llm_bridge_login(
    access: Owner, service: Service, bridge_id: UUID, slot: SlotPath, body: LlmBridgeLoginIn
) -> LlmBridgeLoginOut:
    row = await service.bridge(access.workspace.id, bridge_id)
    data = await service.admin_write(
        access,
        row,
        "account_login",
        "POST",
        f"/accounts/{slot}/login",
        body.model_dump(exclude_none=True),
        {"slot": slot, "method": body.method},
        read_timeout=LOGIN_READ_TIMEOUT,
    )
    return login_out(data)


@router.post(
    "/llm_bridge/bridges/{bridge_id}/accounts/{slot}/login/code",
    summary="提交授权码（owner，Claude 网页授权登录）",
    description="Claude 网页授权登录（T-0287）：人在自己的浏览器里登录授权后，"
    "把授权页显示的授权码交给 MCP 管理器里正在等它的官方程序。授权码不保存、不记审计。"
    "409：没有在等授权码的登录；422：授权码不完整。",
    dependencies=_write,
    responses=_write_errors,
)
async def llm_bridge_login_code(
    access: Owner, service: Service, bridge_id: UUID, slot: SlotPath, body: LlmBridgeLoginCodeIn
) -> LlmBridgeLoginOut:
    row = await service.bridge(access.workspace.id, bridge_id)
    data = await service.admin_write(
        access,
        row,
        "account_login_code",
        "POST",
        f"/accounts/{slot}/login/code",
        {"code": body.code},
        {"slot": slot},
    )
    return login_out(data)


@router.get(
    "/llm_bridge/bridges/{bridge_id}/accounts/{slot}/login",
    summary="登录进行到哪一步（admin）",
    responses=_read_errors,
)
async def llm_bridge_login_state(
    access: Admin, service: Service, bridge_id: UUID, slot: SlotPath
) -> LlmBridgeLoginOut:
    row = await service.bridge(access.workspace.id, bridge_id)
    data = (await service.admin(row, "GET", f"/accounts/{slot}/login")).get("login")
    return login_out(data)


@router.post(
    "/llm_bridge/bridges/{bridge_id}/accounts/{slot}/refresh",
    summary="重新检查一个平台的登录状态（admin）",
    dependencies=_write,
    responses=_write_errors,
)
async def llm_bridge_refresh_account(
    access: Admin, service: Service, bridge_id: UUID, slot: SlotPath
) -> LlmBridgeAccountStateOut:
    row = await service.bridge(access.workspace.id, bridge_id)
    return LlmBridgeAccountStateOut(**await service.admin(row, "POST", f"/accounts/{slot}/refresh"))


@router.post(
    "/llm_bridge/bridges/{bridge_id}/accounts/{slot}/logout",
    summary="退出一个平台的登录（owner）",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=_write,
    responses=_write_errors,
)
async def llm_bridge_logout(
    access: Owner, service: Service, bridge_id: UUID, slot: SlotPath
) -> Response:
    row = await service.bridge(access.workspace.id, bridge_id)
    await service.admin_write(
        access, row, "account_logout", "POST", f"/accounts/{slot}/logout", None, {"slot": slot}
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/llm_bridge/bridges/{bridge_id}/accounts/{slot}/reset",
    summary="更换账号：归档这个平台的全部会话登记，忘掉登记的账号（owner）",
    dependencies=_write,
    responses=_write_errors,
)
async def llm_bridge_reset_account(
    access: Owner, service: Service, bridge_id: UUID, slot: SlotPath
) -> LlmBridgeResetOut:
    row = await service.bridge(access.workspace.id, bridge_id)
    data = await service.admin_write(
        access, row, "account_reset", "POST", f"/accounts/{slot}/reset", None, {"slot": slot}
    )
    return LlmBridgeResetOut(**data)


# ---- 环境 ----
@router.get(
    "/llm_bridge/bridges/{bridge_id}/environments",
    summary="开发环境列表（admin）",
    responses=_read_errors,
)
async def llm_bridge_list_environments(
    access: Admin, service: Service, bridge_id: UUID
) -> LlmBridgeEnvironmentList:
    row = await service.bridge(access.workspace.id, bridge_id)
    return LlmBridgeEnvironmentList(**await service.admin(row, "GET", "/environments"))


_ENV_FIELDS = (
    "platform",
    "mode",
    "name",
    "repo_url",
    "branch",
    "sandbox",
    "network",
    "description",
    "enabled",
    "turn_timeout_seconds",
)


def _bridge_env_body(body: LlmBridgeEnvironmentIn) -> dict[str, Any]:
    """交给会话桥的环境字段（运行位置另加；远程容器的参数不给会话桥）。"""
    return body.model_dump(include=set(_ENV_FIELDS))


def _check_remote_sandbox(body: LlmBridgeEnvironmentIn) -> None:
    if body.location == "remote" and body.platform == "codex" and body.sandbox != "exec":
        # 核验记录 V2：Codex 自己的沙箱在容器里起不来，容器本身是沙箱
        raise ApiError(
            422,
            "VALIDATION_FAILED",
            "远程容器里的 Codex 只有“可执行命令”这一档"
            "（Codex 自己的沙箱在容器里用不了，容器本身就是沙箱）",
        )


@router.post(
    "/llm_bridge/bridges/{bridge_id}/environments",
    summary="新增开发环境（owner）",
    description="location=remote 时用一个远程开发容器（已有的，或者 new_container 新建一个；"
    "新建要先构建镜像，第一次要几分钟）。",
    status_code=status.HTTP_201_CREATED,
    dependencies=_write,
    responses=_write_errors,
)
async def llm_bridge_create_environment(
    access: Owner, service: Service, redis: Redis_, bridge_id: UUID, body: LlmBridgeEnvironmentIn
) -> LlmBridgeEnvironmentOut:
    row = await service.bridge(access.workspace.id, bridge_id)
    _check_remote_sandbox(body)
    payload = _bridge_env_body(body)
    meta: dict[str, Any] = {"platform": body.platform, "name": body.name, "sandbox": body.sandbox}
    if body.location == "remote":
        host = remote_repo_host(body.repo_url)
        remote = remote_of(service, redis)
        if body.new_container is not None:
            container = await create_container(
                access, service, redis, bridge_id, body.new_container, [host] if host else []
            )
        else:
            assert body.remote_container_id is not None  # noqa: S101 - 校验器保证
            try:
                container = await remote.get(access.workspace.id, row.id, body.remote_container_id)
                if host:
                    await remote.set_policy(
                        container,
                        idle_stop_minutes=None,
                        network=None,
                        domains=None,
                        repo_hosts=[host],
                    )
            except (RemoteError, RunnerError) as error:
                raise remote_error(error) from None
        await service.db.flush()
        server = await service.db.get(RunnerHost, container.host_id) if container.host_id else None
        payload.update(
            location="remote",
            remote_container=str(container.id),
            remote_label=f"{server.name if server else '容器服务器'} · {container.name}",
        )
        meta.update(location="remote", container=container.name)
    try:
        data = await service.admin(row, "POST", "/environments", payload)
    except ApiError:
        await service.db.rollback()  # 管理器没收下：新建的远程容器登记一起撤回
        raise
    await service.audit(access, "environment_created", row.id, {"bridge": row.name, **meta})
    await service.db.commit()
    return LlmBridgeEnvironmentOut(**data)


@router.put(
    "/llm_bridge/bridges/{bridge_id}/environments/{env_id}",
    summary="修改开发环境（owner；不能改所属的平台和运行位置）",
    dependencies=_write,
    responses=_write_errors,
)
async def llm_bridge_update_environment(
    access: Owner, service: Service, bridge_id: UUID, env_id: EnvId, body: LlmBridgeEnvironmentIn
) -> LlmBridgeEnvironmentOut:
    row = await service.bridge(access.workspace.id, bridge_id)
    listed = await service.admin(row, "GET", "/environments")
    current = next((e for e in (listed.get("environments") or []) if e.get("id") == env_id), None)
    if current is None:
        raise ApiError(404, "NOT_FOUND", "没有这个开发环境")
    location = current.get("location") or "local"
    if (
        body.location != location
        or body.new_container is not None
        or (
            body.remote_container_id is not None
            and str(body.remote_container_id) != current.get("remote_container")
        )
    ):
        raise ApiError(422, "VALIDATION_FAILED", "运行位置建好后不能改")
    _check_remote_sandbox(body)
    payload = _bridge_env_body(body)
    if location == "remote":
        payload.update(
            location="remote",
            remote_container=current.get("remote_container"),
            remote_label=current.get("remote_label"),
        )
    meta = {"platform": body.platform, "name": body.name, "sandbox": body.sandbox}
    data = await service.admin_write(
        access, row, "environment_updated", "PUT", f"/environments/{env_id}", payload, meta
    )
    return LlmBridgeEnvironmentOut(**data)


@router.delete(
    "/llm_bridge/bridges/{bridge_id}/environments/{env_id}",
    summary="删除开发环境（owner）",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=_write,
    responses=_write_errors,
)
async def llm_bridge_delete_environment(
    access: Owner, service: Service, bridge_id: UUID, env_id: EnvId
) -> Response:
    row = await service.bridge(access.workspace.id, bridge_id)
    await service.admin_write(
        access,
        row,
        "environment_deleted",
        "DELETE",
        f"/environments/{env_id}",
        None,
        {"id": env_id},
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---- 长时间任务：后台作业、预约、自动接着做（docs/v2/LLM_BRIDGE_LONGRUN_PLAN.md 第 6 节）----
JobId = Annotated[str, Path(pattern=r"^bj_[0-9a-f]{20}$")]
TimerId = Annotated[str, Path(pattern=r"^bw_[0-9a-f]{20}$")]


async def _by(access: Admin, service: Service) -> str:
    """页面上的人：自动接着做的消息、会话记录里写“谁”。"""
    user = await service.db.get(User, access.user_id) if access.user_id else None
    if user is None:
        return "管理员"
    return (user.display_name or user.email)[:120]


@router.get(
    "/llm_bridge/bridges/{bridge_id}/sessions/{session_id}/tasks",
    summary="一个会话的后台任务（admin）：作业、预约、自动接着做、正在跑的回合在做什么",
    responses=_read_errors,
)
async def llm_bridge_session_tasks(
    access: Admin, service: Service, bridge_id: UUID, session_id: SessionId
) -> LlmBridgeTasksOut:
    row = await service.bridge(access.workspace.id, bridge_id)
    return LlmBridgeTasksOut(**await service.admin(row, "GET", f"/sessions/{session_id}/tasks"))


@router.post(
    "/llm_bridge/bridges/{bridge_id}/sessions/{session_id}/jobs",
    summary="给开发会话开后台作业（admin；只在“可以执行命令”档位的环境；记审计）",
    responses=error_responses(401, 403, 404, 409, 422, 429, 503),
    dependencies=[Depends(csrf_protected)],
)
async def llm_bridge_job_start(
    access: Admin, service: Service, bridge_id: UUID, session_id: SessionId, body: LlmBridgeJobIn
) -> LlmBridgeJobResult:
    if any(secret_scan.scan(text) for text in (body.command, body.name, body.note)):
        raise ApiError(422, "VALIDATION_FAILED", "内容里像是有令牌、私钥一类的密钥，没有开作业")
    row = await service.bridge(access.workspace.id, bridge_id)
    # 审计只记作业名：先脱敏再截断，不记命令原文（T-0292 审查）
    label = " ".join(agent_ops.redact(body.name or body.command[:200]).split())[:40]
    data = await service.admin_write(
        access,
        row,
        "job_started",
        "POST",
        f"/sessions/{session_id}/jobs",
        {**body.model_dump(), "by": await _by(access, service)},
        {"session": session_id, "name": label},
    )
    return LlmBridgeJobResult(**data)


@router.post(
    "/llm_bridge/bridges/{bridge_id}/jobs/{job_id}/stop",
    summary="停掉后台作业（admin；记审计）",
    responses=error_responses(401, 403, 404, 409, 503),
    dependencies=[Depends(csrf_protected)],
)
async def llm_bridge_job_stop(
    access: Admin, service: Service, bridge_id: UUID, job_id: JobId, body: LlmBridgeJobStopIn
) -> LlmBridgeJobResult:
    row = await service.bridge(access.workspace.id, bridge_id)
    data = await service.admin_write(
        access,
        row,
        "job_stopped",
        "POST",
        f"/jobs/{job_id}/stop",
        {"by": await _by(access, service), "tell_session": body.tell_session},
        {"job": job_id},
    )
    return LlmBridgeJobResult(**data)


@router.get(
    "/llm_bridge/bridges/{bridge_id}/jobs/{job_id}/log",
    summary="后台作业日志的末尾（admin；已脱敏）",
    responses=_read_errors,
)
async def llm_bridge_job_log(
    access: Admin,
    service: Service,
    bridge_id: UUID,
    job_id: JobId,
    max_bytes: Annotated[int, Query(ge=1, le=200_000)] = 20000,
) -> LlmBridgeJobLogOut:
    row = await service.bridge(access.workspace.id, bridge_id)
    data = await service.admin(row, "GET", f"/jobs/{job_id}/log?max_bytes={max_bytes}")
    return LlmBridgeJobLogOut(**data)


@router.post(
    "/llm_bridge/bridges/{bridge_id}/sessions/{session_id}/timers",
    summary="预约：到点给会话发一条消息（admin；记审计）",
    responses=error_responses(401, 403, 404, 409, 422, 429, 503),
    dependencies=[Depends(csrf_protected)],
)
async def llm_bridge_timer_create(
    access: Admin, service: Service, bridge_id: UUID, session_id: SessionId, body: LlmBridgeTimerIn
) -> LlmBridgeTimerResult:
    if secret_scan.scan(body.text):
        raise ApiError(422, "VALIDATION_FAILED", "内容里像是有令牌、私钥一类的密钥，没有预约")
    row = await service.bridge(access.workspace.id, bridge_id)
    data = await service.admin_write(
        access,
        row,
        "timer_created",
        "POST",
        f"/sessions/{session_id}/timers",
        {**body.model_dump(), "by": await _by(access, service)},
        {"session": session_id},
    )
    return LlmBridgeTimerResult(**data)


@router.post(
    "/llm_bridge/bridges/{bridge_id}/timers/{timer_id}/cancel",
    summary="取消一个还没发出的预约（admin；记审计）",
    responses=error_responses(401, 403, 404, 409, 503),
    dependencies=[Depends(csrf_protected)],
)
async def llm_bridge_timer_cancel(
    access: Admin, service: Service, bridge_id: UUID, timer_id: TimerId
) -> LlmBridgeTimerResult:
    row = await service.bridge(access.workspace.id, bridge_id)
    data = await service.admin_write(
        access,
        row,
        "timer_cancelled",
        "POST",
        f"/timers/{timer_id}/cancel",
        {"by": await _by(access, service)},
        {"timer": timer_id},
    )
    return LlmBridgeTimerResult(**data)


@router.post(
    "/llm_bridge/bridges/{bridge_id}/sessions/{session_id}/chain",
    summary="恢复或停止一个会话的“自动接着做”（admin；记审计）",
    responses=error_responses(401, 403, 404, 422, 503),
    dependencies=[Depends(csrf_protected)],
)
async def llm_bridge_chain(
    access: Admin, service: Service, bridge_id: UUID, session_id: SessionId, body: LlmBridgeChainIn
) -> LlmBridgeChainResult:
    row = await service.bridge(access.workspace.id, bridge_id)
    data = await service.admin_write(
        access,
        row,
        "chain_controlled",
        "POST",
        f"/sessions/{session_id}/chain",
        {"action": body.action, "by": await _by(access, service)},
        {"session": session_id, "action": body.action},
    )
    return LlmBridgeChainResult(**data)


# ---- 开发会话工作目录里的文件（T-0289，只读）----
# 人类 2026-11-17 决定：owner / admin 能下载原文件；下载记审计
FilePath = Annotated[str, Query(max_length=1000, description="相对会话工作目录的路径；根目录不填")]
_FILE_DOWNLOAD = {
    "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}},
    "description": "原文件，按附件下载",
}


def _file_query(path: str, **extra: str) -> str:
    return urlencode({"path": path, **extra})


@router.get(
    "/llm_bridge/bridges/{bridge_id}/environments/{env_id}/sessions",
    summary="用这个开发环境建的会话（admin；最近活动的在前）",
    responses=_read_errors,
)
async def llm_bridge_environment_sessions(
    access: Admin, service: Service, bridge_id: UUID, env_id: EnvId
) -> LlmBridgeEnvSessionList:
    row = await service.bridge(access.workspace.id, bridge_id)
    data = await service.admin(row, "GET", f"/environments/{env_id}/sessions?limit=50")
    return LlmBridgeEnvSessionList(**data)


@router.get(
    "/llm_bridge/bridges/{bridge_id}/sessions/{session_id}/files",
    summary="会话工作目录里一个文件夹的内容（admin）",
    description="只浏览管理器建的会话目录；不能跳出工作目录。官方程序里同步来的会话 422。",
    responses=error_responses(401, 403, 404, 422, 503),
)
async def llm_bridge_session_files(
    access: Admin,
    service: Service,
    redis: Redis_,
    bridge_id: UUID,
    session_id: SessionId,
    path: FilePath = "",
) -> LlmBridgeFileList:
    row = await service.bridge(access.workspace.id, bridge_id)
    try:
        data = await service.admin(row, "GET", f"/sessions/{session_id}/files?{_file_query(path)}")
    except ApiError as error:
        if error.code != "REMOTE_SESSION":
            raise
        # 远程开发容器里的会话：工作台经容器服务器在容器里读（REMOTE_ENV_PLAN 10.2）
        return await RemoteFiles(service, remote_of(service, redis)).list_dir(row, session_id, path)
    return LlmBridgeFileList(**data)


@router.get(
    "/llm_bridge/bridges/{bridge_id}/sessions/{session_id}/files/content",
    summary="预览会话工作目录里的一个文件（admin；开头 512 KB，已脱敏）",
    responses=error_responses(401, 403, 404, 422, 503),
)
async def llm_bridge_session_file(
    access: Admin,
    service: Service,
    redis: Redis_,
    bridge_id: UUID,
    session_id: SessionId,
    path: FilePath,
) -> LlmBridgeFileOut:
    row = await service.bridge(access.workspace.id, bridge_id)
    url = f"/sessions/{session_id}/files/content?{_file_query(path)}"
    try:
        return LlmBridgeFileOut(**await service.admin(row, "GET", url))
    except ApiError as error:
        if error.code != "REMOTE_SESSION":
            raise
        return await RemoteFiles(service, remote_of(service, redis)).preview(row, session_id, path)


@router.get(
    "/llm_bridge/bridges/{bridge_id}/sessions/{session_id}/files/download",
    summary="下载会话工作目录里的一个文件（admin；原文件，最多 50 MB，记审计）",
    response_class=Response,
    responses={200: _FILE_DOWNLOAD, **error_responses(401, 403, 404, 422, 503)},
)
async def llm_bridge_session_file_download(
    access: Admin,
    service: Service,
    redis: Redis_,
    bridge_id: UUID,
    session_id: SessionId,
    path: FilePath,
) -> Response:
    row = await service.bridge(access.workspace.id, bridge_id)
    bridge_name = row.name
    url = f"/sessions/{session_id}/files/content?{_file_query(path, download='true')}"
    name = path.rstrip("/").rsplit("/", 1)[-1] or "file"
    try:
        content = (await service.download(row, url)).content
    except ApiError as error:
        if error.code != "REMOTE_SESSION":
            raise
        name, content = await RemoteFiles(service, remote_of(service, redis)).download(
            row, session_id, path
        )
    # 下载原文件记一条审计（谁、哪个会话、哪个文件）；文件内容不记
    await service.audit(
        access,
        "work_file_downloaded",
        bridge_id,
        {"bridge": bridge_name, "session": session_id, "path": path[:300]},
    )
    await service.db.commit()
    return Response(
        content=content,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "Cache-Control": "private, no-store",
        },
    )


# ---- 会话与回合（只读） ----
@router.get(
    "/llm_bridge/bridges/{bridge_id}/sessions",
    summary="会话桥上的会话（admin）",
    description="sync=true 时先把官方程序里有、会话桥没登记的会话登记进来。",
    responses=_read_errors,
)
async def llm_bridge_list_sessions(
    access: Admin,
    service: Service,
    bridge_id: UUID,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    sync: bool = False,
) -> LlmBridgeSessionList:
    row = await service.bridge(access.workspace.id, bridge_id)
    flag = "true" if sync else "false"
    data: dict[str, Any] = await service.admin(
        row, "GET", f"/sessions?limit={limit}&offset={offset}&sync={flag}"
    )
    return LlmBridgeSessionList(**data)


@router.get(
    "/llm_bridge/bridges/{bridge_id}/sessions/{session_id}/turns",
    summary="一个会话的回合（admin；最新的在前）",
    responses=_read_errors,
)
async def llm_bridge_list_turns(
    access: Admin, service: Service, bridge_id: UUID, session_id: SessionId
) -> LlmBridgeTurnList:
    row = await service.bridge(access.workspace.id, bridge_id)
    return LlmBridgeTurnList(**await service.admin(row, "GET", f"/sessions/{session_id}/turns"))


# ---- 会话索引 ----
@router.get(
    "/llm_bridge/sessions",
    summary="所有 MCP 管理器上的会话（索引，不含对话内容；admin）",
    description="按最后活动倒序。数据来自工作台的会话索引，后台每分钟同步一次；"
    "sync=true 时先同步再读（连不上的管理器列在 sync_failed 里，仍给出已有的记录）。"
    "对话内容不在这里：用聊天页的接口按管理器和会话读。",
    responses=_read_errors,
)
async def llm_bridge_index(
    access: Admin,
    service: Service,
    bridge_id: Annotated[UUID | None, Query(description="只看这个 MCP 管理器")] = None,
    owner_kind: Annotated[
        Literal["agent", "user", "external"] | None,
        Query(description="谁建的：智能体 / 人 / 官方程序里已有"),
    ] = None,
    owner_id: Annotated[
        str | None, Query(max_length=80, description="具体是谁：实例的 id，或 user:<用户 id>")
    ] = None,
    platform: Annotated[Platform | None, Query()] = None,
    mode: Annotated[Literal["chat", "dev", "cloud"] | None, Query(description="用法")] = None,
    status: Annotated[Literal["active", "archived"] | None, Query()] = None,
    problem: Annotated[bool, Query(description="只看有失败、结果未知或中断的回合的")] = False,
    since: Annotated[datetime | None, Query(description="最后活动不早于")] = None,
    until: Annotated[datetime | None, Query(description="最后活动早于")] = None,
    q: Annotated[str, Query(max_length=120, description="标题里有这段文字")] = "",
    sync: Annotated[bool, Query(description="先同步一次")] = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> LlmBridgeIndexList:
    workspace_id = access.workspace.id
    failed = await service.sync_index(workspace_id) if sync else []
    items, next_offset = await service.index(
        workspace_id,
        bridge_id=bridge_id,
        owner_kind=owner_kind,
        owner_id=owner_id,
        platform=platform,
        mode=mode,
        status=status,
        problem=problem,
        since=since,
        until=until,
        q=q,
        limit=limit,
        offset=offset,
    )
    return LlmBridgeIndexList(sessions=items, next_offset=next_offset, sync_failed=failed)


@router.get(
    "/llm_bridge/sessions/owners",
    summary="会话索引里出现过的发起人（筛选用；admin）",
    responses=_read_errors,
)
async def llm_bridge_index_owners(access: Admin, service: Service) -> LlmBridgeIndexOwnerList:
    return LlmBridgeIndexOwnerList(owners=await service.index_owners(access.workspace.id))


@router.get(
    "/llm_bridge/sessions/search",
    summary="在所有 MCP 管理器的提问和回答里搜索（admin）",
    description="每个会话给最新的一处和前后一小段文字。内容不存进工作台：现向各个管理器问。"
    "连不上或版本太旧的管理器列在 failed 里。",
    responses=_read_errors,
)
async def llm_bridge_search(
    access: Admin,
    service: Service,
    q: Annotated[str, Query(min_length=2, max_length=120)],
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> LlmBridgeSearchOut:
    return await service.search(access.workspace.id, q, limit)


@router.get(
    "/llm_bridge/usage",
    summary="用量：按天、MCP 管理器、平台、发起人汇总（admin）",
    description="回合数、失败和中断的数量、token。token 是官方程序报的数相加，不是账单；"
    "CodeBuddy 的积分拿不到。日期按 UTC。",
    responses=_read_errors,
)
async def llm_bridge_usage(
    access: Admin,
    service: Service,
    days: Annotated[int, Query(ge=1, le=366)] = 30,
) -> LlmBridgeUsageOut:
    return await service.usage(access.workspace.id, days)


# ---- 按实例的权限 ----
@router.get(
    "/llm_bridge/grants",
    summary="每个实例的大模型会话权限（admin）",
    responses=_read_errors,
)
async def llm_bridge_list_grants(access: Admin, service: Service) -> LlmBridgeGrantList:
    return LlmBridgeGrantList(grants=await service.grants(access.workspace.id))


@router.put(
    "/llm_bridge/grants/{instance_id}",
    summary="设置一个实例的大模型会话权限（owner）",
    description="enabled 关掉时其余权限一并收回；platforms 为空表示所有平台。",
    dependencies=_write,
    responses=_write_errors,
)
async def llm_bridge_set_grant(
    access: Owner, service: Service, instance_id: UUID, body: LlmBridgeGrantIn
) -> LlmBridgeGrantOut:
    return await service.set_grant(access, instance_id, body)


# ---- 聊天页面（docs/v2/LLM_CHAT_PLAN.md）：接口写在 chat_api.py，登记在同一个 router 上 ----
from app.modules.llm_bridge import chat_api as _chat_api  # noqa: E402, F401
from app.modules.llm_bridge import chat_group_api as _chat_group_api  # noqa: E402, F401
from app.modules.llm_bridge import deploy_api as _deploy_api  # noqa: E402, F401
from app.modules.llm_bridge import memory_api as _memory_api  # noqa: E402, F401

__all__ = ["router"]
