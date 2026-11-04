"""知识库（T-0098，docs/v2/KNOWLEDGE_PLAN.md 第 5 节）。

工作空间范围，用 X-Workspace-Id 指定：viewer 可读；member 及以上编辑笔记；
owner / admin 管理知识库设置、参与的智能体、智能体的写入开关，以及彻底删除回收站里的笔记。
笔记的修改、删除、撤销用整数 version 做乐观并发
（409 VERSION_CONFLICT，details.current 是最新的笔记）。
"""

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, Security, status
from pydantic import AfterValidator

from app.api.deps import (
    authenticated,
    csrf_protected,
    get_knowledge_base_service,
    workspace_admin,
    workspace_member,
    workspace_viewer,
)
from app.core.errors import ApiError
from app.domain.auth import AuthContext
from app.domain.knowledge_base import Editor, NoteFields, SaveOptions
from app.domain.knowledge_views import KnowledgeService
from app.domain.policy import WorkspaceAccess, not_found
from app.models import AgentInstance, ProjectAgent
from app.schemas.common import error_responses
from app.schemas.knowledge_base import (
    AgentKnowledgeOut,
    ChangePage,
    GraphOut,
    NoteCreate,
    NoteOut,
    NotePage,
    NoteSuggestionList,
    NoteType,
    NoteUpdate,
    ParticipantPut,
    RevertRequest,
    RevisionDetail,
    RevisionList,
    SpaceActivity,
    SpaceList,
    SpaceOut,
    SpaceUpdate,
    TrashPage,
)

router = APIRouter(
    prefix="/knowledge", tags=["knowledge-base"], dependencies=[Security(authenticated)]
)

Viewer = Annotated[WorkspaceAccess, Depends(workspace_viewer)]
Member = Annotated[WorkspaceAccess, Depends(workspace_member)]
Admin = Annotated[WorkspaceAccess, Depends(workspace_admin)]
Context = Annotated[AuthContext, Security(authenticated)]
Service = Annotated[KnowledgeService, Depends(get_knowledge_base_service)]
Cursor = Annotated[str | None, Query(max_length=512, description="上一页返回的 next_cursor")]
Limit = Annotated[int, Query(ge=1, le=100)]
_write = [Depends(csrf_protected)]
_read_errors = error_responses(401, 404, 422)
_write_errors = error_responses(401, 403, 404, 409, 422)


def _editor(access: WorkspaceAccess, context: AuthContext) -> Editor:
    return Editor(
        workspace_id=access.workspace.id,
        user_id=context.user.id,
        client_ip=context.client_ip,
        user_name=context.user.display_name,
    )


def _fields(body: NoteUpdate | NoteCreate) -> NoteFields:
    data = body.model_dump(exclude_unset=True)
    return NoteFields(
        space_id=data.get("space_id"),
        title=data.get("title"),
        body=data.get("body"),
        type=data.get("type"),
        tags=data.get("tags"),
        folder=data.get("folder"),
        aliases=data.get("aliases"),
        review_at=data.get("review_at"),
        review_set="review_at" in data,
        pinned=data.get("pinned"),
    )


# ---------------------------------------------------------------- 知识库


@router.get("/spaces", summary="知识库列表（viewer）", responses=_read_errors)
async def list_spaces(access: Viewer, service: Service) -> SpaceList:
    await service.ensure_spaces(access.workspace.id)
    await service.db.commit()
    return SpaceList(items=await service.space_outs(await service.spaces(access.workspace.id)))


@router.get("/spaces/{space_id}", summary="知识库详情（viewer）", responses=_read_errors)
async def get_space(access: Viewer, service: Service, space_id: UUID) -> SpaceOut:
    state = await service.space(access.workspace.id, space_id)
    return (await service.space_outs([state]))[0]


