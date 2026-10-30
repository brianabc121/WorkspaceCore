"""知识入口：AFFiNE 外部地址（知识来源）与文档收藏（docs/DEVELOPMENT_SPEC.md 第 4.2、7.2 节）。

约定见 docs/decisions/2026-10-30-sprint-4-knowledge-search-contract.md 第 3、4 节；
深链接的格式与校验规则见 docs/decisions/2026-10-30-github-rest-and-affine-verification.md 第 8 节。
工作台只保存链接与手工元数据，不向 AFFiNE 发任何请求、不读取文档正文。
链接是否属于已配置的来源在服务端校验（需要来源的地址）。
"""

import ipaddress
from datetime import datetime
from typing import Annotated, Literal, Self
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

# 核验过的 AFFiNE 版本（核验 ADR 第 7 节）
AFFINE_TESTED_VERSION = "0.27.4"
MAX_LINKED_RESOURCES = 20
BOOKMARK_URL_MAX = 2048

KnowledgeProvider = Literal["affine"]
Visibility = Literal["workspace", "private"]
LinkKind = Literal["workspace_doc", "share"]
SourceName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
BookmarkTitle = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
]
BookmarkNote = Annotated[
    str,
    StringConstraints(max_length=2000),
    Field(description="纯文本备注；前端按文本显示，不解析 HTML / Markdown"),
]
Version = Annotated[int, Field(ge=1, description="乐观并发版本号；写请求必须带上读取时的版本")]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _blocked_host(host: str) -> bool:
    """链路本地（含云元数据地址 169.254.169.254）与未指定地址不能作为 AFFiNE 的外部地址（A12）。

    回环与内网地址允许：本机开发时 AFFiNE 常在 http://localhost:3010，链接只在用户自己的浏览器里打开。
    """
    try:
        address = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    return address.is_link_local or address.is_unspecified


def _source_url(value: str) -> str:
    """只检查字符串：工作台从不访问这个地址（契约第 3 节），所以不做 SSRF 解析检查。"""
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("只允许 http:// 或 https:// 地址")
    if parts.username or parts.password:
        raise ValueError("地址里不能包含用户名或密码")
    if parts.query or parts.fragment:
        raise ValueError("地址里不能包含查询串或片段")
    if _blocked_host(parts.hostname):
        raise ValueError("不能使用链路本地或未指定地址")
    return value.rstrip("/")


SourceUrl = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=1024),
    AfterValidator(_source_url),
    Field(
        description="AFFiNE 的 server.externalUrl（可带子路径），与浏览器里打开 AFFiNE 的地址一致"
    ),
]
BookmarkUrl = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=BOOKMARK_URL_MAX),
    Field(
        description="在 AFFiNE 里打开文档时浏览器地址栏的链接；"
        "服务端按核验 ADR 第 8.3 节校验并规范化"
    ),
]


def _unique_ids(values: list[UUID]) -> list[UUID]:
    if len(set(values)) != len(values):
        raise ValueError("不能重复关联同一个资源")
    return values


LinkedResourceIds = Annotated[
    list[UUID],
    Field(
        max_length=MAX_LINKED_RESOURCES,
        description="关联的 Vikunja 项目 / 任务（投影 UUID）；修改时整体替换",
    ),
    AfterValidator(_unique_ids),
]


# ---------------------------------------------------------------- 知识来源


class KnowledgeSourceOut(BaseModel):
    id: UUID
    provider: KnowledgeProvider
    name: str
    base_url: str = Field(description="AFFiNE 的外部地址；收藏的链接都以它开头")
    tested_version: str = Field(description="工作台核验过的 AFFiNE 版本")
    bookmark_count: int = Field(ge=0, description="该来源下的收藏数（含所有人的私有收藏）")
    version: int
    created_at: datetime
    updated_at: datetime


class KnowledgeSourceCreate(_Strict):
    provider: KnowledgeProvider
    name: SourceName
    base_url: SourceUrl


class KnowledgeSourceUpdate(_Strict):
    """修改 base_url 后，该来源下所有收藏的链接都随之改为新地址。"""

    version: Version
    name: SourceName | None = None
    base_url: SourceUrl | None = None

    @model_validator(mode="after")
    def _has_change(self) -> Self:
        changed = self.model_fields_set - {"version"}
        if not changed:
            raise ValueError("至少要修改一项")
        if any(getattr(self, name) is None for name in changed):
            raise ValueError("字段不能为 null")
        return self


# ---------------------------------------------------------------- 收藏


class ResourceLinkOut(BaseModel):
    """关联的项目 / 任务；它在 Vikunja 里删除后不再出现（A10）。"""

    resource_id: UUID = Field(description="项目或任务的投影 UUID")
    resource_type: Literal["task", "project"]
    title: str
    source_url: str = Field(description="Vikunja 原生界面中的地址")


class KnowledgeResourceOut(BaseModel):
    id: UUID
    source_id: UUID
    provider: KnowledgeProvider
    resource_type: Literal["affine_doc"]
    title: str
    note: str = Field(description="纯文本备注；前端按文本显示")
    url: str = Field(description="规范化后的链接：来源地址 + 文档路径 + 保留的查询参数")
    link_kind: LinkKind = Field(
        description="workspace_doc：/workspace/<ws>/<doc>；share：/share/<ws>/<doc>"
    )
    affine_workspace_id: str
    doc_id: str
    visibility: Visibility = Field(description="workspace：成员都能看到；private：只有创建者能看到")
    owner_id: UUID | None = Field(description="创建者；用户被删除后为 null")
    links: list[ResourceLinkOut]
    version: int
    created_at: datetime
    updated_at: datetime


class BookmarkCreate(_Strict):
    url: BookmarkUrl
    title: BookmarkTitle
    note: BookmarkNote = ""
    visibility: Visibility = "workspace"
    linked_resource_ids: LinkedResourceIds = Field(default_factory=list)


class BookmarkUpdate(_Strict):
    """只发送要改的字段，都不能为 null；只有创建者能修改 visibility（否则 403）。"""

    version: Version
    url: BookmarkUrl | None = None
    title: BookmarkTitle | None = None
    note: BookmarkNote | None = None
    visibility: Visibility | None = None
    linked_resource_ids: LinkedResourceIds | None = None

    @model_validator(mode="after")
    def _has_change(self) -> Self:
        changed = self.model_fields_set - {"version"}
        if not changed:
            raise ValueError("至少要修改一项")
        if any(getattr(self, name) is None for name in changed):
            raise ValueError("字段不能为 null")
        return self


KnowledgeVisibilityFilter = Literal["workspace", "private", "all"]
KnowledgeSort = Literal["updated", "title"]
