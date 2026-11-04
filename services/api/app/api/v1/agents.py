"""OpenClaw 实例与密钥库的管理员接口（V2）：只给 owner。

约定见 docs/decisions/2026-10-31-v2-oc1-contract.md 第 3、4 节。实例凭证、客户端证书私钥
只在接入包（zip）里出现一次；密钥只写不读。实现：OC-2（T-0054）。
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response, Security, status
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    authenticated,
    client_ip,
    csrf_protected,
    get_agent_ops_service,
    get_agent_service,
    get_agent_ssh_service,
    get_db,
    get_redis,
    workspace_admin,
    workspace_owner,
)
from app.domain.agent_ops import AgentOpsService
from app.domain.agent_ssh import AgentSshService
from app.domain.agents import AgentService
from app.domain.auth import AuthContext
from app.domain.host_metrics import HostService
from app.domain.policy import WorkspaceAccess
from app.schemas.agents import (
    AgentInstanceCreate,
    AgentInstanceOut,
    AgentInstancePage,
    AgentInstanceUpdate,
    AgentOpsActionOut,
    AgentOpsRun,
    AgentOpsState,
    AgentSecretCreate,
    AgentSecretOut,
    AgentSecretPage,
    AgentSecretRotate,
    AgentSecretUpdate,
    AgentSettingsOut,
    AgentSettingsUpdate,
    AgentSshConfirm,
    AgentSshInstall,
    AgentSshRevoke,
    AgentSshState,
    AgentSshTargetSet,
    AgentSshUninstall,
    BundleRequest,
    ProjectAdminOut,
    ProjectAdminPut,
    SecretAccessPage,
    SecretAccessResult,
    SecretGrantsReplace,
    VersionBody,
)
from app.schemas.common import error_responses
from app.schemas.host import HostRange, HostStatusOut, HostSummaryList

router = APIRouter(tags=["agents"], dependencies=[Security(authenticated)])

Owner = Annotated[WorkspaceAccess, Depends(workspace_owner)]
Admin = Annotated[WorkspaceAccess, Depends(workspace_admin)]
Service = Annotated[AgentService, Depends(get_agent_service)]
SshService = Annotated[AgentSshService, Depends(get_agent_ssh_service)]
OpsService = Annotated[AgentOpsService, Depends(get_agent_ops_service)]
Cursor = Annotated[str | None, Query(max_length=512, description="上一页返回的 next_cursor")]
Limit = Annotated[int, Query(ge=1, le=100)]
_write = [Depends(csrf_protected)]
_read_errors = error_responses(401, 403, 404, 422)
_write_errors = error_responses(401, 403, 404, 409, 422)
_im_errors = error_responses(401, 403, 404, 409, 422, 503)


def _ip(request: Request, service: AgentService) -> str:
    return client_ip(request, request.app.state.settings)


_ZIP = {
    "content": {"application/zip": {"schema": {"type": "string", "format": "binary"}}},
    "description": "接入包：myworkspace.env、openclaw.json 片段、取密钥小程序、启动包装脚本、"
    "定时器、CHECKLIST.md；外网模式另有客户端证书、私钥与隧道配置。"
    "凭证只在这里出现一次（Cache-Control: no-store）",
}


# ---------------------------------------------------------------- 全局总开关
# 固定路径写在 /agents/instances/{instance_id} 之前


@router.get("/agents/settings", summary="实例总开关（owner）", responses=_read_errors)
async def get_agent_settings(access: Owner, service: Service) -> AgentSettingsOut:
    return await service.get_settings(access)


@router.patch(
    "/agents/settings",
    summary="打开或关闭实例总开关（owner）",
    description="关闭后所有实例的 MCP、取密钥、IM 立即被拒绝，并强制下线 IM；"
    "凭证不吊销，重新打开即恢复。",
    dependencies=_write,
    responses=_im_errors,
)
async def update_agent_settings(
    access: Owner, request: Request, service: Service, body: AgentSettingsUpdate
) -> AgentSettingsOut:
    return await service.update_settings(access, body, client_ip=_ip(request, service))


# ---------------------------------------------------------------- 实例


@router.get("/agents/instances", summary="实例列表（owner）", responses=_read_errors)
async def list_agent_instances(
    access: Owner, service: Service, cursor: Cursor = None, limit: Limit = 50
) -> AgentInstancePage:
    return await service.list_instances(access, cursor=cursor, limit=limit)


@router.post(
    "/agents/instances",
    status_code=status.HTTP_201_CREATED,
    summary="登记实例（owner）",
    description="同一个事务里写入实例；随后注册 OpenIM 用户并与 owner / admin 互加好友，"
    "失败时 im_ready 为 false，后台重试。不签发凭证：凭证在接入包里。",
    dependencies=_write,
    responses=_write_errors,
)
async def create_agent_instance(
    access: Owner, request: Request, service: Service, body: AgentInstanceCreate
) -> AgentInstanceOut:
    return await service.create_instance(access, body, client_ip=_ip(request, service))


@router.get("/agents/instances/{instance_id}", summary="实例详情（owner）", responses=_read_errors)
async def get_agent_instance(
    access: Owner, service: Service, instance_id: UUID
) -> AgentInstanceOut:
    return await service.get_instance(access, instance_id)


@router.patch(
    "/agents/instances/{instance_id}",
    summary="修改实例（owner，乐观并发）",
    dependencies=_write,
    responses=_im_errors,
)
async def update_agent_instance(
    access: Owner, request: Request, service: Service, instance_id: UUID, body: AgentInstanceUpdate
) -> AgentInstanceOut:
    return await service.update_instance(access, instance_id, body, client_ip=_ip(request, service))


@router.get(
    "/agents/host-summary",
    summary="各实例服务器状态的小指示（admin）",
    description="CPU、内存、磁盘的当前使用率与正在提醒的告警；"
    "没上报过的实例各项为 null（T-0113）。",
    responses=_read_errors,
)
async def host_summary(
    access: Admin,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> HostSummaryList:
    return await HostService(db, redis).summary(access)


@router.get(
    "/agents/instances/{instance_id}/host",
    summary="实例的服务器状态（admin）",
    description="当前值（超过 3 分钟没上报为 stale）与每小时的平均、峰值（24h / 7d / 30d）；"
    "数值是整台服务器的（容器用 host 网络读宿主机的 /proc，T-0113）。",
    responses=_read_errors,
)
async def host_status(
    access: Admin,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],
    instance_id: UUID,
    span: Annotated[HostRange, Query(alias="range")] = "24h",
) -> HostStatusOut:
    return await HostService(db, redis).status(access, instance_id, span)


@router.put(
    "/agents/instances/{instance_id}/project-admin",
    summary="打开或关闭实例的项目管理权限（admin，乐观并发）",
    description="打开后这个实例的工具里多出 create_project / add_to_project / list_peers，"
    "直接执行、不审批（人类 2026-11-04 决定）；它建的项目群的群主是打开权限的人。"
    "关闭后它已经建的项目和拉进来的成员保留，只是不能再建、再拉。每次都写审计（T-0112）。",
    dependencies=_write,
    responses=_write_errors,
)
async def put_project_admin(
    access: Admin, request: Request, service: Service, instance_id: UUID, body: ProjectAdminPut
) -> ProjectAdminOut:
    return await service.set_project_admin(
        access, instance_id, body, client_ip=_ip(request, service)
    )


@router.post(
    "/agents/instances/{instance_id}/disable",
    summary="停用实例（owner）",
    description="凭证立即失效、强制退出 IM、拒绝取密钥。",
    dependencies=_write,
    responses=_im_errors,
)
async def disable_agent_instance(
    access: Owner, request: Request, service: Service, instance_id: UUID, body: VersionBody
) -> AgentInstanceOut:
    return await service.set_enabled(
        access, instance_id, enabled=False, version=body.version, client_ip=_ip(request, service)
    )


@router.post(
    "/agents/instances/{instance_id}/enable",
    summary="启用实例（owner）",
    dependencies=_write,
    responses=_write_errors,
)
async def enable_agent_instance(
    access: Owner, request: Request, service: Service, instance_id: UUID, body: VersionBody
) -> AgentInstanceOut:
    return await service.set_enabled(
        access, instance_id, enabled=True, version=body.version, client_ip=_ip(request, service)
    )


@router.post(
    "/agents/instances/{instance_id}/bundle",
    summary="生成接入包并签发新凭证（owner）",
    description="每次调用都签发新凭证（外网模式另签发客户端证书）。"
    "previous=keep_24h 时旧凭证再保留 24 小时；同一种凭证同时最多两份有效，"
    "超出时最旧的一份立即吊销。已停用的实例返回 409 AGENT_DISABLED。",
    dependencies=_write,
    responses={200: _ZIP, **_write_errors},
    response_model=None,
)
async def issue_agent_bundle(
    access: Owner, request: Request, service: Service, instance_id: UUID, body: BundleRequest
) -> Response:
    filename, data = await service.issue_bundle(
        access, instance_id, body, client_ip=_ip(request, service)
    )
    return Response(
        content=data,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


# ---------------------------------------------------------------- SSH 自动部署（T-0089）


@router.get(
    "/agents/instances/{instance_id}/ssh",
    summary="SSH 自动部署的目标服务器（owner）",
    responses=_read_errors,
)
async def get_agent_ssh(access: Owner, service: SshService, instance_id: UUID) -> AgentSshState:
    return await service.state(access, instance_id)


@router.put(
    "/agents/instances/{instance_id}/ssh",
    summary="填写或更换目标服务器，读取主机公钥（owner）",
    description="解析地址（拒绝回环、链路本地、组播等地址），连到 SSH 端口只读取主机公钥、不登录，"
    "返回 SHA256 指纹；owner 核对后调用 /ssh/confirm 确认。只支持外网模式的实例。"
    "连不上时 503 AGENT_SSH_UNREACHABLE（details.reason：dns / timeout / refused / unreachable / "
    "handshake）；地址不允许时 422 AGENT_SSH_ADDRESS_NOT_ALLOWED；已经装了专用公钥时 409 "
    "AGENT_SSH_KEY_INSTALLED。每个用户每分钟最多探测 10 次。",
    dependencies=_write,
    responses=error_responses(401, 403, 404, 409, 422, 429, 503),
)
async def set_agent_ssh(
    access: Owner,
    request: Request,
    service: SshService,
    instance_id: UUID,
    body: AgentSshTargetSet,
) -> AgentSshState:
    return await service.set_target(
        access, instance_id, body, client_ip=client_ip(request, request.app.state.settings)
    )


@router.post(
    "/agents/instances/{instance_id}/ssh/confirm",
    summary="确认主机公钥的指纹（owner）",
    description="指纹必须与探测时读到的一致（409 AGENT_SSH_FINGERPRINT_MISMATCH）；"
    "确认后，之后的连接只接受这把主机公钥。",
    dependencies=_write,
    responses=_write_errors,
)
async def confirm_agent_ssh(
    access: Owner,
    request: Request,
    service: SshService,
    instance_id: UUID,
    body: AgentSshConfirm,
) -> AgentSshState:
    return await service.confirm(
        access, instance_id, body, client_ip=client_ip(request, request.app.state.settings)
    )


@router.post(
    "/agents/instances/{instance_id}/ssh/install",
    status_code=status.HTTP_202_ACCEPTED,
    summary="开始自动安装 OpenClaw（owner）",
    description="主机公钥已确认、外网接入入口已配置、实例启用且是外网模式时，新建安装任务并立即返回；"
    "进度看 GET /ssh 的 job（steps、脱敏后的 log）。登录密码只用于这一次（登录、装专用公钥、"
    "需要时用 sudo 装 Docker），不保存；已经装了专用公钥、且 Docker 可以直接使用时可以不填。"
    "主机公钥未确认时 409 AGENT_SSH_HOST_KEY_UNCONFIRMED；已经有进行中的任务时 409 "
    "AGENT_SSH_JOB_RUNNING；外网入口未配置时 409 AGENT_INGRESS_NOT_CONFIGURED。",
    dependencies=_write,
    responses=error_responses(401, 403, 404, 409, 422, 503),
)
async def install_agent_ssh(
    access: Owner,
    request: Request,
    service: SshService,
    instance_id: UUID,
    body: AgentSshInstall,
) -> AgentSshState:
    return await service.install(
        access, instance_id, body, client_ip=client_ip(request, request.app.state.settings)
    )


@router.post(
    "/agents/instances/{instance_id}/ssh/uninstall",
    status_code=status.HTTP_202_ACCEPTED,
    summary="卸载服务器上的 OpenClaw（owner）",
    description="只用专用密钥登录：停止并删除容器、删掉工作台写入 crontab 的定时任务；"
    "remove_files=true 时同时删除接入包目录（含实例凭证与 OpenClaw 的会话、记忆，不可恢复）。"
    "不卸载 Docker，不吊销专用公钥与实例凭证。进度看 GET /ssh 的 job。"
    "没有专用公钥时 409 AGENT_SSH_KEY_MISSING；已经有进行中的任务时 409 AGENT_SSH_JOB_RUNNING。",
    dependencies=_write,
    responses=error_responses(401, 403, 404, 409, 422, 503),
)
async def uninstall_agent_ssh(
    access: Owner,
    request: Request,
    service: SshService,
    instance_id: UUID,
    body: AgentSshUninstall,
) -> AgentSshState:
    return await service.uninstall(
        access, instance_id, body, client_ip=client_ip(request, request.app.state.settings)
    )


@router.post(
    "/agents/instances/{instance_id}/ssh/revoke",
    summary="吊销专用密钥（owner）",
    description="用专用密钥登录，从服务器的 ~/.ssh/authorized_keys 删掉这把公钥，"
    "再删除工作台保存的私钥；之后可以更换或删除目标服务器。"
    "连不上服务器时 503 AGENT_SSH_UNREACHABLE（details.reason）；force=true 时仍然删除"
    "工作台保存的私钥，返回的 warning 说明要手动删除的那一行公钥。",
    dependencies=_write,
    responses=error_responses(401, 403, 404, 409, 422, 503),
)
async def revoke_agent_ssh(
    access: Owner,
    request: Request,
    service: SshService,
    instance_id: UUID,
    body: AgentSshRevoke,
) -> AgentSshState:
    return await service.revoke(
        access, instance_id, body, client_ip=client_ip(request, request.app.state.settings)
    )


@router.delete(
    "/agents/instances/{instance_id}/ssh",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="删除目标服务器（owner）",
    description="已经装了工作台的专用公钥时要先吊销（409 AGENT_SSH_KEY_INSTALLED）。",
    dependencies=_write,
    responses=_write_errors,
)
async def delete_agent_ssh(
    access: Owner,
    request: Request,
    service: SshService,
    instance_id: UUID,
    version: Annotated[int, Query(ge=1)],
) -> None:
    await service.remove(
        access,
        instance_id,
        version=version,
        client_ip=client_ip(request, request.app.state.settings),
    )


@router.post(
    "/agents/instances/{instance_id}/credentials/{credential_id}/revoke",
    summary="立即吊销一份凭证（owner）",
    dependencies=_write,
    responses=_write_errors,
)
async def revoke_agent_credential(
    access: Owner,
    request: Request,
    service: Service,
    instance_id: UUID,
    credential_id: UUID,
    body: VersionBody,
) -> AgentInstanceOut:
    return await service.revoke_credential(
        access,
        instance_id,
        credential_id,
        version=body.version,
        client_ip=_ip(request, service),
    )


@router.put(
    "/agents/instances/{instance_id}/secret-grants",
    summary="设置实例可以取用的密钥（owner，整体替换）",
    dependencies=_write,
    responses=_write_errors,
)
async def replace_agent_secret_grants(
    access: Owner, request: Request, service: Service, instance_id: UUID, body: SecretGrantsReplace
) -> AgentInstanceOut:
    return await service.replace_grants(access, instance_id, body, client_ip=_ip(request, service))


# ---------------------------------------------------------------- 密钥库


@router.get("/agents/secrets", summary="密钥列表（owner，不含明文）", responses=_read_errors)
async def list_agent_secrets(
    access: Owner, service: Service, cursor: Cursor = None, limit: Limit = 50
) -> AgentSecretPage:
    return await service.list_secrets(access, cursor=cursor, limit=limit)


@router.post(
    "/agents/secrets",
    status_code=status.HTTP_201_CREATED,
    summary="新增密钥（owner，只写不读）",
    dependencies=_write,
    responses=_write_errors,
)
async def create_agent_secret(
    access: Owner, request: Request, service: Service, body: AgentSecretCreate
) -> AgentSecretOut:
    return await service.create_secret(access, body, client_ip=_ip(request, service))


@router.get(
    "/agents/secrets/accesses",
    summary="取密钥记录（owner）",
    responses=_read_errors,
)
async def list_secret_accesses(
    access: Owner,
    service: Service,
    instance_id: UUID | None = None,
    secret_id: UUID | None = None,
    result: SecretAccessResult | None = None,
    cursor: Cursor = None,
    limit: Limit = 50,
) -> SecretAccessPage:
    return await service.list_accesses(
        access,
        instance_id=instance_id,
        secret_id=secret_id,
        result=result,
        cursor=cursor,
        limit=limit,
    )


@router.patch(
    "/agents/secrets/{secret_id}",
    summary="修改密钥的名称与说明（owner）",
    dependencies=_write,
    responses=_write_errors,
)
async def update_agent_secret(
    access: Owner, request: Request, service: Service, secret_id: UUID, body: AgentSecretUpdate
) -> AgentSecretOut:
    return await service.update_secret(access, secret_id, body, client_ip=_ip(request, service))


@router.post(
    "/agents/secrets/{secret_id}/rotate",
    summary="轮换密钥的值（owner）",
    description="新值立即生效，旧值不再下发；实例最迟在下一次 secrets reload（每 10 分钟）时换上。",
    dependencies=_write,
    responses=_write_errors,
)
async def rotate_agent_secret(
    access: Owner, request: Request, service: Service, secret_id: UUID, body: AgentSecretRotate
) -> AgentSecretOut:
    return await service.rotate_secret(access, secret_id, body, client_ip=_ip(request, service))


@router.delete(
    "/agents/secrets/{secret_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="删除密钥（owner）",
    description="同时删除对它的授权；之后实例取用返回 NOT_FOUND。",
    dependencies=_write,
    responses=_write_errors,
)
async def delete_agent_secret(
    access: Owner,
    request: Request,
    service: Service,
    secret_id: UUID,
    version: Annotated[int, Query(ge=1)],
) -> None:
    await service.delete_secret(access, secret_id, version=version, client_ip=_ip(request, service))


# ---------------------------------------------------------------- 远程运维动作（T-0114）


@router.get(
    "/agents/instances/{instance_id}/ops",
    summary="运维动作：是否可用、进行中的动作与最近记录（admin）",
    description="只适用于用 SSH 部署过、专用密钥还在的实例（available）；"
    "最近 20 次（7 天内），列表不含输出，输出看单条记录（T-0114）。",
    responses=_read_errors,
)
async def get_agent_ops(access: Admin, service: OpsService, instance_id: UUID) -> AgentOpsState:
    return await service.state(access, instance_id)


@router.post(
    "/agents/instances/{instance_id}/ops",
    status_code=status.HTTP_202_ACCEPTED,
    summary="执行一个固定的运维动作（admin）",
    description="动作是写死的：status 查看状态、logs 查看日志"
    "（最近 200 行，可选 openclaw / tunnel / image-proxy）、restart 重启 OpenClaw、"
    "up 重新拉起全部服务、prune 清理无用镜像、"
    "report 立即上报一次。restart / up / prune 要带自己的登录密码（没带 403 STEP_UP_REQUIRED，"
    "错误 403 STEP_UP_FAILED，错太多次 429）。同一实例同时只跑一个动作（409 AGENT_OPS_RUNNING），"
    "正在部署或卸载时 409 AGENT_SSH_JOB_RUNNING；没用 SSH 部署过 409 AGENT_OPS_UNAVAILABLE。"
    "立即返回 running 的记录，进度与输出看 GET /ops/{action_id}；超时 60 秒，up 是 180 秒。"
    "输出脱敏后保存 7 天；审计记下动作与退出码，不记输出。",
    dependencies=_write,
    responses=error_responses(401, 403, 404, 409, 422, 429, 503),
)
async def run_agent_ops(
    access: Admin,
    context: Annotated[AuthContext, Security(authenticated)],
    request: Request,
    service: OpsService,
    instance_id: UUID,
    body: AgentOpsRun,
) -> AgentOpsActionOut:
    return await service.run(
        access,
        context.user,
        instance_id,
        body,
        client_ip=client_ip(request, request.app.state.settings),
    )


@router.get(
    "/agents/instances/{instance_id}/ops/{action_id}",
    summary="一次运维动作的状态与脱敏后的输出（admin）",
    responses=_read_errors,
)
async def get_agent_ops_action(
    access: Admin, service: OpsService, instance_id: UUID, action_id: UUID
) -> AgentOpsActionOut:
    return await service.get(access, instance_id, action_id)