@router.patch(
    "/spaces/{space_id}",
    summary="修改知识库设置（admin）",
    description="说明、智能体权限（公共库三种，智能体库 read / none）、智能体的写入开关、"
    "解除被自动暂停的删除。version 不一致时 409。",
    dependencies=_write,
    responses=_write_errors,
)
async def update_space(
    access: Admin, context: Context, service: Service, space_id: UUID, body: SpaceUpdate
) -> SpaceOut:
    state = await service.space(access.workspace.id, space_id)
    space = state.space
    if space.version != body.version:
        current = (await service.space_outs([state]))[0]
        raise ApiError(
            409,
            "VERSION_CONFLICT",
            "设置已被修改，请刷新后重试",
            details={"current_version": space.version, "current": current.model_dump(mode="json")},
        )
    changed: list[str] = []
    if body.description is not None:
        space.description = body.description
        changed.append("description")
    if body.agent_access is not None:
        allowed = {"public": {"read_write", "read", "none"}, "agent": {"read", "none"}}
        if body.agent_access not in allowed.get(space.kind, set()):
            raise ApiError(422, "VALIDATION_FAILED", "这个知识库不能设置这种智能体权限")
        space.agent_access = body.agent_access
        changed.append("agent_access")
    if body.writes_enabled is not None:
        if space.kind != "agent":
            raise ApiError(422, "VALIDATION_FAILED", "只有智能体知识库有写入开关")
        space.writes_enabled = body.writes_enabled
        changed.append("writes_enabled")
    if body.resume_deletes:
        space.deletes_paused_at = None
        changed.append("resume_deletes")
    space.version += 1
    await service.audit.record(
        "knowledge_space.updated",
        workspace_ids=[access.workspace.id],
        actor_id=context.user.id,
        resource_type="knowledge_space",
        resource_id=space.id,
        metadata={"fields": changed},
        client_ip=context.client_ip,
    )
    await service.db.commit()
    return (await service.space_outs([await service.space(access.workspace.id, space_id)]))[0]


@router.get(
    "/spaces/{space_id}/activity",
    summary="知识库近 8 周的修改次数（viewer）",
    description="按周（周一开始，UTC）统计人和智能体的新建、修改、改名、退回次数，最早的在前。",
    responses=_read_errors,
)
async def space_activity(access: Viewer, service: Service, space_id: UUID) -> SpaceActivity:
    await service.space(access.workspace.id, space_id)
    return await service.space_activity(access.workspace.id, space_id)


@router.put(
    "/spaces/{space_id}/agents/{instance_id}",
    summary="让智能体参与项目（admin）",
    description="只用于项目知识库：参与的智能体能读写（或只读）这个项目的知识库，并收到项目简报。",
    dependencies=_write,
    responses=_write_errors,
)
async def put_participant(
    access: Admin,
    context: Context,
    service: Service,
    space_id: UUID,
    instance_id: UUID,
    body: ParticipantPut,
) -> SpaceOut:
    state = await service.space(access.workspace.id, space_id)
    if state.space.kind != "project" or state.space.project_id is None:
        raise ApiError(422, "VALIDATION_FAILED", "只有项目知识库可以设置参与的智能体")
    instance = await service.db.get(AgentInstance, instance_id)
    if instance is None or instance.workspace_id != access.workspace.id:
        raise not_found()
    row = await service.db.get(ProjectAgent, (state.space.project_id, instance_id))
    if row is None:
        service.db.add(
            ProjectAgent(
                workspace_id=access.workspace.id,
                project_id=state.space.project_id,
                instance_id=instance_id,
                access=body.access,
                added_by=context.user.id,
            )
        )
    else:
        row.access = body.access
    await service.audit.record(
        "knowledge_space.agent_added",
        workspace_ids=[access.workspace.id],
        actor_id=context.user.id,
        resource_type="knowledge_space",
        resource_id=space_id,
        metadata={"instance_id": str(instance_id), "access": body.access},
        client_ip=context.client_ip,
    )
    await service.db.commit()
    return (await service.space_outs([await service.space(access.workspace.id, space_id)]))[0]


@router.delete(
    "/spaces/{space_id}/agents/{instance_id}",
    summary="移出参与项目的智能体（admin）",
    dependencies=_write,
    responses=_write_errors,
)
async def delete_participant(
    access: Admin, context: Context, service: Service, space_id: UUID, instance_id: UUID
) -> SpaceOut:
    state = await service.space(access.workspace.id, space_id)
    if state.space.project_id is not None:
        row = await service.db.get(ProjectAgent, (state.space.project_id, instance_id))
        if row is not None:
            await service.db.delete(row)
            await service.audit.record(
                "knowledge_space.agent_removed",
                workspace_ids=[access.workspace.id],
                actor_id=context.user.id,
                resource_type="knowledge_space",
                resource_id=space_id,
                metadata={"instance_id": str(instance_id)},
                client_ip=context.client_ip,
            )
            await service.db.commit()
    return (await service.space_outs([await service.space(access.workspace.id, space_id)]))[0]


