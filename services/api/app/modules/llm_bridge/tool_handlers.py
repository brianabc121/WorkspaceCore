"""智能体工具 llm_bridge_* 的处理（docs/AGENTS.md 例外九）。

- 只列给、只接受管理员打开了“大模型会话权限”的实例；写工具还要打开“可以新建和发送”。
- 读工具经 MCP 读工具的通用流程（宿主记执行记录、检查角色）；写工具直接执行、不审批，
  这里记执行记录，新建会话时通知管理员。
- 执行记录里的消息正文脱敏并截断；会话桥返回的内容不进执行记录。
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable, Collection
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.domain import agent_ops, notifications, secret_scan
from app.domain.ai_tools import AiToolService, Requester, ToolCallError, validation_message
from app.domain.connectors import ConnectorRuntime
from app.integrations.base import ConnectorError
from app.models import AgentInstance
from app.models.task import Task
from app.models.timeline import TaskRun
from app.modules.llm_bridge import watches
from app.modules.llm_bridge.client import BridgeCaller, BridgeCallError, BridgeClient
from app.modules.llm_bridge.models import LlmBridge, LlmBridgeGrant
from app.modules.llm_bridge.service import LlmBridgeService
from app.modules.llm_bridge.tool_schemas import BridgeToolOutput
from app.modules.llm_bridge.tools import SPECS, WRITE_TOOL_NAMES
from app.schemas.tool_spec import ToolSpec

if TYPE_CHECKING:  # mcp_read_tools 导入本模块的 HANDLERS
    from app.domain.mcp_read_tools import ReadCall

PREFIX = "llm_bridge_"
TEXT_LIMIT = 2000
_NO_GRANT = "管理员还没有给你开“大模型会话权限”"
_NO_WRITE = "管理员没有给你“新建会话和发送消息”的权限"
_NO_BRIDGE = "管理员没有给你开任何 MCP 管理器（会话桥）"
# 会话桥的错误码 → 工作台工具的错误码；其余都当作外部服务不可用
_CODES = {
    "NOT_FOUND": "NOT_FOUND",
    "VALIDATION_FAILED": "VALIDATION_FAILED",
    "RATE_LIMITED": "RATE_LIMITED",
    "FORBIDDEN": "FORBIDDEN_CAPABILITY",
    "CONFLICT": "CONFLICT",
    "NOT_SUPPORTED": "NOT_SUPPORTED",
    "NOT_LOGGED_IN": "NOT_LOGGED_IN",
    "ACCOUNT_MISMATCH": "ACCOUNT_MISMATCH",
    "UNAVAILABLE": "EXTERNAL_UNAVAILABLE",
}


def _scopes(grant: LlmBridgeGrant) -> tuple[str, ...]:
    flags = (
        ("write", grant.can_write),
        ("account", grant.can_account),
        ("takeover", grant.can_takeover),
    )
    return tuple(name for name, on in flags if on)


async def visible_tools(
    db: AsyncSession, settings: Settings, instance: AgentInstance
) -> Collection[str]:
    """列工具时用：这个实例能看到本模块的哪些工具（没授权时一个都看不到）。"""
    grant = await db.get(LlmBridgeGrant, instance.id)
    if grant is None or not grant.enabled:
        return ()
    return [s.name for s in SPECS if grant.can_write or s.name not in WRITE_TOOL_NAMES]


async def _candidates(
    service: LlmBridgeService, instance: AgentInstance, grant: LlmBridgeGrant
) -> list[LlmBridge]:
    """这个实例能用的会话桥（MCP 管理器）：启用着的，并且管理员允许它用。"""
    bridges = await service.bridges(instance.workspace_id, only_enabled=True)
    if not bridges:
        raise ToolCallError("EXTERNAL_UNAVAILABLE", "这个工作空间还没有登记可用的会话桥")
    allowed = service.allowed(grant, bridges)
    if not allowed:
        raise ToolCallError("FORBIDDEN_CAPABILITY", _NO_BRIDGE)
    return allowed


def _pick_bridge(bridges: list[LlmBridge], name: str | None) -> LlmBridge:
    if name is None:
        if len(bridges) == 1:
            return bridges[0]
        names = "、".join(b.name for b in bridges)
        raise ToolCallError(
            "VALIDATION_FAILED",
            f"你能用多个 MCP 管理器（会话桥），请用 bridge 参数指定：{names}。"
            "不填 bridge 调用 llm_bridge_status 可以看到每一个控制哪些平台",
        )
    for bridge in bridges:
        if bridge.name == name:
            return bridge
    # 存在但没有授权的，也按“没有”回答：不透露别的管理器的名字
    raise ToolCallError("NOT_FOUND", f"没有名为 {name} 的会话桥")


# 没等到回答时该怎么办，按工具说：只有带幂等号的调用才能放心重试
_TIMEOUT_ADVICE = {
    "llm_bridge_send": "用同一个 request_id 重试不会重复发送",
    "llm_bridge_session_create": "不确定会话有没有建好：带着同一个 request_id 再调一次，"
    "得到的是同一个会话；这次没带 request_id 的话，先用 llm_bridge_session_list 看一下，"
    "不要直接再建一个",
}


# 会话桥报的错里，重新调用有没有用（docs/v2/LLM_BRIDGE_IMPROVE_PLAN.md 4.1 的 A3）：
# True 过一会儿再调有意义；False 要先解决原因（让管理员登录、改参数、换会话）
_RETRYABLE = {
    "RATE_LIMITED": True,
    "UNAVAILABLE": True,
    "NOT_LOGGED_IN": False,
    "ACCOUNT_MISMATCH": False,
    "NOT_FOUND": False,
    "VALIDATION_FAILED": False,
    "FORBIDDEN": False,
    "CONFLICT": False,
    "NOT_SUPPORTED": False,
}
_ADVICE = {
    "NOT_LOGGED_IN": "这个平台还没有登录：要管理员在“大模型客户端管理”里登录，你重试没有用",
    "ACCOUNT_MISMATCH": "这个平台登录的账号和登记的不一样：要管理员处理，你重试没有用",
}


def _call_error(error: BridgeCallError | ConnectorError, tool: str = "") -> ToolCallError:
    """把会话桥的错误说清楚：是什么问题、该不该再调、多久以后（details 里的 retryable、
    retry_after_seconds）。没登录、账号不对各有各的错误码，不再混在“外部服务不可用”里。"""
    if isinstance(error, BridgeCallError):
        code = _CODES.get(error.code, "EXTERNAL_UNAVAILABLE")
        details: dict[str, Any] = {"retryable": _RETRYABLE.get(error.code, True), **error.details}
        if error.code == "UNAVAILABLE":
            details.setdefault("retry_after_seconds", 30)
        message = error.message or "会话桥返回了错误"
        if error.code in _ADVICE:
            message = f"{message}。{_ADVICE[error.code]}"
        return ToolCallError(code, message, details)
    if error.kind == "TIMEOUT_UNKNOWN":
        advice = _TIMEOUT_ADVICE.get(tool, "稍后再试")
        # 不知道做没做：不是“能重试”，也不是“不能重试”
        return ToolCallError(
            "OUTCOME_UNKNOWN", f"会话桥没有在时限内回答；{advice}", {"retryable": None}
        )
    return ToolCallError(
        "EXTERNAL_UNAVAILABLE",
        "会话桥暂时连不上，过一会儿再试",
        {"retryable": True, "retry_after_seconds": 30},
    )


def _trim_result(data: dict[str, Any], max_chars: int, offset: int) -> None:
    """回答太长时只给一段：默认从头给 max_chars 个字符，并说明怎么接着取。"""
    result = data.get("result")
    if not isinstance(result, str):
        return
    total = len(result)
    piece = result[offset : offset + max_chars]
    data["result"] = piece
    if offset or offset + len(piece) < total:
        data["result_chars"] = total
        data["result_offset"] = offset
        more = offset + len(piece) < total
        data["result_next_offset"] = offset + len(piece) if more else None
        if more:
            data["hint"] = (
                f"回答一共 {total} 个字符，这里是第 {offset} 到 {offset + len(piece)} 个："
                f"用 llm_bridge_turn_get 加 result_offset={offset + len(piece)} 接着取"
            )


def _trim_messages(data: dict[str, Any], max_chars: int) -> None:
    for message in data.get("messages") or []:
        text = message.get("text") if isinstance(message, dict) else None
        if isinstance(text, str) and len(text) > max_chars:
            message["text"] = text[:max_chars]
            message["text_truncated"] = True
            message["text_chars"] = len(text)


async def _status_of_all(
    caller: BridgeCaller, clients: list[tuple[str, BridgeClient]]
) -> BridgeToolOutput:
    """能用多个管理器、又没指定 bridge 时：每一个的概况（控制的平台、登录情况、排队）。"""

    async def one(name: str, client: BridgeClient) -> dict[str, Any]:
        try:
            data = await client.tool(caller, "llm_status", {})
        except (BridgeCallError, ConnectorError) as error:
            return {"bridge": name, "reachable": False, "error": _call_error(error).message}
        if not isinstance(data, dict):
            return {"bridge": name, "reachable": False, "error": "返回了无法识别的结果"}
        return {"bridge": name, "reachable": True, **data}

    results = await asyncio.gather(*(one(name, client) for name, client in clients))
    return BridgeToolOutput(bridge="", data={"bridges": list(results)})


async def forward(
    db: AsyncSession,
    settings: Settings,
    runtime: ConnectorRuntime,
    instance: AgentInstance,
    tool: str,
    parsed: BaseModel,
) -> BridgeToolOutput:
    """检查这个实例的权限，把调用转给会话桥。"""
    output, _ = await _forward(db, settings, runtime, instance, tool, parsed)
    return output


async def _forward(
    db: AsyncSession,
    settings: Settings,
    runtime: ConnectorRuntime,
    instance: AgentInstance,
    tool: str,
    parsed: BaseModel,
) -> tuple[BridgeToolOutput, str]:
    """同上，另外给出用的是哪个会话桥（通知里的链接要用）。"""
    service = LlmBridgeService(db, settings, runtime)
    grant = await service.grant(instance.id)
    if grant is None or not grant.enabled:
        raise ToolCallError("FORBIDDEN_CAPABILITY", _NO_GRANT)
    if tool in WRITE_TOOL_NAMES and not grant.can_write:
        raise ToolCallError("FORBIDDEN_CAPABILITY", _NO_WRITE)
    arguments = parsed.model_dump(mode="json", exclude_none=True)
    bridge_name = arguments.pop("bridge", None)
    # 这几项是工作台自己用的，不传给会话桥
    max_chars = arguments.pop("max_chars", None)
    result_offset = arguments.pop("result_offset", 0)
    arguments.pop("notify", None)
    # 发送时没给会话：先新建一个（要有平台）
    create: dict[str, Any] | None = None
    if tool == "llm_bridge_send":
        new_keys = ("platform", "session_mode", "model", "environment", "title")
        given = {key: arguments.pop(key) for key in new_keys if key in arguments}
        if "session_id" not in arguments:
            if "platform" not in given:
                raise ToolCallError(
                    "VALIDATION_FAILED",
                    "要么填 session_id 发到已有的会话，要么填 platform 先新建一个再发",
                )
            create = {"mode": given.pop("session_mode", "chat"), **given}
            create["request_id"] = f"{arguments['request_id']}.new"
        elif given:
            raise ToolCallError(
                "VALIDATION_FAILED",
                "填了 session_id 就是发到已有的会话：不要再填 "
                + "、".join(given)
                + "（它们只在新建会话时用）",
            )
    platform = (create or arguments).get("platform")
    if platform and grant.platforms and platform not in grant.platforms:
        raise ToolCallError("FORBIDDEN_CAPABILITY", f"管理员没有给你开 {platform} 这个平台")
    # 要发给第三方平台的内容先过密钥扫描：像密钥的不发、不存（例外九的“密钥拦截”）
    if any(
        isinstance(arguments.get(key), str) and secret_scan.scan(arguments[key])
        for key in ("text", "title")
    ):
        raise ToolCallError(
            "VALIDATION_FAILED",
            "内容里像是有令牌、私钥一类的密钥，没有发送；去掉之后再发",
        )
    # 开发环境只能用管理员给这个实例勾选的
    environments = set(grant.environment_ids or [])
    wanted = (create or arguments).get("environment")
    if wanted and wanted not in environments:
        raise ToolCallError(
            "FORBIDDEN_CAPABILITY",
            "管理员没有给你开这个开发环境；能用的见 llm_bridge_environments",
        )
    bridges = await _candidates(service, instance, grant)
    source = await _source(db, instance) if tool in WRITE_TOOL_NAMES else None
    # 平台的限制一并带给会话桥：发送、取回合、读会话这些调用不带 platform 参数，
    # 只有会话桥知道会话属于哪个平台
    caller = BridgeCaller(
        str(instance.id),
        instance.name,
        _scopes(grant),
        tuple(grant.platforms) if grant.platforms else None,
        source,
    )
    if tool == "llm_bridge_status" and bridge_name is None and len(bridges) > 1:
        clients = [(b.name, await service.client(b)) for b in bridges]
        await db.commit()
        return await _status_of_all(caller, clients), ""
    bridge = _pick_bridge(bridges, bridge_name)
    # 之后不再用数据库：等会话桥回答可能要几十秒，不占着事务
    bridge_label, bridge_id, client = bridge.name, str(bridge.id), await service.client(bridge)
    await db.commit()
    try:
        if create is not None:
            made = await client.tool(caller, "llm_session_create", create)
            if not isinstance(made, dict) or not isinstance(made.get("session_id"), str):
                raise ToolCallError("EXTERNAL_UNAVAILABLE", "会话桥返回了无法识别的结果")
            arguments["session_id"] = made["session_id"]
        data = await client.tool(caller, "llm_" + tool.removeprefix(PREFIX), arguments)
    except (BridgeCallError, ConnectorError) as error:
        failure = _call_error(error, "llm_bridge_session_create" if create is not None else tool)
        if create is not None and "session_id" in arguments:
            # 会话建好了但消息没发出去：告诉调用方会话号，重发时填它，不要再建一个
            failure.details["session_id"] = arguments["session_id"]
            failure.message = (
                f"会话建好了（session_id={arguments['session_id']}），但消息没有发出去："
                f"{failure.message}"
            )
        raise failure from None
    if not isinstance(data, dict):
        raise ToolCallError("EXTERNAL_UNAVAILABLE", "会话桥返回了无法识别的结果")
    if create is not None:
        data["created_session"] = True
    if tool in ("llm_bridge_send", "llm_bridge_turn_get") and max_chars:
        _trim_result(data, int(max_chars), int(result_offset))
    if tool == "llm_bridge_session_get" and max_chars:
        _trim_messages(data, int(max_chars))
    if tool == "llm_bridge_environments" and isinstance(data.get("environments"), list):
        data["environments"] = [
            e for e in data["environments"] if isinstance(e, dict) and e.get("id") in environments
        ]
    return BridgeToolOutput(bridge=bridge_label, data=data), bridge_id


def _read_handler(tool: str) -> Callable[[ReadCall, BaseModel], Awaitable[BaseModel]]:
    async def handler(call: ReadCall, parsed: BaseModel) -> BaseModel:
        return await forward(
            call.db, call.settings, call.runtime, call.requester.instance, tool, parsed
        )

    return handler


HANDLERS: dict[str, Callable[..., Awaitable[BaseModel]]] = {
    spec.name: _read_handler(spec.name) for spec in SPECS if spec.name not in WRITE_TOOL_NAMES
}


def _record_arguments(parsed: BaseModel) -> dict[str, Any]:
    data = parsed.model_dump(mode="json", exclude_none=True)
    text = data.get("text")
    if isinstance(text, str):
        clean = agent_ops.redact(text)
        data["text"] = clean if len(clean) <= TEXT_LIMIT else clean[:TEXT_LIMIT] + "…"
    return data


async def _source(db: AsyncSession, instance: AgentInstance) -> dict[str, Any]:
    """出处：哪个实例、它这时候正在执行哪些任务（没结束的执行记录，最多 3 个）。

    由工作台填，不让智能体自己说；管理器只记录，回溯时能从会话点回任务。"""
    rows = (
        await db.execute(
            select(TaskRun.id, Task.id, Task.title, Task.project_id)
            .join(Task, Task.id == TaskRun.task_id)
            .where(TaskRun.instance_id == instance.id, TaskRun.ended_at.is_(None))
            .order_by(TaskRun.started_at.desc())
            .limit(3)
        )
    ).all()
    return {
        "kind": "agent",
        "instance_id": str(instance.id),
        "instance_name": instance.name,
        "tasks": [
            {
                "run_id": str(run_id),
                "task_id": str(task_id),
                "title": agent_ops.redact(title or "")[:120],
                "project_id": str(project_id),
            }
            for run_id, task_id, title, project_id in rows
        ],
    }


def _result_ref(bridge_id: str, data: dict[str, Any]) -> dict[str, Any]:
    """执行得到的编号：执行记录里带着，回溯时能从这条记录点到会话的那一回合。"""
    ref = {"bridge_id": bridge_id}
    for key in ("session_id", "turn_id"):
        if isinstance(data.get(key), str):
            ref[key] = data[key]
    return ref


def _session_link(bridge_id: str, session_id: Any) -> str:
    """通知里的链接：直接到聊天页的那个会话。"""
    if not bridge_id or not isinstance(session_id, str):
        return "/llm_bridge"
    return f"/llm_bridge?m={bridge_id}&s={session_id}"


def _summary(tool: str, output: BridgeToolOutput) -> str:
    data = output.data
    where = f"{data.get('platform')}.{data.get('mode')}"
    if tool == "llm_bridge_session_create":
        return f"在 {where} 新建会话（模型 {data.get('model')}）"
    if tool == "llm_bridge_send":
        return f"向 {where} 的会话发送消息：{data.get('status')}"
    if tool == "llm_bridge_turn_cancel":
        return f"取消回合：{data.get('status')}"
    return f"归档 {where} 的会话"


async def run(
    service: AiToolService,
    requester: Requester,
    spec: ToolSpec,
    raw: dict[str, Any],
    *,
    request_id: str | None,
) -> BaseModel:
    """写工具（风险等级 direct）：直接执行、不审批；记执行记录。

    通知管理员：新建会话、向不是自己建的会话发消息（接手）、被发送限额拒绝。"""
    try:
        parsed = spec.input_model.model_validate(raw)
    except ValidationError as error:
        raise ToolCallError("VALIDATION_FAILED", validation_message(error)) from None
    arguments = _record_arguments(parsed)
    denied = service.check(requester, spec)
    if denied is not None:
        await service.record_denied(requester, spec, arguments, denied, request_id=request_id)
        raise ToolCallError(denied, "这个实例没有权限调用这个工具")
    instance_id = requester.instance.id
    try:
        output, bridge_id = await _forward(
            service.db, service.settings, service.runtime, requester.instance, spec.name, parsed
        )
    except ToolCallError as error:
        await service.db.rollback()
        current = await service.db.get(AgentInstance, instance_id)
        if current is not None:
            if error.code == "RATE_LIMITED":
                # 同一个实例一个小时只通知一次，不刷屏
                await notifications.notify_admins(
                    service.db,
                    current.workspace_id,
                    type_="llm_bridge.rate_limited",
                    severity="warning",
                    title=f"智能体「{current.name}」向大模型发消息被限额拒绝",
                    body=error.message,
                    link="/llm_bridge?view=manage",
                    key=str(current.id),
                )
            await service.record_knowledge(
                await service.requester(current),
                spec,
                arguments,
                ok=False,
                summary=f"{spec.name}：{error.code}",
                error_code=error.code,
                request_id=request_id,
            )
        raise
    # forward 里提交过事务，requester 里的对象已经过期：重新取
    current = await service.db.get(AgentInstance, instance_id)
    if current is None:
        raise ToolCallError("NOT_FOUND", "实例不存在")
    who = await service.requester(current)
    if spec.name == "llm_bridge_session_create":
        data = output.data
        await notifications.notify_admins(
            service.db,
            current.workspace_id,
            type_="llm_bridge.session_created",
            severity="info",
            title=f"智能体「{current.name}」在会话桥上新建了会话",
            body=f"{data.get('platform')}.{data.get('mode')}，模型 {data.get('model')}"
            f"（{output.bridge}）",
            link=_session_link(bridge_id, data.get("session_id")),
            key=str(data.get("session_id")),
        )
    if spec.name == "llm_bridge_send" and getattr(parsed, "notify", False):
        # 结束后叫我：回合还没做完才用得着；已经有结果的不用叫
        pending = output.data.get("status") in ("queued", "running")
        if pending and isinstance(output.data.get("turn_id"), str):
            await watches.add(
                service.db,
                current,
                uuid.UUID(bridge_id),
                str(output.data.get("session_id")),
                output.data["turn_id"],
            )
        output.data["will_notify"] = pending
    if spec.name == "llm_bridge_send" and output.data.get("takeover"):
        data = output.data
        await notifications.notify_admins(
            service.db,
            current.workspace_id,
            type_="llm_bridge.takeover_sent",
            severity="warning",
            title=f"智能体「{current.name}」向不是它建的会话发了消息",
            body=f"{data.get('platform')}.{data.get('mode')}（{output.bridge}）",
            link=_session_link(bridge_id, data.get("session_id")),
            key=str(data.get("turn_id")),
        )
    await service.record_knowledge(
        who,
        spec,
        arguments,
        ok=True,
        summary=_summary(spec.name, output)[:200],
        request_id=request_id,
        display={**arguments, "result": _result_ref(bridge_id, output.data)},
    )
    return output
