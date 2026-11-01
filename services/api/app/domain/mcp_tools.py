"""MCP 工具服务器（V2 OC-5，契约 docs/decisions/2026-10-31-v2-oc1-contract.md 第 9 节）。

- 传输：MCP Python SDK 2.3.0 的底层 `Server` + `StreamableHTTPSessionManager`（无状态、JSON 响应，
  OC-0 ADR 第 7 节）。不用 `MCPServer`：它按函数签名生成参数 schema，而工具的参数模型已经写在
  `app/schemas/ai_tools.py` 里（禁止多余字段，写工具直接复用 V1 的创建模型）。
- 服务器名 `myworkspace`，只提供 tools（不提供 prompts、resources，OC-0 M5）。
- 认证在路由里做（`app/api/mcp.py`，与 `/agent-api` 同一套），这里只拿到已认证的实例 ID。
  SDK 在自己的任务组里处理请求，所以实例 ID 放在 ASGI scope 的 state 里传进来，不用 contextvar。
- 每次工具调用都按实例限流（每分钟 120 次）；写工具与高危工具只建待审批记录（A14），读工具也写记录。
- 错误：`isError: true`，结构化内容 `{"error": {"code", "message"}}`。
"""

import asyncio
import contextlib
import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any

import mcp_types as types
from mcp.server.context import ServerRequestContext
from mcp.server.lowlevel.server import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from pydantic import BaseModel, ValidationError
from starlette.requests import Request

from app import __version__
from app.core.errors import ApiError
from app.core.logging import request_id_var
from app.domain.ai_tools import AiToolService, Requester, ToolCallError, validation_message
from app.domain.mcp_read_tools import READ_HANDLERS, ReadCall
from app.domain.ratelimit import WindowLimiter
from app.models import AgentInstance
from app.schemas.ai_tools import (
    TOOLS,
    ToolSpec,
)

logger = logging.getLogger("app.mcp")

SERVER_NAME = "myworkspace"
CALLS_PER_MINUTE = 120
CALLER_STATE_KEY = "mcp_caller"


@dataclass(frozen=True)
class McpCaller:
    """路由认证通过后交给工具处理函数的信息。"""

    instance_id: uuid.UUID
    request_id: str | None


def available(spec: ToolSpec) -> bool:
    return spec.risk != "read" or spec.name in READ_HANDLERS


def tool_definition(spec: ToolSpec) -> types.Tool:
    return types.Tool(
        name=spec.name,
        description=spec.description,
        input_schema=spec.input_model.model_json_schema(),
        output_schema=spec.output_model.model_json_schema(),
        annotations=types.ToolAnnotations(
            read_only_hint=spec.risk == "read",
            destructive_hint=spec.risk == "critical",
            idempotent_hint=spec.risk == "read",
            open_world_hint=False,
        ),
    )


def _caller(ctx: ServerRequestContext[Any, Any]) -> McpCaller:
    request = ctx.request
    caller = None
    if isinstance(request, Request):
        caller = getattr(request.state, CALLER_STATE_KEY, None)
    if not isinstance(caller, McpCaller):
        # 只会在没经过 app/api/mcp.py 的路由时发生：不处理
        raise RuntimeError("MCP 请求没有经过实例认证")
    return caller


def _state(ctx: ServerRequestContext[Any, Any]) -> Any:
    request = ctx.request
    if not isinstance(request, Request):
        raise RuntimeError("MCP 请求缺少 HTTP 上下文")
    return request.app.state


def error_result(code: str, message: str) -> types.CallToolResult:
    payload = {"error": {"code": code, "message": message}}
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))],
        structured_content=payload,
        is_error=True,
    )


def _ok(output: BaseModel) -> types.CallToolResult:
    payload = output.model_dump(mode="json")
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))],
        structured_content=payload,
        is_error=False,
    )


def _from_api_error(error: ApiError) -> tuple[str, str]:
    """领域服务的错误换成契约里的工具错误码；不透出内部细节。"""
    if error.status_code == 404:
        return "NOT_FOUND", "要操作的对象不存在"
    if error.status_code == 422:
        return "VALIDATION_FAILED", error.message
    if error.status_code == 429:
        return "RATE_LIMITED", "请求太频繁，请稍后再试"
    return "EXTERNAL_UNAVAILABLE", "依赖的服务暂时不可用"


async def _list_tools(
    ctx: ServerRequestContext[Any, Any], params: types.PaginatedRequestParams | None
) -> types.ListToolsResult:
    caller = _caller(ctx)
    state = _state(ctx)
    async with state.sessionmaker() as db:
        instance = await db.get(AgentInstance, caller.instance_id)
        scope = instance.scope if instance is not None else "read"
    # 只列这个实例的权限范围里、已经实现的工具；角色是否够在调用时再检查（角色可能变化）
    tools = [
        tool_definition(spec)
        for spec in TOOLS.values()
        if available(spec) and (spec.scope == "read" or scope == "read_write")
    ]
    return types.ListToolsResult(tools=tools)


