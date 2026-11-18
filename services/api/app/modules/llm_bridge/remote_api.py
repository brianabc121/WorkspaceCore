"""远程开发容器的页面接口（docs/v2/LLM_BRIDGE_REMOTE_ENV_PLAN.md 第 12 节）。

查看：owner / admin；建、改、操作、删：owner。都校验工作空间，写操作带 CSRF、记审计。
"""

from __future__ import annotations

import contextlib
from typing import Annotated, Literal
from uuid import UUID

from fastapi import Depends, Path, Response, status
from redis.asyncio import Redis
from sqlalchemy import select

from app.api.deps import csrf_protected, get_redis
from app.core.errors import ApiError
from app.domain.runner import RunnerError
from app.models import RunnerEnvironment, RunnerHost
from app.modules.llm_bridge import remote_config as cfg
from app.modules.llm_bridge.client import LOGIN_READ_TIMEOUT
from app.modules.llm_bridge.deps import Admin, Owner, Service, router
from app.modules.llm_bridge.models import LlmBridgeRemoteContainer
from app.modules.llm_bridge.remote import RemoteContainers, RemoteError, container_state
from app.modules.llm_bridge.remote_schemas import (
    LlmBridgeRemoteContainerAction,
    LlmBridgeRemoteContainerIn,
    LlmBridgeRemoteContainerList,
    LlmBridgeRemoteContainerOut,
    LlmBridgeRemoteContainerPatch,
    LlmBridgeRemoteHostOut,
    LlmBridgeRemoteLoginIn,
    LlmBridgeRemotePlatformOut,
    LlmBridgeRemotePlatforms,
    LlmBridgeRemoteTargets,
    RemoteResources,
)
from app.modules.llm_bridge.schemas import LlmBridgeLoginOut
from app.modules.llm_bridge.service import LlmBridgeService, login_out
from app.schemas.common import error_responses

_write = [Depends(csrf_protected)]
_read_errors = error_responses(401, 403, 404, 503)
_write_errors = error_responses(401, 403, 404, 409, 422, 503)
Redis_ = Annotated[Redis, Depends(get_redis)]

_STATUS = {
    "NOT_FOUND": 404,
    "VALIDATION_FAILED": 422,
    "QUOTA_EXCEEDED": 409,
    "CONFLICT": 409,
    "VERSION_CONFLICT": 409,
    "ENV_UNAVAILABLE": 409,
    "NOT_SUPPORTED": 409,
    "TEMPLATE_NOT_READY": 409,
    "PLACEMENT_CONFLICT": 409,
    "RESOURCE_BUSY": 409,
    "RUNNER_UNAVAILABLE": 503,
}


def remote_error(error: RemoteError | RunnerError) -> ApiError:
    return ApiError(_STATUS.get(error.code, 409), error.code, error.message)


def remote_of(service: LlmBridgeService, redis: Redis) -> RemoteContainers:
    return RemoteContainers(service.db, service.settings, service.runtime, redis)


async def container_out(
    remote: RemoteContainers, row: LlmBridgeRemoteContainer
) -> LlmBridgeRemoteContainerOut:
    env = await remote.env_of(row)
    template = await remote.template_of(row)
    state, reason = container_state(row, env, template)
    host = await remote.db.get(RunnerHost, row.host_id) if row.host_id else None
    settings = row.settings or {}
    network = settings.get("network")
    return LlmBridgeRemoteContainerOut(
        id=row.id,
        bridge_id=row.bridge_id,
        name=row.name,
        host_id=row.host_id,
        host_name=host.name if host else None,
        image_kind=row.image_kind,
        image=row.image,
        state=state,
        state_reason=reason,
        runner_environment_id=row.runner_environment_id,
        cpus=float(settings.get("cpus") or 0),
        memory_mb=int(settings.get("memory_mb") or 0),
        disk_gb=int(settings.get("disk_gb") or 0),
        runtime=str(settings.get("runtime") or "gvisor"),
        network=network if network in ("models", "models_registries", "all") else "all",
        domains=list(settings.get("domains") or []),
        secrets=list(settings.get("secrets") or []),
        idle_stop_minutes=int(settings.get("idle_stop_minutes") or 0),
        cli_versions={str(k): str(v) for k, v in (row.cli_versions or {}).items()},
        versions_ok=dict(row.cli_versions or {}) == cfg.CLI_VERSIONS,
        updating_image=bool(settings.get("pending_image") or settings.get("recreating")),
        image_error=settings.get("image_error"),
        last_used_at=env.last_used_at if env is not None else None,
        created_at=row.created_at,
        version=row.version,
    )


