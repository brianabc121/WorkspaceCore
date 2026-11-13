"""智能体的运行环境工具（T-0137 / T-0138）。

见 docs/v2/RUNNER_PLAN.md 第 10 节与 docs/AGENTS.md 例外八：

- 只列给打开了“运行环境权限”的实例；关掉后调用返回 FORBIDDEN_CAPABILITY。
- 直接执行、不审批；仍然经过权限范围与角色、限额（app/domain/runner.py）、执行记录与审计。
- 执行记录里只存参数摘要：命令脱敏、截断；文件只记路径与大小，不存内容。
"""

import uuid
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy import select

from app.domain import agent_ops
from app.domain import runner_specs as specs
from app.domain.ai_tools import AiToolService, Requester, ToolCallError, validation_message
from app.domain.runner import Runner, RunnerError, Viewer
from app.domain.runner_capabilities import ContainerApprovalRequired, RunnerCapabilityService
from app.models import AgentInstance, RunnerCommand
from app.schemas.ai_tools import ToolSpec
from app.schemas.runner import (
    EnvCreateInput,
    EnvCreateOutput,
    EnvExecInput,
    EnvFilesInput,
    EnvFindInput,
    EnvFindOutput,
    EnvIdInput,
    EnvOutput,
    EnvRunInput,
    EnvSetupInput,
    EnvUpdateInput,
    RunnerHostMcpCandidateOut,
    RunnerHostMcpCandidatesOutput,
    RunnerHostText,
    RunnerTemplateSaveInput,
    RunnerTemplateSaveOutput,
    RunnerTemplatesInput,
    RunnerTemplatesOutput,
    RunOut,
)

READ_TOOLS = frozenset({"runner_templates", "env_find", "env_run", "runner_hosts"})
ARG_TEXT_LIMIT = 2000


def _short(value: Any) -> str:
    text = value if isinstance(value, str) else " ".join(str(v) for v in value)
    text = agent_ops.redact(text)
    return text if len(text) <= ARG_TEXT_LIMIT else text[:ARG_TEXT_LIMIT] + "…"


def _arguments(parsed: BaseModel) -> dict[str, Any]:
    """执行记录里的参数：去掉文件内容，命令脱敏截断。"""
    data = parsed.model_dump(mode="json", exclude_none=True)
    if isinstance(parsed, EnvFilesInput):
        size = len(parsed.text or "") or len(parsed.content_base64 or "") * 3 // 4
        data.pop("text", None)
        data.pop("content_base64", None)
        if parsed.action == "write":
            data["size"] = size
    if isinstance(parsed, EnvCreateInput):
        data["files"] = [{"path": f.path} for f in parsed.files]
        if parsed.command is not None:
            data["command"] = _short(parsed.command)
    if isinstance(parsed, EnvExecInput):
        data["command"] = _short(parsed.command)
        data["env"] = sorted(parsed.env)
    if isinstance(parsed, EnvSetupInput):
        data["steps"] = [_short(s) for s in parsed.steps[:50]]
    if isinstance(parsed, RunnerTemplateSaveInput):
        spec = data.get("spec") or {}
        spec["steps"] = [_short(s) for s in parsed.spec.steps]
        spec["env"] = sorted(parsed.spec.env)
        data["spec"] = spec
    return data


def _viewer(instance: AgentInstance) -> Viewer:
    return Viewer(instance.workspace_id, instance=instance)


async def _wait_run(runner: Runner, run_id: uuid.UUID, seconds: int) -> RunOut:
    run = await runner.wait_run(run_id, seconds)
    if run is not None and run.finished_at is None:
        run = await runner.wake_later(run_id)
    if run is None:
        raise ToolCallError("NOT_FOUND", "没有这次运行")
    return runner.run_out(run)


async def _env_ready(runner: Runner, env_id: uuid.UUID, seconds: float) -> None:
    """等建环境的指令结束（最多 seconds 秒）；超时就先返回 creating。"""
    pending = await runner.db.scalar(
        select(RunnerCommand.id)
        .where(
            RunnerCommand.environment_id == env_id,
            RunnerCommand.kind == "create_env",
            RunnerCommand.status.in_(("pending", "sent")),
        )
        .limit(1)
    )
    if pending is not None:
        await runner.wait_command(pending, seconds)