async def _call_tool(
    ctx: ServerRequestContext[Any, Any], params: types.CallToolRequestParams
) -> types.CallToolResult:
    caller = _caller(ctx)
    state = _state(ctx)
    token = request_id_var.set(caller.request_id)
    try:
        spec = TOOLS.get(params.name)
        if spec is None or not available(spec):
            return error_result("NOT_FOUND", "没有这个工具")
        if state.redis is not None:
            limiter = WindowLimiter(state.redis, 60)
            if await limiter.hit(f"mcp_calls:{caller.instance_id}", CALLS_PER_MINUTE) is not None:
                return error_result("RATE_LIMITED", "调用太频繁，请稍后再试")
        async with state.sessionmaker() as db:
            service = AiToolService(
                db, state.settings, state.connectors, state.redis, jobs=state.jobs
            )
            instance = await db.get(AgentInstance, caller.instance_id)
            if instance is None:
                return error_result("NOT_FOUND", "实例不存在")
            requester = await service.requester(instance)
            raw = dict(params.arguments or {})
            try:
                if spec.risk == "read":
                    output = await _read(service, requester, spec, raw, caller, state)
                else:
                    output = await service.request(
                        requester, spec, raw, request_id=caller.request_id
                    )
            except ToolCallError as error:
                return error_result(error.code, error.message)
            except ApiError as error:
                await db.rollback()
                return error_result(*_from_api_error(error))
        return _ok(output)
    finally:
        request_id_var.reset(token)


async def _read(
    service: AiToolService,
    requester: Requester,
    spec: ToolSpec,
    raw: dict[str, Any],
    caller: McpCaller,
    state: Any,
) -> BaseModel:
    try:
        parsed = spec.input_model.model_validate(raw)
    except ValidationError as error:
        raise ToolCallError("VALIDATION_FAILED", validation_message(error)) from None
    arguments = parsed.model_dump(mode="json")
    denied = service.check(requester, spec)
    if denied is not None:
        await service.record_denied(
            requester, spec, arguments, denied, request_id=caller.request_id
        )
        raise ToolCallError(denied, "这个实例没有权限调用这个工具")
    try:
        call = ReadCall(
            db=service.db,
            settings=service.settings,
            runtime=service.runtime,
            redis=service.redis,
            state=state,
            requester=requester,
        )
        output: BaseModel = await READ_HANDLERS[spec.name](call, parsed)
    except (ToolCallError, ApiError) as error:
        code = error.code if isinstance(error, ToolCallError) else _from_api_error(error)[0]
        # 不回滚：读工具不写数据，回滚会让 requester 里的 ORM 对象过期（异步会话不能隐式加载）
        await service.record_read(
            requester, spec, arguments, ok=False, error_code=code, request_id=caller.request_id
        )
        raise
    await service.record_read(requester, spec, arguments, ok=True, request_id=caller.request_id)
    return output


def _input_schema(name: str) -> dict[str, Any] | None:
    spec = TOOLS.get(name)
    return spec.input_model.model_json_schema() if spec is not None and available(spec) else None


def build_server() -> Server[Any]:
    return Server(
        SERVER_NAME,
        version=__version__,
        get_tool_input_schema=_input_schema,
        on_list_tools=_list_tools,
        on_call_tool=_call_tool,
    )


def build_manager() -> StreamableHTTPSessionManager:
    """每个应用实例一个（SDK 规定 run() 只能调用一次）。"""
    return StreamableHTTPSessionManager(app=build_server(), json_response=True, stateless=True)


class McpRunner:
    """在单独的后台任务里运行 SDK 的会话管理器。

    管理器内部是一个 anyio 任务组，进入和退出必须在同一个任务里；应用的 lifespan
    在测试里（pytest-asyncio 的 fixture）启动和关闭可能不在同一个任务，
    所以不在 lifespan 里直接 `async with`。
    """

    def __init__(self) -> None:
        self.manager = build_manager()
        self._ready = asyncio.Event()
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="mcp-session-manager")
        ready = asyncio.create_task(self._ready.wait())
        await asyncio.wait({self._task, ready}, return_when=asyncio.FIRST_COMPLETED)
        if not self._ready.is_set():
            ready.cancel()
            await self._task  # 启动失败：把异常抛给 lifespan

    async def _run(self) -> None:
        async with self.manager.run():
            self._ready.set()
            await self._stop.wait()

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
