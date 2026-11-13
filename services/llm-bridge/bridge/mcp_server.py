"""MCP 服务：SDK 2.3.0 的底层 Server + StreamableHTTPSessionManager（无状态、JSON 响应），

写法与工作台的 services/api/app/domain/mcp_tools.py 相同。调用方由 app.py 的路由认证后放进
请求的 state；工具只做参数校验和转给 engine。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

import mcp_types as types
from mcp.server.context import ServerRequestContext
from mcp.server.lowlevel.server import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from pydantic import ValidationError
from starlette.requests import Request

from bridge import __version__
from bridge.engine import BridgeError, Caller, Engine
from bridge.tools import TOOL_MAP, TOOLS, ToolDef

SERVER_NAME = "llm-bridge"
CALLER_STATE_KEY = "bridge_caller"


def _request(ctx: ServerRequestContext[Any, Any]) -> Request:
    request = ctx.request
    if not isinstance(request, Request):
        raise RuntimeError("MCP 请求缺少 HTTP 上下文")
    return request


def _caller(ctx: ServerRequestContext[Any, Any]) -> Caller:
    caller = getattr(_request(ctx).state, CALLER_STATE_KEY, None)
    if not isinstance(caller, Caller):
        raise RuntimeError("MCP 请求没有经过认证")
    return caller


def tool_definition(tool: ToolDef) -> types.Tool:
    return types.Tool(
        name=tool.name,
        description=tool.description,
        input_schema=tool.input_model.model_json_schema(),
        annotations=types.ToolAnnotations(
            read_only_hint=tool.read_only,
            destructive_hint=tool.name == "llm_session_archive",
            idempotent_hint=tool.read_only or tool.name == "llm_send",
            open_world_hint=not tool.read_only,
        ),
    )


def error_result(code: str, message: str) -> types.CallToolResult:
    payload = {"error": {"code": code, "message": message}}
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))],
        is_error=True,
    )


def ok_result(payload: dict[str, Any]) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))],
        structured_content=payload,
        is_error=False,
    )


async def run_tool(
    engine: Engine, caller: Caller, name: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    """执行一个工具，返回结果；失败抛 BridgeError。"""
    tool = TOOL_MAP.get(name)
    if tool is None:
        raise BridgeError("NOT_FOUND", f"没有这个工具：{name}")
    try:
        data = tool.input_model.model_validate(arguments)
    except ValidationError as error:
        first = error.errors()[0]
        where = ".".join(str(part) for part in first["loc"])
        raise BridgeError("VALIDATION_FAILED", f"参数不正确：{where} {first['msg']}") from error
    args = data.model_dump()
    if name == "llm_status":
        return await engine.overview()
    if name == "llm_models":
        return {"models": await engine.models(args["platform"], args["mode"])}
    if name == "llm_environments":
        return {"environments": engine.environments(args["platform"], args["mode"])}
    if name == "llm_session_create":
        return await engine.create_session(caller, **args)
    if name == "llm_send":
        return await engine.send(caller, **args)
    if name == "llm_turn_get":
        return await engine.get_turn(caller, args["turn_id"], args["wait_seconds"])
    if name == "llm_turn_cancel":
        return await engine.cancel_turn(caller, args["turn_id"])
    if name == "llm_session_list":
        return await engine.list_sessions(caller, **args)
    if name == "llm_session_get":
        return await engine.get_session(
            caller, args["session_id"], limit=args["limit"], offset=args["offset"]
        )
    return await engine.archive_session(caller, args["session_id"])


async def _list_tools(
    ctx: ServerRequestContext[Any, Any], params: types.PaginatedRequestParams | None
) -> types.ListToolsResult:
    caller = _caller(ctx)
    # 没有 write 权限的调用方只看到读工具
    return types.ListToolsResult(
        tools=[tool_definition(t) for t in TOOLS if t.read_only or caller.can("write")]
    )


async def _call_tool(
    ctx: ServerRequestContext[Any, Any], params: types.CallToolRequestParams
) -> types.CallToolResult:
    engine: Engine = _request(ctx).app.state.engine
    try:
        return ok_result(await run_tool(engine, _caller(ctx), params.name, params.arguments or {}))
    except BridgeError as error:
        return error_result(error.code, error.message)


def _input_schema(name: str) -> dict[str, Any] | None:
    tool = TOOL_MAP.get(name)
    return tool.input_model.model_json_schema() if tool is not None else None


def build_manager() -> StreamableHTTPSessionManager:
    server: Server[Any] = Server(
        SERVER_NAME,
        version=__version__,
        get_tool_input_schema=_input_schema,
        on_list_tools=_list_tools,
        on_call_tool=_call_tool,
    )
    return StreamableHTTPSessionManager(app=server, json_response=True, stateless=True)


class McpRunner:
    """在单独的后台任务里运行会话管理器（进入和退出必须在同一个任务里）。"""

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
            await self._task

    async def _run(self) -> None:
        async with self.manager.run():
            self._ready.set()
            await self._stop.wait()

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
