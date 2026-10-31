#!/usr/bin/env python3
"""全栈验收的测试数据：在 api 容器里用应用自身的代码写入（V1 只能通过首次安装创建用户）。

用法（同目录的验收脚本直接导入；Node 写的浏览器验收通过子进程调用）：
  E2E_PASSWORD=… python3 tests/e2e/seed.py user <邮箱> <显示名>
      输出“用户ID 工作空间ID 默认仪表盘ID”
  python3 tests/e2e/seed.py member <工作空间ID> <用户ID> <角色>
  python3 tests/e2e/seed.py corrupt <仪表盘ID>
      输出组件 ID
  python3 tests/e2e/seed.py notify <工作空间ID> <用户ID> <标识> <标题>
      给用户写一条通知，并像应用自身一样发布数据失效事件（SSE）
  python3 tests/e2e/seed.py bulk <工作空间ID> <用户ID> <任务数> <通知数>
      性能基准用的数据量：一个没有凭据的 Vikunja 集成（Worker 不会同步它）、
      它的任务投影与同步游标，以及若干条通知
新用户自带工作空间“<显示名> 的空间”和默认首页（与首次安装一致）。
只依赖 Python 标准库与 docker compose；Compose 全栈需已启动并迁移。
"""

import os
import sys

from e2e_support import compose_exec

# 在 api 容器里运行：写入用户（含工作空间与默认首页）、成员关系或一条已损坏的组件
SEED_SCRIPT = r"""
import asyncio, os, sys, uuid
from datetime import UTC, datetime, timedelta
from sqlalchemy.ext.asyncio import async_sessionmaker
from app.core.config import get_settings
from app.core.resources import create_engine, create_redis
from app.domain import dashboard_defaults, events, notifications
from app.domain.security import hash_password
from app.models import ExternalResource, Integration, Widget, WorkspaceMember
from app.repositories import integrations as integration_repo, users, workspaces

async def main():
    settings = get_settings()
    engine = create_engine(settings)
    redis = create_redis(settings)
    bus = events.EventBus(redis)
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False, info=bus.session_info())
        async with sessions() as db:
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
            elif command == "notify":
                workspace_id, user_id, key, title = sys.argv[2:6]
                await notifications.notify_user(
                    db, uuid.UUID(workspace_id), uuid.UUID(user_id), type_="e2e_notice",
                    severity="info", title=title, body="<b>按纯文本显示</b>",
                    link="/operations", key=key,
                )
            elif command == "bulk":
                workspace_id, user_id = uuid.UUID(sys.argv[2]), uuid.UUID(sys.argv[3])
                task_count, notice_count = int(sys.argv[4]), int(sys.argv[5])
                vikunja = "http://vikunja:3456"
                integration = Integration(
                    workspace_id=workspace_id, provider="vikunja", status="active",
                    name="perf-" + uuid.uuid4().hex[:8], base_url=vikunja, public_url=vikunja,
                )
                db.add(integration)
                await db.flush()
                now = datetime.now(UTC)
                for i in range(task_count):
                    db.add(ExternalResource(
                        workspace_id=workspace_id, integration_id=integration.id,
                        resource_type="task", external_id=str(100000 + i),
                        title=f"性能基准任务 {i}", source_url=f"{vikunja}/tasks/{100000 + i}",
                        source_updated_at=now,
                        metadata_json={
                            "description": "", "done": i % 4 == 0, "priority": i % 6,
                            "labels": ["perf"],
                            "due_at": (now + timedelta(hours=i - task_count // 2)).isoformat(),
                        },
                    ))
                # 同步游标：任务模块据此认为投影是新的（否则显示“尚未同步”，不查询任务）
                await integration_repo.upsert_cursor(db, integration.id, "task", success_at=now)
                for i in range(notice_count):
                    await notifications.notify_user(
                        db, workspace_id, user_id, type_="e2e_notice", severity="info",
                        title=f"性能基准通知 {i}", body="", link=None,
                        key="perf-" + uuid.uuid4().hex,
                    )
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
        await bus.drain()
    finally:
        await engine.dispose()
        await redis.aclose()

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


def notify(workspace_id: str, user_id: str, key: str, title: str) -> None:
    _run("notify", workspace_id, user_id, key, title)


def bulk(workspace_id: str, user_id: str, tasks: int, notices: int) -> None:
    _run("bulk", workspace_id, user_id, str(tasks), str(notices))


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
        case ["notify", workspace_id, user_id, key, title]:
            notify(workspace_id, user_id, key, title)
        case ["bulk", workspace_id, user_id, tasks, notices]:
            bulk(workspace_id, user_id, int(tasks), int(notices))
        case _:
            sys.exit(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
