"""页面上一键部署 MCP 管理器的接口（docs/v2/LLM_BRIDGE_PLAN.md 第 20 节）。

登记在模块的 router 上（deps.py；宿主自动加启用检查）。开始部署、重新部署、卸载只有 owner 能做
（和智能体服务器上的操作一样）；看任务进度 owner 和 admin 都可以。
"""

from typing import Annotated
from uuid import UUID

from fastapi import Depends, Request, status

from app.api.deps import csrf_protected, get_agent_server_service
from app.domain.agent_servers import AgentServerService
from app.modules.llm_bridge.deploy import LlmBridgeDeployService
from app.modules.llm_bridge.deploy_schemas import (
    LlmBridgeDeployIn,
    LlmBridgeDeployJobList,
    LlmBridgeDeployJobOut,
    LlmBridgeRedeployIn,
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
    summary="一键部署：把一个 MCP 管理器部署到智能体服务器上并登记（owner）",
    description="立刻返回任务，部署在后台进行，进度用任务接口读。服务器要已经确认主机公钥、保存了密码并初始化过。"
    "409 DEPLOY_BUSY：这台服务器上有任务在进行；503 DEPLOY_UNAVAILABLE：这个工作台不能一键部署。",
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


@router.post(
    "/llm_bridge/bridges/{bridge_id}/redeploy",
    summary="一键部署：重新部署（升级）工作台部署的 MCP 管理器（owner）",
    description="同一个目录、同一枚令牌、同一个数据卷：登录状态和会话记录保留。"
    "409 NOT_DEPLOYED_HERE：这个管理器不是工作台部署的。",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=write,
    responses=write_errors,
)
async def llm_bridge_redeploy(
    access: Owner, deploys: Deploys, bridge_id: UUID, body: LlmBridgeRedeployIn
) -> LlmBridgeDeployJobOut:
    return await deploys.redeploy(access, bridge_id, body)


@router.post(
    "/llm_bridge/bridges/{bridge_id}/uninstall",
    summary="一键部署：卸载工作台部署的 MCP 管理器并去掉登记（owner）",
    description="停掉并删除容器、删除部署目录、去掉登记；"
    "数据卷默认保留，remove_data=true 时一起删除。",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=write,
    responses=write_errors,
)
async def llm_bridge_uninstall(
    access: Owner, deploys: Deploys, bridge_id: UUID, body: LlmBridgeUninstallIn
) -> LlmBridgeDeployJobOut:
    return await deploys.uninstall(access, bridge_id, body)
