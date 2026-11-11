"""业务接口 /api/v1。各领域的路由在这里汇总。"""

from fastapi import APIRouter, Depends

from app.api.v1 import (
    agent_model_bindings,
    agent_resources,
    agents,
    ai,
    audit,
    auth,
    calendar,
    dashboards,
    developer,
    events,
    im,
    integrations,
    issues,
    knowledge,
    knowledge_base,
    mail,
    me,
    model_catalog,
    model_connections,
    modules,
    navigation,
    notifications,
    operations,
    readme,
    runner,
    runner_capabilities,
    runner_network,
    search,
    setup,
    tasks,
    workspaces,
)
from app.modules import registry
from app.modules.deps import require_module
from app.schemas.common import error_responses

# 在路由层统一声明 422：否则带参数的接口在 OpenAPI 里会用 FastAPI 默认的
# {"detail": [...]} 描述校验错误，与实际返回的统一错误格式不符
router = APIRouter(prefix="/api/v1", responses=error_responses(422))
for module in (
    setup,
    auth,
    me,
    workspaces,
    audit,
    dashboards,
    integrations,
    tasks,
    readme,
    issues,
    knowledge,
    knowledge_base,
    calendar,
    developer,
    search,
    operations,
    notifications,
    events,
    agents,
    im,
    mail,
    runner,
    runner_capabilities,
    runner_network,
    ai,
    model_connections,
    model_catalog,
    agent_model_bindings,
    agent_resources,
    modules,
    navigation,
):
    router.include_router(module.router)

# 插拔式模块的页面接口（docs/modules/README.md）：每个路由都加上启用检查
for manifest, module_router in registry.routers():
    router.include_router(module_router, dependencies=[Depends(require_module(manifest.key))])
