"""业务接口 /api/v1。各领域的路由在这里汇总。"""

from fastapi import APIRouter

from app.api.v1 import audit, auth, me, setup, workspaces

router = APIRouter(prefix="/api/v1")
for module in (setup, auth, me, workspaces, audit):
    router.include_router(module.router)
