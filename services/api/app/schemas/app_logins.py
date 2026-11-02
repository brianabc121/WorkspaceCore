"""n8n 管理界面的登录账号（T-0080，docs/decisions/2026-11-02-app-logins.md）。

T-0082 移除了 Vikunja，之后只有 n8n。

只有 admin 以上能读写。列表只返回账号与“是否保存了密码”，
密码要单独调用 reveal 才返回，每次都写审计。
"""

from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, SecretStr, StringConstraints, model_validator

App = Literal["n8n"]
Username = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=254),
    Field(description="n8n 的登录邮箱"),
]
Password = Annotated[SecretStr, Field(min_length=1, max_length=256)]


class AppLoginOut(BaseModel):
    app: App
    configured: bool = Field(description="false：还没有保存账号，其余字段为默认值")
    username: str | None = None
    password_set: bool = False
    login_url: str | None = Field(
        default=None,
        description="浏览器打开登录页的地址（n8n 连接的浏览器地址）；没有配置时为 null",
    )
    updated_at: datetime | None = None
    version: int | None = None


class AppLoginList(BaseModel):
    items: list[AppLoginOut] = Field(description="固定一项：n8n")


class AppLoginUpsert(BaseModel):
    """保存或修改登录账号（admin）。新建时必须给 username；修改时必须带 version。"""

    model_config = ConfigDict(extra="forbid")

    version: int | None = Field(default=None, ge=1, description="修改时必填；新建时不填")
    username: Username | None = None
    password: Password | None = Field(default=None, description="不填表示不改密码")

    @model_validator(mode="after")
    def _has_change(self) -> Self:
        if not (self.model_fields_set - {"version"}):
            raise ValueError("至少要修改一项")
        return self


class AppLoginSecret(BaseModel):
    app: App
    username: str
    password: str = Field(description="明文密码；响应带 Cache-Control: no-store")
