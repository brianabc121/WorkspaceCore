#!/usr/bin/env python3
"""新建一个插拔式模块的骨架（docs/modules/README.md 第 4 节）。

    make module-new key=finance name=记账
    python3 scripts/new_module.py --key finance --name 记账 [--description ...] [--dry-run]

生成后端、前端两个文件夹和测试，并在前后端注册表里各插入一行。生成的模块默认停用，
有一个 GET /api/v1/<key>/status 接口和一个“应用”分组下的页面，测试覆盖“停用时 404、启用后 200”。
之后按指南补数据表、接口、页面、工具。只用标准库，可以在仓库根目录直接运行。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API = ROOT / "services" / "api"
WEB = ROOT / "apps" / "web"
BACKEND_REGISTRY = API / "app" / "modules" / "registry.py"
FRONTEND_REGISTRY = WEB / "src" / "modules" / "registry.ts"
BACKEND_MARKER = "    # module-new:"
FRONTEND_MARKER = "  // module-new:"
KEY = re.compile(r"[a-z][a-z0-9_]{1,31}")


def pascal(key: str) -> str:
    return "".join(part.capitalize() for part in key.split("_"))


def camel(key: str) -> str:
    name = pascal(key)
    return name[0].lower() + name[1:]


def backend_files(key: str, name: str, description: str) -> dict[Path, str]:
    base = API / "app" / "modules" / key
    return {
        base / "__init__.py": "",
        base / "manifest.py": f'''"""{name}模块的清单（模块写法见 docs/modules/README.md）。"""

from app.modules.base import ModuleManifest

MANIFEST = ModuleManifest(
    key="{key}",
    name="{name}",
    description="{description}",
    version="0.1.0",
    # 新模块默认停用：owner 在“应用中心”启用
    default_enabled=False,
    router="app.modules.{key}.api:router",
)
''',
        base / "api.py": f'''"""{name}的页面接口：都挂在 /api/v1/{key} 下，宿主自动加启用检查。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Security
from pydantic import BaseModel

from app.api.deps import authenticated, workspace_viewer
from app.domain.policy import WorkspaceAccess
from app.schemas.common import error_responses

router = APIRouter(tags=["{key}"], dependencies=[Security(authenticated)])

Viewer = Annotated[WorkspaceAccess, Depends(workspace_viewer)]


class {pascal(key)}Status(BaseModel):
    workspace_id: str
    message: str


@router.get(
    "/{key}/status",
    summary="{name}的状态（viewer）",
    responses=error_responses(401, 404),
)
async def {key}_status(access: Viewer) -> {pascal(key)}Status:
    return {pascal(key)}Status(workspace_id=str(access.workspace.id), message="{name}已启用")
''',
        API / "tests" / f"test_module_{key}.py": f'''"""{name}模块：清单合法；停用时接口 404 MODULE_DISABLED，启用后可用。"""

from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx
import pytest

from app.modules import registry
from app.modules.base import validate
from app.modules.{key}.manifest import MANIFEST
from tests.helpers import OWNER, ClientFactory, csrf


@dataclass
class Env:
    client: httpx.AsyncClient
    workspace_id: str

    def headers(self, write: bool = False) -> dict[str, str]:
        extra = csrf(self.client) if write else {{}}
        return {{"X-Workspace-Id": self.workspace_id, **extra}}


@pytest.fixture
async def env(make_client: ClientFactory) -> AsyncIterator[Env]:
    async with make_client() as client:
        setup = await client.post("/api/v1/setup", json=OWNER)
        assert setup.status_code == 201
        yield Env(client, setup.json()["workspaces"][0]["id"])


def test_manifest_is_valid_and_registered() -> None:
    assert validate(MANIFEST) == []
    assert registry.get("{key}") is MANIFEST


async def test_status_needs_the_module_enabled(env: Env) -> None:
    disabled = await env.client.get("/api/v1/{key}/status", headers=env.headers())
    assert disabled.status_code == 404
    assert disabled.json()["error"]["code"] == "MODULE_DISABLED"

    enable = await env.client.patch(
        "/api/v1/modules/{key}", json={{"enabled": True}}, headers=env.headers(write=True)
    )
    assert enable.status_code == 200, enable.text
    enabled = await env.client.get("/api/v1/{key}/status", headers=env.headers())
    assert enabled.status_code == 200
    assert enabled.json()["workspace_id"] == env.workspace_id