# ---------------------------------------------------------------- 笔记


def _folder(value: str | None) -> str | None:
    return value.strip("/ ") if value is not None else None


@router.get("/notes", summary="笔记列表与搜索（viewer）", responses=_read_errors)
async def list_notes(
    access: Viewer,
    service: Service,
    space_id: UUID | None = None,
    folder: Annotated[str | None, Query(max_length=200), AfterValidator(_folder)] = None,
    type: NoteType | None = None,
    tag: Annotated[str | None, Query(max_length=40)] = None,
    author: Annotated[str | None, Query(pattern="^(user|agent)$")] = None,
    q: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
    cursor: Cursor = None,
    limit: Limit = 50,
) -> NotePage:
    return await service.list_notes(
        access.workspace.id,
        space_id=space_id,
        folder=folder,
        note_type=type,
        tag=tag,
        author=author,
        q=q,
        cursor=cursor,
        limit=limit,
    )


@router.get(
    "/notes/suggest",
    summary="[[ 补全：按标题或别名找笔记（viewer）",
    responses=_read_errors,
)
async def suggest_notes(
    access: Viewer,
    service: Service,
    q: Annotated[str, Query(min_length=1, max_length=100)],
    limit: Annotated[int, Query(ge=1, le=20)] = 10,
) -> NoteSuggestionList:
    return NoteSuggestionList(items=await service.suggest(access.workspace.id, q, limit=limit))


@router.post(
    "/notes",
    status_code=status.HTTP_201_CREATED,
    summary="新建笔记（member）",
    description="同一个知识库里标题不能重复（409 TITLE_TAKEN）；"
    "内容里有密钥会被拒绝（422 SECRET_DETECTED）。",
    dependencies=_write,
    responses=_write_errors,
)
async def create_note(
    access: Member, context: Context, service: Service, body: NoteCreate
) -> NoteOut:
    await service.ensure_spaces(access.workspace.id)
    note = await service.create(
        _editor(access, context), _fields(body), SaveOptions(reason=body.reason)
    )
    await service.db.commit()
    return await service.note_out(await service.note(access.workspace.id, note.id))


@router.get("/notes/{note_id}", summary="笔记详情（viewer）", responses=_read_errors)
async def get_note(access: Viewer, service: Service, note_id: UUID) -> NoteOut:
    return await service.note_out(await service.note(access.workspace.id, note_id))


@router.patch(
    "/notes/{note_id}",
    summary="修改笔记（member，乐观并发）",
    description="改了智能体写的内容时，reason 会写回它知识库里的《收到的纠正》。",
    dependencies=_write,
    responses=_write_errors,
)
async def update_note(
    access: Member, context: Context, service: Service, note_id: UUID, body: NoteUpdate
) -> NoteOut:
    note = await service.note(access.workspace.id, note_id, lock=True)
    await service.update(
        _editor(access, context), note, body.version, _fields(body), SaveOptions(reason=body.reason)
    )
    await service.db.commit()
    return await service.note_out(await service.note(access.workspace.id, note_id))


@router.delete(
    "/notes/{note_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="删除笔记：放进回收站，30 天后彻底删除（member，乐观并发）",
    dependencies=_write,
    responses=_write_errors,
)
async def delete_note(
    access: Member,
    context: Context,
    service: Service,
    note_id: UUID,
    version: Annotated[int, Query()],
    reason: Annotated[str, Query(max_length=500)] = "",
) -> None:
    note = await service.note(access.workspace.id, note_id, lock=True)
    await service.delete(_editor(access, context), note, version, SaveOptions(reason=reason))
    await service.db.commit()


@router.post(
    "/notes/{note_id}/restore",
    summary="从回收站恢复（member）",
    dependencies=_write,
    responses=_write_errors,
)
async def restore_note(
    access: Member, context: Context, service: Service, note_id: UUID
) -> NoteOut:
    note = await service.note(access.workspace.id, note_id, lock=True)
    await service.restore(_editor(access, context), note)
    await service.db.commit()
    return await service.note_out(await service.note(access.workspace.id, note_id))


