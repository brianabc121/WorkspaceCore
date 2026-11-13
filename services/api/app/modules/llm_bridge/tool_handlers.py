"""智能体工具 llm_bridge_* 的处理（docs/AGENTS.md 例外九）。

- 只列给、只接受管理员打开了“大模型会话权限”的实例；写工具还要打开“可以新建和发送”。
- 读工具经 MCP 读工具的通用流程（宿主记执行记录、检查角色）；写工具直接执行、不审批，
  这里记执行记录，新建会话时通知管理员。
- 执行记录里的消息正文脱敏并截断；会话桥返回的内容不进执行记录。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Collection
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.domain import agent_ops, notifications
from app.domain.ai_tools import AiToolService, Requester, ToolCallError, validation_message
from app.domain.connectors import ConnectorRuntime
from app.integrations.base import ConnectorError
from app.models import AgentInstance
from app.modules.llm_bridge.client import BridgeCaller, BridgeCallError
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
# 会话桥的错误码 → 工作台工具的错误码；其余都当作外部服务不可用
_CODES = {
    "NOT_FOUND": "NOT_FOUND",
    "VALIDATION_FAILED": "VALIDATION_FAILED",
    "RATE_LIMITED": "RATE_LIMITED",
    "FORBIDDEN": "FORBIDDEN_CAPABILITY",
    "CONFLICT": "CONFLICT",
    "NOT_SUPPORTED": "NOT_SUPPORTED",
    "NOT_LOGGED_IN": "EXTERNAL_UNAVAILABLE",
    "ACCOUNT_MISMATCH": "EXTERNAL_UNAVAILABLE",
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


async def _pick_bridge(
    service: LlmBridgeService, instance: AgentInstance, name: str | None
) -> LlmBridge:
    bridges = await service.bridges(instance.workspace_id, only_enabled=True)
    if not bridges:
        raise ToolCallError("EXTERNAL_UNAVAILABLE", "这个工作空间还没有登记可用的会话桥")
    if name is None:
        if len(bridges) == 1:
            return bridges[0]
        names = "、".join(b.name for b in bridges)
        raise ToolCallError("VALIDATION_FAILED", f"登记了多座会话桥，请用 bridge 参数指定：{names}")
    for bridge in bridges:
        if bridge.name == name:
            return bridge
    raise ToolCallError("NOT_FOUND", f"没有名为 {name} 的会话桥")


async def forward(
    db: AsyncSession,
    settings: Settings,
    runtime: ConnectorRuntime,
    instance: AgentInstance,
    tool: str,
    parsed: BaseModel,
) -> BridgeToolOutput:
    """检查这个实例的权限，把调用转给会话桥。"""
    service = LlmBridgeService(db, settings, runtime)
    grant = await service.grant(instance.id)
    if grant is None or not grant.enabled:
        raise ToolCallError("FORBIDDEN_CAPABILITY", _NO_GRANT)
    if tool in WRITE_TOOL_NAMES and not grant.can_write:
        raise ToolCallError("FORBIDDEN_CAPABILITY", _NO_WRITE)
    arguments = parsed.model_dump(mode="json", exclude_none=True)
    bridge_name = arguments.pop("bridge", None)
    platform = arguments.get("platform")
    if platform and grant.platforms and platform not in grant.platforms:
        raise ToolCallError("FORBIDDEN_CAPABILITY", f"管理员没有给你开 {platform} 这个平台")
    bridge = await _pick_bridge(service, instance, bridge_name)
    caller = BridgeCaller(str(instance.id), instance.name, _scopes(grant))
    # 之后不再用数据库：等会话桥回答可能要几十秒，不占着事务
    bridge_label, client = bridge.name, service.client(bridge)
    await db.commit()
    try:
        data = await client.tool(caller, "llm_" + tool.removeprefix(PREFIX), arguments)
    except BridgeCallError as error:
        code = _CODES.get(error.code, "EXTERNAL_UNAVAILABLE")
        raise ToolCallError(code, error.message or "会话桥返回了错误") from None
    except ConnectorError as error:
        if error.kind == "TIMEOUT_UNKNOWN":
            raise ToolCallError(
                "EXTERNAL_UNAVAILABLE",
                "会话桥没有在时限内回答；用同一个 request_id 重试不会重复发送",
            ) from None
        raise ToolCallError("EXTERNAL_UNAVAILABLE", "会话桥暂时连不上") from None
    if not isinstance(data, dict):
        raise ToolCallError("EXTERNAL_UNAVAILABLE", "会话桥返回了无法识别的结果")
    return BridgeToolOutput(bridge=bridge_label, data=data)


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
    """写工具（风险等级 direct）：直接执行、不审批；记执行记录，新建会话时通知管理员。"""
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
        output = await forward(
            service.db, service.settings, service.runtime, requester.instance, spec.name, parsed
        )
    except ToolCallError as error:
        await service.db.rollback()
        current = await service.db.get(AgentInstance, instance_id)
        if current is not None:
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
            link="/llm_bridge",
            key=str(data.get("session_id")),
        )
    await service.record_knowledge(
        who,
        spec,
        arguments,
        ok=True,
        summary=_summary(spec.name, output)[:200],
        request_id=request_id,
    )
    return output
