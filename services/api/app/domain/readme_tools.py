"""智能体的 README 工具（T-0122，docs/v2/AGENT_MESSAGING_PLAN.md 9.6）。

- readme_get（只读）：给项目或任务，直接返回 README，不用先找笔记；还没有就当场按模板生成。
- readme_update（直接执行，docs/AGENTS.md 第 8 节例外五）：只改某一个非自动部分（替换或追加），
  经 KnowledgeBase.update 写入，所以仍要过项目成员与读写权限、写入开关、密钥拦截、限额与版本。
都只能用在自己参与的项目上；别的工作空间、没参与的项目一律当作不存在。
"""

import uuid
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy import select

from app.core.errors import ApiError
from app.domain import readme_text as rt
from app.domain.ai_tools import AiToolService, Requester, ToolCallError, validation_message
from app.domain.knowledge_base import Editor, KnowledgeBase, NoteFields, SaveOptions
from app.domain.readme import Readmes
from app.models import AgentInstance, KnowledgeNote, Project, Task
from app.schemas.ai_tools import (
    ReadmeDoc,
    ReadmeGetInput,
    ReadmeGetOutput,
    ReadmeRef,
    ReadmeTarget,
    ReadmeText,
    ReadmeUpdateInput,
    ReadmeUpdateOutput,
    ToolSpec,
)

_MISSING = "没有这个项目或任务，或者你没有参与这个项目"


def doc(note: KnowledgeNote) -> ReadmeDoc:
    return ReadmeDoc(
        note_id=note.id,
        version=note.version,
        auto_sections=rt.auto_names(note.body),
        untrusted=ReadmeText(title=note.title, body=note.body),
    )


def ref(note: KnowledgeNote | None) -> ReadmeRef | None:
    return ReadmeRef(note_id=note.id, version=note.version) if note is not None else None


async def readable(db: Any, settings: Any, instance: AgentInstance, project_id: uuid.UUID) -> bool:
    """这个实例能不能读这个项目的知识库（参与了这个项目）。"""
    kb = KnowledgeBase(db, settings)
    return project_id in await kb.participation(instance.id)


async def _target(
    service: AiToolService, requester: Requester, args: ReadmeTarget
) -> tuple[Project, Task | None]:
    db, workspace_id = service.db, requester.workspace.id
    task: Task | None = None
    if args.task_id is not None:
        task = await db.scalar(
            select(Task).where(Task.id == args.task_id, Task.workspace_id == workspace_id)
        )
        if task is None:
            raise ToolCallError("NOT_FOUND", _MISSING)
        project_id = task.project_id
    elif args.project_id is not None:
        project_id = args.project_id
    else:  # ReadmeTarget 已经保证填了一个
        raise ToolCallError("VALIDATION_FAILED", "project_id 和 task_id 填一个")
    project = await db.scalar(
        select(Project).where(Project.id == project_id, Project.workspace_id == workspace_id)
    )
    if project is None or not await readable(db, service.settings, requester.instance, project.id):
        raise ToolCallError("NOT_FOUND", _MISSING)
    return project, task


async def _note(readmes: Readmes, project: Project, task: Task | None) -> KnowledgeNote:
    return await (
        readmes.ensure_task(task) if task is not None else readmes.ensure_project(project)
    )


async def _get(
    service: AiToolService, requester: Requester, args: ReadmeGetInput
) -> tuple[BaseModel, str]:
    project, task = await _target(service, requester, args)
    note = await _note(Readmes(service.db, service.settings), project, task)
    out = ReadmeGetOutput(
        project_id=project.id,
        task_id=task.id if task else None,
        readme=doc(note),
        sections=rt.headings(note.body),
    )
    return out, "读任务 README" if task else "读项目 README"


async def _update(
    service: AiToolService, requester: Requester, args: ReadmeUpdateInput
) -> tuple[BaseModel, str]:
    project, task = await _target(service, requester, args)
    readmes = Readmes(service.db, service.settings)
    note = await _note(readmes, project, task)
    try:
        body = rt.edit_section(note.body, args.section, args.text, args.mode)
    except rt.SectionError as error:
        raise ToolCallError(error.code, error.message) from None
    if body == note.body:
        out = ReadmeUpdateOutput(
            status="unchanged", note_id=note.id, version=note.version, section=args.section
        )
        return out, f"README 没有变化：{args.section}"
    kb = KnowledgeBase(service.db, service.settings, service.redis)
    editor = Editor(workspace_id=requester.workspace.id, instance=requester.instance)
    try:
        saved = await kb.update(
            editor,
            note,
            note.version,
            NoteFields(body=body),
            SaveOptions(reason=f"readme_update：{args.section}", source_kind="task"),
        )
    except ApiError as error:
        if error.status_code == 404:
            raise ToolCallError("NOT_FOUND", _MISSING) from None
        raise ToolCallError(error.code, error.message) from None
    out = ReadmeUpdateOutput(
        status="updated", note_id=saved.id, version=saved.version, section=args.section
    )
    return out, f"改 README：{args.section}"


_HANDLERS: dict[str, Any] = {"readme_get": _get, "readme_update": _update}


async def run(
    service: AiToolService,
    requester: Requester,
    spec: ToolSpec,
    raw: dict[str, Any],
    *,
    request_id: str | None,
) -> BaseModel:
    try:
        parsed = spec.input_model.model_validate(raw)
    except ValidationError as error:
        raise ToolCallError("VALIDATION_FAILED", validation_message(error)) from None
    arguments = parsed.model_dump(mode="json")
    if "text" in arguments:  # 执行记录里不存正文，只记长度
        arguments["text"] = f"（{len(arguments['text'])} 字）"
    denied = service.check(requester, spec)
    if denied is not None:
        await service.record_denied(requester, spec, arguments, denied, request_id=request_id)
        raise ToolCallError(denied, "这个实例没有权限调用这个工具")
    instance_id = requester.instance.id
    try:
        output, summary = await _HANDLERS[spec.name](service, requester, parsed)
    except ToolCallError as error:
        await service.db.rollback()
        instance = await service.db.get(AgentInstance, instance_id)
        if instance is not None:
            await service.record_knowledge(
                await service.requester(instance),
                spec,
                arguments,
                ok=False,
                summary=f"{spec.name}：{error.code}",
                error_code=error.code,
                request_id=request_id,
            )
        raise
    await service.record_knowledge(
        requester, spec, arguments, ok=True, summary=summary, request_id=request_id
    )
    result: BaseModel = output
    return result


__all__ = ["doc", "readable", "ref", "run"]