async def _call(
    runner: Runner, instance: AgentInstance, spec: ToolSpec, parsed: BaseModel
) -> tuple[BaseModel, str]:
    viewer = _viewer(instance)
    name = spec.name
    if name == "runner_hosts":
        output = await runner.host_candidates(viewer)
        safe = RunnerHostMcpCandidatesOutput(
            items=[
                RunnerHostMcpCandidateOut(
                    **item.model_dump(exclude={"name", "node_name"}),
                    untrusted=RunnerHostText(name=item.name, node_name=item.node_name),
                )
                for item in output.items
            ]
        )
        return safe, f"{len(output.items)} 个运行服务器"
    if isinstance(parsed, RunnerTemplatesInput):
        templates = await runner.templates(instance.workspace_id)
        owners = await runner.owner_names(
            {t.owner_instance_id for t in templates if t.owner_instance_id}
        )
        listed = [
            await runner.template_out(t, viewer, owners)
            for t in templates
            if t.status == "active" or t.owner_instance_id == instance.id
        ]
        return RunnerTemplatesOutput(items=listed), f"{len(listed)} 个模板"
    if isinstance(parsed, RunnerTemplateSaveInput):
        template, created = await runner.save_template(
            viewer,
            name=parsed.name,
            description=parsed.description,
            spec=parsed.spec.model_dump(),
        )
        await runner.db.commit()
        command = await runner.db.scalar(
            select(RunnerCommand.id)
            .where(RunnerCommand.template_id == template.id, RunnerCommand.status == "pending")
            .order_by(RunnerCommand.created_at.desc())
            .limit(1)
        )
        if command is not None and parsed.wait_seconds:
            await runner.wait_command(command, parsed.wait_seconds)
        await runner.db.refresh(template)
        saved = await runner.template_out(template, viewer)
        return (
            RunnerTemplateSaveOutput(template=saved, created=created),
            f"模板 {template.name} r{template.revision}：{template.build_status}",
        )
    if isinstance(parsed, EnvFindInput):
        envs = await runner.find(
            viewer,
            template=parsed.template,
            reuse_key=parsed.reuse_key,
            query=parsed.query,
            include_deleted=parsed.include_deleted,
        )
        names = await runner.owner_names({e.owner_instance_id for e in envs if e.owner_instance_id})
        conditions = await runner.conditions([e.id for e in envs])
        items = [await runner.env_out(e, viewer, names=names, conditions=conditions) for e in envs]
        return EnvFindOutput(items=items), f"{len(items)} 个环境"
    if isinstance(parsed, EnvCreateInput):
        env, reused, hint, run = await runner.create(instance, parsed)
        run_out: RunOut | None = None
        if run is not None:
            run_out = await _wait_run(runner, run.id, specs.SYNC_WAIT_SECONDS)
        elif not reused:
            await _env_ready(runner, env.id, specs.SYNC_WAIT_SECONDS)
        await runner.db.refresh(env)
        out = await runner.env_out(env, viewer)
        summary = f"{'复用' if reused else '新建'}环境 {env.id}（{env.lifetime}）：{env.state}"
        return EnvCreateOutput(environment=out, reused=reused, hint=hint, run=run_out), summary
    if isinstance(parsed, EnvExecInput):
        run = await runner.exec(instance, parsed)
        result = await _wait_run(runner, run.id, parsed.wait_seconds)
        return result, f"env_exec {parsed.environment_id}：{result.status}"
    if isinstance(parsed, EnvRunInput):
        found = await runner.run_for(viewer, parsed.run_id)
        return runner.run_out(found), f"运行 {found.id}：{found.status}"
    if isinstance(parsed, EnvFilesInput):
        files = await runner.files(instance, parsed)
        return files, f"env_files {parsed.action} {parsed.path or '.'}"
    if isinstance(parsed, EnvSetupInput):
        env = await runner.setup(instance, parsed.environment_id, parsed.steps)
        return EnvOutput(environment=await runner.env_out(env, viewer)), (
            f"登记 {len(parsed.steps)} 条初始化步骤"
        )
    if isinstance(parsed, EnvUpdateInput):
        env = await runner.update(instance, parsed)
        return EnvOutput(environment=await runner.env_out(env, viewer)), "更新环境"
    if isinstance(parsed, EnvIdInput):
        if name == "env_stop":
            env = await runner.stop_by_owner(instance, parsed.environment_id)
            summary = "停止环境"
        else:
            env = await runner.release(instance, parsed.environment_id)
            summary = f"释放环境：{env.state}"
        return EnvOutput(environment=await runner.env_out(env, viewer)), summary
    raise ToolCallError("NOT_FOUND", "没有这个工具")


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
    arguments = _arguments(parsed)
    instance = requester.instance
    denied = service.check(requester, spec)
    if denied is None and not instance.runner_enabled:
        denied = "FORBIDDEN_CAPABILITY"
    if denied is not None:
        await service.record_denied(requester, spec, arguments, denied, request_id=request_id)
        message = (
            "管理员还没有给你开“运行环境权限”"
            if denied == "FORBIDDEN_CAPABILITY"
            else "这个实例没有权限调用这个工具"
        )
        raise ToolCallError(denied, message)
    if spec.name == "runner_request_capability":
        pending = await RunnerCapabilityService(
            service.db, service.settings, service.redis
        ).request(instance, **parsed.model_dump())
        await service.db.commit()
        await service._enqueue_cards(pending.action_id)
        return pending
    runner = Runner(service.db, service.settings, service.redis)
    reading = spec.name in READ_TOOLS
    instance_id = instance.id
    try:
        output, summary = await _call(runner, instance, spec, parsed)
    except ContainerApprovalRequired as error:
        # The quota decision is an intended result: persist the pending approval before returning.
        await service.db.commit()
        await service._enqueue_cards(error.approval_id)
        from app.models import AiToolExecution
        from app.schemas.ai_tools import ContainerApprovalOutput

        approval = await service.db.get(AiToolExecution, error.approval_id)
        if approval is None or approval.expires_at is None:
            raise ToolCallError("NOT_FOUND", "审批记录不存在") from None
        return ContainerApprovalOutput(
            action_id=error.approval_id,
            approval_id=error.approval_id,
            summary=error.message,
            expires_at=approval.expires_at,
            deduplicated=error.deduplicated,
        )
    except (RunnerError, ToolCallError) as error:
        code = error.code
        message = error.message
        await service.db.rollback()
        current = await service.db.get(AgentInstance, instance_id)
        if current is not None:
            who = await service.requester(current)
            if reading:
                await service.record_read(
                    who, spec, arguments, ok=False, error_code=code, request_id=request_id
                )
            else:
                await service.record_knowledge(
                    who,
                    spec,
                    arguments,
                    ok=False,
                    summary=f"{spec.name}：{code}",
                    error_code=code,
                    request_id=request_id,
                )
        raise ToolCallError(code, message) from None
    if reading:
        await service.record_read(requester, spec, arguments, ok=True, request_id=request_id)
    else:
        await service.record_knowledge(
            requester, spec, arguments, ok=True, summary=summary[:200], request_id=request_id
        )
    return output


__all__ = ["READ_TOOLS", "run"]
