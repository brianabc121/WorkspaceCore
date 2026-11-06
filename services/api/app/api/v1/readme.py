"""项目与任务的 README（T-0122，docs/v2/AGENT_MESSAGING_PLAN.md 第 9 节）。

README 是项目知识库里的一篇 readme 笔记：读这里，改用知识库的笔记接口
（`PATCH /knowledge/notes/{id}`）。
还没有时当场按模板生成（迁移前已有的项目和任务也由 Worker 补建）。
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Security
from sqlalchemy import select

from app.api.deps import authenticated, get_knowledge_base_service, workspace_viewer
from app.domain import readme_text
from app.domain.knowledge_views import KnowledgeService
from app.domain.policy import WorkspaceAccess, not_found
from app.domain.readme import Readmes
from app.models import KnowledgeNote, Project, Task
from app.schemas.common import error_responses
from app.schemas.knowledge_base import ReadmeOut

router = APIRouter(tags=["readme"], dependencies=[Security(authenticated)])

Viewer = Annotated[WorkspaceAccess, Depends(workspace_viewer)]
Service = Annotated[KnowledgeService, Depends(get_knowledge_base_service)]
_errors = error_responses(401, 404, 422)


async def _out(
    service: KnowledgeService, note: KnowledgeNote, project_id: UUID, task_id: UUID | None
) -> ReadmeOut:
    return ReadmeOut(
        note=await service.note_out(note),
        project_id=project_id,
        task_id=task_id,
        auto_sections=readme_text.auto_names(note.body),
    )


@router.get(
    "/projects/{project_id}/readme",
    summary="项目 README（viewer）",
    description="项目知识库里置顶的导览；还没有就按模板生成。"
    "自动部分随项目数据更新（1 分钟合并一次）。",
    responses=_errors,
)
async def project_readme(access: Viewer, service: Service, project_id: UUID) -> ReadmeOut:
    project = await service.db.scalar(
        select(Project).where(Project.id == project_id, Project.workspace_id == access.workspace.id)
    )
    if project is None:
        raise not_found()
    note = await Readmes(service.db, service.settings).ensure_project(project)
    await service.db.commit()
    return await _out(service, note, project.id, None)


@router.get(
    "/tasks/{task_id}/readme",
    summary="任务 README（viewer）",
    description="项目知识库“任务导览”里这个任务的导览；还没有就按模板生成。",
    responses=_errors,
)
async def task_readme(access: Viewer, service: Service, task_id: UUID) -> ReadmeOut:
    task = await service.db.scalar(
        select(Task).where(Task.id == task_id, Task.workspace_id == access.workspace.id)
    )
    if task is None:
        raise not_found()
    note = await Readmes(service.db, service.settings).ensure_task(task)
    await service.db.commit()
    return await _out(service, note, task.project_id, task.id)
