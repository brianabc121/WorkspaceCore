"""AFFiNE 深链接的校验与规范化（核验 ADR 第 8.3 节，Sprint 4 契约第 4.2 节）。

只看字符串，不发任何请求。路由结构来自 AFFiNE tag v0.27.4 的源码：
`/workspace/:workspaceId/:pageId` 与 `/share/:workspaceId/:pageId`；文档链接可带的查询参数见
`modules/navigation/utils.ts`。
"""

import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import parse_qsl, urlencode, urlsplit

LinkKind = Literal["workspace_doc", "share"]
LinkReason = Literal[
    "scheme_not_allowed", "credentials_not_allowed", "host_mismatch", "not_a_document", "invalid_id"
]

# workbench-router.ts 中工作空间下的功能页，不是文档
FEATURE_PAGES = frozenset({"all", "collection", "tag", "trash", "journals", "settings", "chat"})
# 保留的查询参数及顺序；refreshKey 只在 AFFiNE 前端内部使用，丢弃
KEPT_PARAMS = ("mode", "blockIds", "elementIds", "databaseId", "databaseRowId", "commentId")
_MODES = frozenset({"page", "edgeless"})
_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
_PARAM_MAX = 512
_DEFAULT_PORTS = {"http": 80, "https": 443}

_MESSAGES: dict[LinkReason, str] = {
    "scheme_not_allowed": "只接受与 AFFiNE 地址相同协议的 http(s) 链接",
    "credentials_not_allowed": "链接里不能包含用户名或密码",
    "host_mismatch": "链接不属于已配置的 AFFiNE 地址",
    "not_a_document": "请复制 AFFiNE 文档页面的链接（/workspace/…/文档 或 /share/…）",
    "invalid_id": "链接里的工作空间或文档 ID 不合规",
}


class LinkError(ValueError):
    def __init__(self, reason: LinkReason) -> None:
        super().__init__(_MESSAGES[reason])
        self.reason: LinkReason = reason


@dataclass(frozen=True)
class DocLink:
    link_kind: LinkKind
    affine_workspace_id: str
    doc_id: str
    # 来源地址之后的部分（路径 + 保留的查询参数）
    suffix: str


def _origin(scheme: str, host: str | None, port: int | None) -> tuple[str, str, int]:
    scheme = scheme.lower()
    return scheme, (host or "").lower(), port or _DEFAULT_PORTS.get(scheme, 0)


def _query(raw: str) -> str:
    kept: dict[str, str] = {}
    for key, value in parse_qsl(raw, keep_blank_values=False):
        if key not in KEPT_PARAMS or key in kept or len(value) > _PARAM_MAX:
            continue
        if key == "mode" and value not in _MODES:
            continue
        kept[key] = value
    ordered = [(key, kept[key]) for key in KEPT_PARAMS if key in kept]
    return urlencode(ordered, safe=",")


def parse_doc_link(url: str, base_url: str) -> DocLink:
    """把用户粘贴的链接解析为属于 base_url 的文档链接；不合规时抛出 LinkError。"""
    url = url.strip()
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as error:  # 端口不是数字等
        raise LinkError("host_mismatch") from error
    if parts.scheme.lower() not in _DEFAULT_PORTS:
        raise LinkError("scheme_not_allowed")
    if parts.username or parts.password:
        raise LinkError("credentials_not_allowed")
    base = urlsplit(base_url)
    if parts.scheme.lower() != base.scheme.lower():
        raise LinkError("scheme_not_allowed")
    if _origin(parts.scheme, parts.hostname, port) != _origin(
        base.scheme, base.hostname, base.port
    ):
        raise LinkError("host_mismatch")

    prefix = base.path.rstrip("/")
    path = parts.path
    if prefix:
        if path != prefix and not path.startswith(prefix + "/"):
            raise LinkError("host_mismatch")
        path = path[len(prefix) :]
    segments = path.removesuffix("/").split("/")
    if len(segments) != 4 or segments[0] != "" or segments[1] not in {"workspace", "share"}:
        raise LinkError("not_a_document")
    _, kind, workspace_id, doc_id = segments
    if not _ID.fullmatch(workspace_id) or not _ID.fullmatch(doc_id):
        raise LinkError("invalid_id")
    if kind == "workspace" and doc_id in FEATURE_PAGES:
        raise LinkError("not_a_document")

    suffix = f"/{kind}/{workspace_id}/{doc_id}"
    if query := _query(parts.query):
        suffix += "?" + query
    return DocLink(
        link_kind="workspace_doc" if kind == "workspace" else "share",
        affine_workspace_id=workspace_id,
        doc_id=doc_id,
        suffix=suffix,
    )


def build_url(base_url: str, suffix: str) -> str:
    return base_url.rstrip("/") + suffix