def resources_of(body: LlmBridgeRemoteContainerIn) -> dict[str, float]:
    if body.preset == "custom" and body.resources is not None:
        return body.resources.model_dump()
    return {k: float(v) for k, v in cfg.RESOURCE_PRESETS[body.preset].items()}


@router.get(
    "/llm_bridge/remote/targets",
    summary="能放远程开发容器的容器服务器、资源预设与上限（admin）",
    responses=_read_errors,
)
async def llm_bridge_remote_targets(
    access: Admin, service: Service, redis: Redis_
) -> LlmBridgeRemoteTargets:
    remote = remote_of(service, redis)
    hosts = list(
        await service.db.scalars(
            select(RunnerHost)
            .where(RunnerHost.workspace_id == access.workspace.id)
            .order_by(RunnerHost.name)
        )
    )
    out = []
    for host in hosts:
        reason = None
        if host.backend != "docker":
            reason = "Kubernetes 类的容器服务器暂时不能放远程开发容器"
        elif host.status != "online":
            reason = "容器服务器不在线"
        out.append(
            LlmBridgeRemoteHostOut(
                id=host.id,
                name=host.name,
                status=host.status,
                available=reason is None,
                reason=reason,
                cpus=float(host.cpus) if host.cpus else None,
                memory_mb=int(host.mem_bytes // (1024 * 1024)) if host.mem_bytes else None,
                disk_gb=int(host.disk_free_bytes // (1024**3)) if host.disk_free_bytes else None,
                runtimes=list(host.runtimes or []),
            )
        )
    return LlmBridgeRemoteTargets(
        hosts=out,
        presets={
            k: RemoteResources(**{key: v[key] for key in ("cpus", "memory_mb", "disk_gb")})
            for k, v in cfg.RESOURCE_PRESETS.items()
        },
        limits={k: float(v) for k, v in cfg.LIMITS.items()},
        used=await remote.usage(access.workspace.id),
        versions=dict(cfg.CLI_VERSIONS),
        network_domains={**cfg.PLATFORM_DOMAINS, "registries": list(cfg.REGISTRY_DOMAINS)},
    )


@router.get(
    "/llm_bridge/bridges/{bridge_id}/containers",
    summary="这个管理器的远程开发容器（admin）",
    responses=_read_errors,
)
async def llm_bridge_list_containers(
    access: Admin, service: Service, redis: Redis_, bridge_id: UUID
) -> LlmBridgeRemoteContainerList:
    row = await service.bridge(access.workspace.id, bridge_id)
    remote = remote_of(service, redis)
    rows = list(
        await service.db.scalars(
            select(LlmBridgeRemoteContainer)
            .where(
                LlmBridgeRemoteContainer.workspace_id == access.workspace.id,
                LlmBridgeRemoteContainer.bridge_id == row.id,
            )
            .order_by(LlmBridgeRemoteContainer.created_at)
        )
    )
    return LlmBridgeRemoteContainerList(containers=[await container_out(remote, r) for r in rows])


async def create_container(
    access: Owner,
    service: LlmBridgeService,
    redis: Redis,
    bridge_id: UUID,
    body: LlmBridgeRemoteContainerIn,
    repo_hosts: list[str] | None = None,
) -> LlmBridgeRemoteContainer:
    """建远程容器（不提交）：建环境时一起建也走这里。"""
    bridge = await service.bridge(access.workspace.id, bridge_id)
    remote = remote_of(service, redis)
    try:
        row = await remote.create(
            access.workspace.id,
            bridge,
            name=body.name.strip(),
            host_id=body.host_id,
            image_kind=body.image_kind,
            image=(body.image or "").strip() or None,
            template_name=(body.image or "").strip() or None,
            resources=resources_of(body),
            runtime=body.runtime,
            network=body.network,
            domains=[d.strip().lower() for d in body.domains if d.strip()],
            secrets=list(body.secrets),
            idle_stop_minutes=body.idle_stop_minutes,
            repo_hosts=repo_hosts or [],
            actor_id=access.user_id,
        )
    except (RemoteError, RunnerError) as error:
        raise remote_error(error) from None
    await service.audit(
        access,
        "remote_container_created",
        bridge.id,
        {
            "bridge": bridge.name,
            "container": row.name,
            "host": str(body.host_id),
            "image_kind": body.image_kind,
            "runtime": body.runtime,
            "network": body.network,
        },
    )
    return row


@router.post(
    "/llm_bridge/bridges/{bridge_id}/containers",
    summary="新建远程开发容器（owner）",
    description="先在容器服务器上构建镜像（第一次要几分钟），好了以后自动建容器；"
    "进度看列表里的 state。",
    status_code=status.HTTP_201_CREATED,
    dependencies=_write,
    responses=_write_errors,
)
async def llm_bridge_create_container(
    access: Owner,
    service: Service,
    redis: Redis_,
    bridge_id: UUID,
    body: LlmBridgeRemoteContainerIn,
) -> LlmBridgeRemoteContainerOut:
    row = await create_container(access, service, redis, bridge_id, body)
    await service.db.commit()
    return await container_out(remote_of(service, redis), row)


async def _container(
    access: Admin | Owner,
    service: LlmBridgeService,
    redis: Redis,
    bridge_id: UUID,
    container_id: UUID,
) -> tuple[RemoteContainers, LlmBridgeRemoteContainer]:
    bridge = await service.bridge(access.workspace.id, bridge_id)
    remote = remote_of(service, redis)
    try:
        row = await remote.get(access.workspace.id, bridge.id, container_id)
    except RemoteError as error:
        raise remote_error(error) from None
    return remote, row


@router.patch(
    "/llm_bridge/bridges/{bridge_id}/containers/{container_id}",
    summary="改远程开发容器的空闲停止和网络（owner）",
    description="网络在下一次启动或执行时生效。资源建好后不能改：要更大的就新建一个容器。",
    dependencies=_write,
    responses=_write_errors,
)
async def llm_bridge_update_container(
    access: Owner,
    service: Service,
    redis: Redis_,
    bridge_id: UUID,
    container_id: UUID,
    body: LlmBridgeRemoteContainerPatch,
) -> LlmBridgeRemoteContainerOut:
    remote, row = await _container(access, service, redis, bridge_id, container_id)
    if row.version != body.version:
        raise ApiError(409, "VERSION_CONFLICT", "容器设置已被修改，请刷新后重试")
    try:
        await remote.set_policy(
            row,
            idle_stop_minutes=body.idle_stop_minutes,
            network=body.network,
            domains=[d.strip().lower() for d in body.domains if d.strip()]
            if body.domains is not None
            else None,
        )
    except (RemoteError, RunnerError) as error:
        raise remote_error(error) from None
    await service.audit(
        access,
        "remote_container_updated",
        bridge_id,
        {"container": row.name, **body.model_dump(exclude={"version"}, exclude_none=True)},
    )
    await service.db.commit()
    return await container_out(remote, row)


@router.post(
    "/llm_bridge/bridges/{bridge_id}/containers/{container_id}/actions",
    summary="启动 / 停止 / 重试 / 更新镜像（owner）",
    dependencies=_write,
    responses=_write_errors,
)
async def llm_bridge_container_action(
    access: Owner,
    service: Service,
    redis: Redis_,
    bridge_id: UUID,
    container_id: UUID,
    body: LlmBridgeRemoteContainerAction,
) -> LlmBridgeRemoteContainerOut:
    remote, row = await _container(access, service, redis, bridge_id, container_id)
    if row.version != body.version:
        raise ApiError(409, "VERSION_CONFLICT", "容器状态已变化，请刷新后重试")
    try:
        await remote.action(row, body.action)
    except (RemoteError, RunnerError) as error:
        raise remote_error(error) from None
    await service.audit(
        access, f"remote_container_{body.action}", bridge_id, {"container": row.name}
    )
    await service.db.commit()
    return await container_out(remote, row)


@router.delete(
    "/llm_bridge/bridges/{bridge_id}/containers/{container_id}",
    summary="删除远程开发容器（owner）",
    description="连数据卷一起删、不做快照（里面有登录凭据）：会话的工作目录和容器里的登录都会没了。"
    "还有开发环境用它时 409，先删环境或改用别的容器。",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=_write,
    responses=_write_errors,
)
async def llm_bridge_delete_container(
    access: Owner,
    service: Service,
    redis: Redis_,
    bridge_id: UUID,
    container_id: UUID,
) -> Response:
    remote, row = await _container(access, service, redis, bridge_id, container_id)
    bridge = await service.bridge(access.workspace.id, bridge_id)
    data = await service.admin(bridge, "GET", "/environments")
    using = [
        e.get("name")
        for e in (data.get("environments") or [])
        if e.get("remote_container") == str(row.id)
    ]
    if using:
        raise ApiError(
            409,
            "CONTAINER_IN_USE",
            f"还有开发环境在用这个容器：{'、'.join(str(n) for n in using)}",
        )
    name = row.name
    container_key = str(row.id)
    try:
        await remote.delete(row)
    except (RemoteError, RunnerError) as error:
        raise remote_error(error) from None
    with contextlib.suppress(ApiError):  # 管理器忘掉这个容器里的登录记录（连不上也不影响删除）
        await service.admin(bridge, "DELETE", f"/remote/containers/{container_key}")
    await service.audit(access, "remote_container_deleted", bridge_id, {"container": name})
    await service.db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def environment_of_container(
    service: LlmBridgeService, redis: Redis, workspace_id: UUID, container_id: UUID
) -> RunnerEnvironment | None:
    row = await service.db.get(LlmBridgeRemoteContainer, container_id)
    if row is None or row.workspace_id != workspace_id:
        return None
    return await remote_of(service, redis).env_of(row)


SlotPath = Annotated[Literal["openai", "claude", "workbuddy"], Path(description="账号槽位")]


@router.get(
    "/llm_bridge/bridges/{bridge_id}/containers/{container_id}/platforms",
    summary="各平台在这个远程容器里能不能用（admin）",
    description="凭据随回合注入的（API 密钥、Claude 长期令牌）不用登录；"
    "会刷新的登录要在容器里登录一次（docs/v2/LLM_BRIDGE_REMOTE_ENV_PLAN.md 第 9 节）。",
    responses=_read_errors,
)
async def llm_bridge_container_platforms(
    access: Admin, service: Service, redis: Redis_, bridge_id: UUID, container_id: UUID
) -> LlmBridgeRemotePlatforms:
    _remote, row = await _container(access, service, redis, bridge_id, container_id)
    bridge = await service.bridge(access.workspace.id, bridge_id)
    data = await service.admin(bridge, "GET", f"/remote/containers/{row.id}")
    platforms = data.get("platforms") if isinstance(data, dict) else None
    out: dict[str, LlmBridgeRemotePlatformOut] = {}
    for slot, item in (platforms or {}).items():
        if not isinstance(item, dict) or item.get("state") not in (
            "injected",
            "ready",
            "needs_login",
            "mismatch",
        ):
            continue
        out[str(slot)] = LlmBridgeRemotePlatformOut(
            state=item["state"],
            detail=str(item.get("detail") or "")[:500],
            identity=str(item["identity"])[:200] if item.get("identity") else None,
            can_login=bool(item.get("can_login")),
        )
    return LlmBridgeRemotePlatforms(platforms=out)


@router.post(
    "/llm_bridge/bridges/{bridge_id}/containers/{container_id}/login/{slot}",
    summary="在远程容器里登录一个平台（owner）",
    description="action=start 开始（返回登录网址和验证码）；code 交回 Claude 授权页上的授权码；"
    "cancel 取消。登录后管理器核对是同一个账号。",
    dependencies=_write,
    responses=_write_errors,
)
async def llm_bridge_container_login(
    access: Owner,
    service: Service,
    redis: Redis_,
    bridge_id: UUID,
    container_id: UUID,
    slot: SlotPath,
    body: LlmBridgeRemoteLoginIn,
) -> LlmBridgeLoginOut:
    _remote, row = await _container(access, service, redis, bridge_id, container_id)
    bridge = await service.bridge(access.workspace.id, bridge_id)
    data = await service.admin(
        bridge,
        "POST",
        f"/remote/containers/{row.id}/login/{slot}",
        body.model_dump(exclude_none=True),
        read_timeout=LOGIN_READ_TIMEOUT,
    )
    await service.audit(
        access,
        "remote_login",
        bridge_id,
        {"container": row.name, "slot": slot, "action": body.action},
    )
    await service.db.commit()
    return login_out(data.get("login") if isinstance(data, dict) else None)


@router.get(
    "/llm_bridge/bridges/{bridge_id}/containers/{container_id}/login/{slot}",
    summary="远程容器里登录的进度（admin）",
    responses=_read_errors,
)
async def llm_bridge_container_login_state(
    access: Admin,
    service: Service,
    redis: Redis_,
    bridge_id: UUID,
    container_id: UUID,
    slot: SlotPath,
) -> LlmBridgeLoginOut:
    _remote, row = await _container(access, service, redis, bridge_id, container_id)
    bridge = await service.bridge(access.workspace.id, bridge_id)
    data = await service.admin(bridge, "GET", f"/remote/containers/{row.id}/login/{slot}")
    return login_out(data.get("login") if isinstance(data, dict) else None)
