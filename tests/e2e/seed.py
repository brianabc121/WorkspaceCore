#!/usr/bin/env python3
"""全栈验收的测试数据：在 api 容器里用应用自身的代码写入（V1 只能通过首次安装创建用户）。

用法（同目录的验收脚本直接导入；Node 写的浏览器验收通过子进程调用）：
  E2E_PASSWORD=… python3 tests/e2e/seed.py user <邮箱> <显示名>
      输出“用户ID 工作空间ID 默认仪表盘ID”
  python3 tests/e2e/seed.py member <工作空间ID> <用户ID> <角色>
  python3 tests/e2e/seed.py corrupt <仪表盘ID>
      输出组件 ID
新用户自带工作空间“<显示名> 的空间”和默认首页（与首次安装一致）。
只依赖 Python 标准库与 docker compose；Compose 全栈需已启动并迁移。
"""

import os
import sys

from e2e_support import compose_exec

# 在 api 容器里运行：写入用户（含工作空间与默认首页）、成员关系或一条已损坏的组件
SEED_SCRIPT = r"""
import asyncio, os, sys, uuid
from sqlalchemy.ext.asyncio import async_sessionmaker
from app.core.config import get_settings
from app.core.resources import create_engine
from app.domain import dashboard_defaults
from app.domain.security import hash_password
from app.models import Widget, WorkspaceMember
from app.repositories import users, workspaces

async def main():
    engine = create_engine(get_settings())
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            command = sys.argv[1]
            if command == "user":
                user = await users.create(
                    db,
                    email=os.environ["E2E_EMAIL"],
                    password_hash=hash_password(os.environ["E2E_PASSWORD"]),
                    display_name=os.environ["E2E_NAME"],
                )
                workspace = await workspaces.create_with_owner(
                    db, name=os.environ["E2E_NAME"] + " 的空间", owner_id=user.id
                )
                dashboard = await dashboard_defaults.create_default_dashboard(
                    db, workspace_id=workspace.id, owner_id=user.id
                )
                print(user.id, workspace.id, dashboard.id)
            elif command == "member":
                workspace_id, user_id, role = sys.argv[2:5]
                db.add(WorkspaceMember(
                    workspace_id=uuid.UUID(workspace_id), user_id=uuid.UUID(user_id), role=role
                ))
            else:
                # 模拟旧版本留下或被手工改坏的数据：已不支持的类型、不是对象的配置、无法解析的布局
                widget = Widget(
                    dashboard_id=uuid.UUID(sys.argv[2]),
                    widget_type="retired_widget",
                    config_json="not-an-object",
                    layout_json={"desktop": "broken"},
                )
                db.add(widget)
                await db.flush()
                print(widget.id)
            await db.commit()
    finally:
        await engine.dispose()

asyncio.run(main())
"""


def _run(*args: str, env: dict[str, str] | None = None) -> list[str]:
    return compose_exec("api", "python", "-c", SEED_SCRIPT, *args, env=env).split()


def create_user(email: str, name: str, password: str) -> tuple[str, str, str]:
    """返回 (用户 ID, 自己的工作空间 ID, 默认仪表盘 ID)。"""
    env = {"E2E_EMAIL": email, "E2E_PASSWORD": password, "E2E_NAME": name}
    user_id, workspace_id, dashboard_id = _run("user", env=env)[-3:]
    return user_id, workspace_id, dashboard_id


def add_member(workspace_id: str, user_id: str, role: str) -> None:
    _run("member", workspace_id, user_id, role)


def add_corrupt_widget(dashboard_id: str) -> str:
    return _run("corrupt", dashboard_id)[-1]


def main(argv: list[str]) -> int:
    match argv:
        case ["user", email, name]:
            password = os.environ.get("E2E_PASSWORD")
            if not password:
                sys.exit("请用环境变量 E2E_PASSWORD 提供测试账号的口令")
            print(*create_user(email, name, password))
        case ["member", workspace_id, user_id, role]:
            add_member(workspace_id, user_id, role)
        case ["corrupt", dashboard_id]:
            print(add_corrupt_widget(dashboard_id))
        case _:
            sys.exit(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