@router.post(
    "/notes/{note_id}/revert",
    summary="退回到某一版（member，乐观并发）",
    description="生成一个新版本，不改写历史；退回的是智能体的改动时会写回它的《收到的纠正》。",
    dependencies=_write,
    responses=_write_errors,
)
async def revert_note(
    access: Member, context: Context, service: Service, note_id: UUID, body: RevertRequest
) -> NoteOut:
    note = await service.note(access.workspace.id, note_id, lock=True)
    await service.revert(_editor(access, context), note, body.version, body.to_version, body.reason)
    await service.db.commit()
    return await service.note_out(await service.note(access.workspace.id, note_id))


@router.get("/notes/{note_id}/revisions", summary="版本历史（viewer）", responses=_read_errors)
async def list_revisions(access: Viewer, service: Service, note_id: UUID) -> RevisionList:
    note = await service.note(access.workspace.id, note_id)
    return RevisionList(items=await service.revisions(note))


@router.get(
    "/notes/{note_id}/revisions/{version}",
    summary="某一版的全文与差异（viewer）",
    responses=_read_errors,
)
async def get_revision(
    access: Viewer, service: Service, note_id: UUID, version: int
) -> RevisionDetail:
    note = await service.note(access.workspace.id, note_id)
    return await service.revision_detail(note, version)


@router.get(
    "/notes/{note_id}/graph",
    summary="局部图谱：这篇笔记周围 1–2 层（viewer）",
    responses=_read_errors,
)
async def local_graph(
    access: Viewer,
    service: Service,
    note_id: UUID,
    depth: Annotated[int, Query(ge=1, le=2)] = 1,
) -> GraphOut:
    await service.note(access.workspace.id, note_id)
    return await service.graph(access.workspace.id, center=note_id, depth=depth)


# ---------------------------------------------------------------- 图谱、变更记录、回收站


@router.get(
    "/graph",
    summary="知识图谱（viewer）",
    description="笔记与 [[链接]]，以及链接到的任务、项目、书签；超过 2000 篇时截断（truncated）。",
    responses=_read_errors,
)
async def graph(
    access: Viewer,
    service: Service,
    space_id: Annotated[list[UUID] | None, Query(max_length=50)] = None,
    resources: bool = True,
) -> GraphOut:
    return await service.graph(access.workspace.id, space_ids=space_id, include_resources=resources)


@router.get(
    "/changes",
    summary="变更记录（viewer）",
    description="每次改动（谁、什么时候、改了什么、为什么）与保护记录（拦下的写入、自动暂停、限流）。",
    responses=_read_errors,
)
async def list_changes(
    access: Viewer,
    service: Service,
    space_id: UUID | None = None,
    instance_id: UUID | None = None,
    kind: Annotated[
        str | None, Query(pattern="^(create|update|delete|restore|revert|rename|guard)$")
    ] = None,
    cursor: Cursor = None,
    limit: Limit = 30,
) -> ChangePage:
    return await service.changes(
        access.workspace.id,
        space_id=space_id,
        instance_id=instance_id,
        kind=kind,
        cursor=cursor,
        limit=limit,
    )


@router.get("/trash", summary="回收站（viewer）", responses=_read_errors)
async def list_trash(
    access: Viewer, service: Service, cursor: Cursor = None, limit: Limit = 50
) -> TrashPage:
    return await service.trash(access.workspace.id, cursor=cursor, limit=limit)


@router.delete(
    "/trash/{note_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="彻底删除回收站里的笔记（admin，不可恢复）",
    dependencies=_write,
    responses=_write_errors,
)
async def purge_note(access: Admin, context: Context, service: Service, note_id: UUID) -> None:
    note = await service.note(access.workspace.id, note_id, lock=True)
    await service.purge(_editor(access, context), note)
    await service.db.commit()


# ---------------------------------------------------------------- 智能体知识库


@router.get(
    "/agents/{instance_id}",
    summary="智能体知识库：开关、近 30 天的指标、参与的项目（viewer）",
    responses=_read_errors,
)
async def agent_knowledge(access: Viewer, service: Service, instance_id: UUID) -> AgentKnowledgeOut:
    instance = await service.db.get(AgentInstance, instance_id)
    if instance is None or instance.workspace_id != access.workspace.id:
        raise not_found()
    result = await service.agent_overview(instance)
    await service.db.commit()
    return result


_ = date  # 供 OpenAPI 生成日期类型（NoteCreate.review_at）