''',
    }


def frontend_files(key: str, name: str, description: str) -> dict[Path, str]:
    base = WEB / "src" / "modules" / key
    page = f"{pascal(key)}Page"
    return {
        base / "index.ts": f"""import {{ defineModule }} from '../types'

// {name}（模块写法见 docs/modules/README.md）。页面按需加载，模块停用时侧栏不显示
export default defineModule({{
  key: '{key}',
  nav: [
    {{ path: '/{key.replace('_', '-')}', title: '{name}', section: '应用', description: '{description}' }},
  ],
  routes: [
    {{ path: '/{key.replace('_', '-')}', component: () => import('./{page}.vue'), meta: {{ title: '{name}' }} }},
  ],
}})
""",
        base / f"{page}.vue": f"""<script setup lang="ts">
// {name}的页面骨架：按 docs/modules/README.md 补数据和交互
</script>

<template>
  <section class="page">
    <h1>{name}</h1>
    <p>{description}</p>
  </section>
</template>
""",
    }


def insert_before(path: Path, marker: str, line: str, dry_run: bool) -> None:
    text = path.read_text(encoding="utf-8")
    if line in text:
        return
    if marker not in text:
        raise SystemExit(f"{path} 里找不到标记 {marker.strip()!r}，请手动登记")
    updated = text.replace(marker, f"{line}\n{marker}", 1)
    if not dry_run:
        path.write_text(updated, encoding="utf-8")


def register(key: str, dry_run: bool) -> None:
    text = BACKEND_REGISTRY.read_text(encoding="utf-8")
    alias = key.upper()
    import_line = f"from app.modules.{key}.manifest import MANIFEST as {alias}"
    if import_line not in text:
        anchor = "from app.modules.news.manifest import MANIFEST as NEWS"
        if anchor not in text:
            raise SystemExit("后端注册表的导入区找不到，请手动登记")
        text = text.replace(anchor, f"{anchor}\n{import_line}", 1)
        if not dry_run:
            BACKEND_REGISTRY.write_text(text, encoding="utf-8")
    insert_before(BACKEND_REGISTRY, BACKEND_MARKER, f"    {alias},", dry_run)

    text = FRONTEND_REGISTRY.read_text(encoding="utf-8")
    import_line = f"import {camel(key)} from './{key}'"
    if import_line not in text:
        imports = [line for line in text.splitlines() if line.startswith("import ")]
        if not imports:
            raise SystemExit("前端注册表的导入区找不到，请手动登记")
        last = imports[-1]
        text = text.replace(last, f"{last}\n{import_line}", 1)
        if not dry_run:
            FRONTEND_REGISTRY.write_text(text, encoding="utf-8")
    insert_before(FRONTEND_REGISTRY, FRONTEND_MARKER, f"  {camel(key)},", dry_run)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--key", required=True, help="模块 key：小写字母开头，小写字母、数字、下划线")
    parser.add_argument("--name", required=True, help="中文名，显示在侧栏和应用中心")
    parser.add_argument("--description", default="", help="一句话说明")
    parser.add_argument("--dry-run", action="store_true", help="只打印要生成的文件")
    args = parser.parse_args(argv)
    key: str = args.key
    if not KEY.fullmatch(key):
        parser.error("key 只能是小写字母开头的小写字母、数字、下划线，2~32 个字符")
    if (API / "app" / "modules" / key).exists():
        parser.error(f"模块 {key} 已经存在")
    description = args.description or f"{args.name}（新模块，待补充说明）"
    files = {**backend_files(key, args.name, description), **frontend_files(key, args.name, description)}
    for path, content in files.items():
        print(("将生成 " if args.dry_run else "生成 ") + str(path.relative_to(ROOT)))
        if not args.dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
    register(key, args.dry_run)
    print(("将登记到 " if args.dry_run else "已登记到 ") + "前后端模块注册表")
    print("下一步：按 docs/modules/README.md 第 5 节补数据表、接口、页面和测试，然后跑检查命令。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
