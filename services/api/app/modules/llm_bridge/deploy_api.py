"""页面上一键部署 MCP 管理器的接口（docs/v2/LLM_BRIDGE_DEPLOY_PLAN.md 第 6 节）。

登记在模块的 router 上（deps.py；宿主自动加启用检查）。开始部署、重新部署、重启、卸载只有 owner
能做（和两个服务器页面上的操作一样）；服务器、任务进度、某台服务器上的管理器，admin 也能看。
"""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import Depends, Query, Request, status

from app.api.deps import csrf_protected, get_agent_server_service
from app.domain.agent_servers import AgentServerService
from app.modules.llm_bridge.deploy import LlmBridgeDeployService
from app.modules.llm_bridge.deploy_schemas import (
    LlmBridgeDeployIn,
    LlmBridgeDeployJobList,
    LlmBridgeDeployJobOut,
    LlmBridgeDeployTargetList,
    LlmBridgeRedeployIn,
    LlmBridgeRestartIn,
    LlmBridgeServerManagerList,
    LlmBridgeUninstallIn,
)
from app.modules.llm_bridge.deps import Admin, Owner, router
from app.modules.llm_bridge.deps import Service as Bridges
from app.schemas.common import error_responses

write = [Depends(csrf_protected)]
read_errors = error_responses(401, 403, 404)
write_errors = error_responses(401, 403, 404, 409, 422, 503)


def get_deploys(
    request: Request,
    bridges: Bridges,
    servers: Annotated[AgentServerService, Depends(get_agent_server_service)],
) -> LlmBridgeDeployService:
    state = request.app.state
    return LlmBridgeDeployService(
        bridges, servers, getattr(state, "ssh_jobs", None), getattr(state, "sessionmaker", None)
    )


Deploys = Annotated[LlmBridgeDeployService, Depends(get_deploys)]


_SUDO = (
    "登录账号用 Docker 要 sudo 密码时带 sudo_password（只用这一次，不保存、不进日志）；"
    "需要却没带：409 SUDO_PASSWORD_REQUIRED。"
)


@router.get(
    "/llm_bridge/deployments/targets",
    summary="一键部署：可以部署到哪些服务器（admin）",
    description="智能体服务器和容器服务器两类；不能部署的带上原因。只读工作台里的登记，不连服务器。"
    "部署 MCP 管理器不看这两类服务器自己的准入（资源、配额、正在进行的任务）。",
    responses=read_errors,
)
async def llm_bridge_deploy_targets(access: Admin, deploys: Deploys) -> LlmBridgeDeployTargetList:
    return await deploys.targets(access)


@router.get(
    "/llm_bridge/deployments",
    summary="一键部署：最近的部署任务（admin）",
    description="available 为 false 时这个工作台不能一键部署（镜像里没有 MCP 管理器的构建文件）。",
    responses=read_errors,
)
async def llm_bridge_list_deployments(access: Admin, deploys: Deploys) -> LlmBridgeDeployJobList:
    return await deploys.jobs(access)


@router.post(
    "/llm_bridge/deployments",
    summary="一键部署：把一个 MCP 管理器部署到智能体服务器或容器服务器上并登记（owner）",
    description="立刻返回任务，部署在后台进行，进度用任务接口读。"
    "和工作台同一台服务器：加入工作台的内部网络；其他服务器：只监听那台服务器的本机地址，"
    "工作台经专用的 SSH 通道访问。登录账号用 Docker 要 sudo 密码却没带 sudo_password 时，"
    "任务在“检查”一步失败（error_code=sudo_password_required），带上密码重新提交。"
    "409 DEPLOY_BLOCKED：工作台现在登录不上这台服务器；409 DEPLOY_BUSY：这台服务器上有 MCP 管理器的"
    "任务在进行；503 DEPLOY_UNAVAILABLE：这个工作台不能一键部署。",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=write,
    responses=write_errors,
)
async def llm_bridge_deploy(
    access: Owner, deploys: Deploys, body: LlmBridgeDeployIn
) -> LlmBridgeDeployJobOut:
    return await deploys.deploy(access, body)


@router.get(
    "/llm_bridge/deployments/{job_id}",
    summary="一键部署：一个任务的步骤和日志（admin）",
    responses=read_errors,
)
async def llm_bridge_get_deployment(
    access: Admin, deploys: Deploys, job_id: UUID
) -> LlmBridgeDeployJobOut:
    return await deploys.get(access, job_id)


@router.get(
    "/llm_bridge/installs",
    summary="某台服务器上的 MCP 管理器（admin）",
    description="智能体服务器页面和容器服务器页面的“MCP 管理器”入口用：这台服务器上由工作台部署的"
    "管理器，带现在连不连得上、已登录的账号。这些容器不受那两个页面的管理程序影响。",
    responses=read_errors,
)
async def llm_bridge_installs(
    access: Admin,
    deploys: Deploys,
    target_kind: Annotated[Literal["agent_server", "runner_host"], Query()],
    target_id: Annotated[UUID, Query()],
) -> LlmBridgeServerManagerList:
    return await deploys.managers_on(access, target_kind, target_id)


@router.post(
    "/llm_bridge/bridges/{bridge_id}/redeploy",
    summary="一键部署：重新部署（升级）工作台部署的 MCP 管理器（owner）",
    description="同一个目录、同一枚令牌、同一个数据卷、同一条通道：登录状态和会话记录保留。"
    + _SUDO
    + "409 NOT_DEPLOYED_HERE：这个管理器不是工作台部署的；"
    "409 SERVER_REMOVED：那台服务器已经从工作台移除。",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=write,
    responses=write_errors,
)
async def llm_bridge_redeploy(
    access: Owner, deploys: Deploys, bridge_id: UUID, body: LlmBridgeRedeployIn
) -> LlmBridgeDeployJobOut:
    return await deploys.redeploy(access, bridge_id, body)


@router.post(
    "/llm_bridge/bridges/{bridge_id}/restart",
    summary="一键部署：重启工作台部署的 MCP 管理器（owner）",
    description="docker compose restart，再确认工作台连得上。进行中的回合会中断。" + _SUDO,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=write,
    responses=write_errors,
)
async def llm_bridge_restart(
    access: Owner, deploys: Deploys, bridge_id: UUID, body: LlmBridgeRestartIn
) -> LlmBridgeDeployJobOut:
    return await deploys.restart(access, bridge_id, body)


@router.post(
    "/llm_bridge/bridges/{bridge_id}/uninstall",
    summary="一键部署：卸载工作台部署的 MCP 管理器并去掉登记（owner）",
    description="停掉并删除容器、删除部署目录和转发密钥、去掉登记；"
    "数据卷默认保留，remove_data=true 时一起删除。"
    "force=true：不连服务器，只删除工作台里的登记（服务器连不上或已移除时用），"
    "返回的任务里写明服务器上还留着什么。" + _SUDO,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=write,
    responses=write_errors,
)
async def llm_bridge_uninstall(
    access: Owner, deploys: Deploys, bridge_id: UUID, body: LlmBridgeUninstallIn
) -> LlmBridgeDeployJobOut:
    return await deploys.uninstall(access, bridge_id, body)
