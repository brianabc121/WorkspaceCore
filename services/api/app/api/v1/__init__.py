"""业务接口 /api/v1。各领域的路由在这里汇总。"""

from fastapi import APIRouter

from app.api.v1 import (
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
    news,
    notifications,
    operations,
    readme,
    runner,
    search,
    setup,
    tasks,
    workspaces,
)
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
    ai,
    news,
):
    router.include_router(module.router)
