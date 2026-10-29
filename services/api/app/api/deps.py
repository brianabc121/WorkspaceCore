"""路由共用的依赖：会话 Cookie、CSRF 头、工作空间上下文。

约定见 docs/decisions/2026-10-30-sprint-1-auth-contract.md。
T-0009 只声明签名（决定 OpenAPI 的样子），校验逻辑由 T-0010 实现；
实现时保持参数不变，openapi.json 就不会因此改变。
"""

from typing import Annotated
from uuid import UUID

from fastapi import Header, Security
from fastapi.security import APIKeyCookie

SESSION_COOKIE = "ws_session"
CSRF_COOKIE = "ws_csrf"
CSRF_HEADER = "X-CSRF-Token"
WORKSPACE_HEADER = "X-Workspace-Id"

session_cookie = APIKeyCookie(
    name=SESSION_COOKIE,
    scheme_name="SessionCookie",
    description="登录后由服务端设置的 HttpOnly 会话 Cookie；浏览器同源请求会自动携带",
    auto_error=False,
)


async def authenticated(token: Annotated[str | None, Security(session_cookie)]) -> None:
    """要求有效会话，否则 401 UNAUTHORIZED。（T-0010 实现）"""


async def csrf_protected(
    csrf_token: Annotated[
        str | None,
        Header(
            alias=CSRF_HEADER,
            description=(
                f"改变状态的请求必须携带，值为 {CSRF_COOKIE} Cookie 的内容；"
                "缺失或不匹配返回 403 CSRF_FAILED"
            ),
        ),
    ] = None,
) -> None:
    """校验 CSRF Token 与 Origin / Referer。（T-0010 实现）"""


async def workspace_context(
    workspace_id: Annotated[
        UUID,
        Header(alias=WORKSPACE_HEADER, description="当前工作空间；不是成员时返回 404"),
    ],
) -> UUID:
    """工作空间范围的接口通过请求头指定工作空间。（成员与角色校验由 T-0011 实现）"""
    return workspace_id
