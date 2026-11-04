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
      性能基准用的数据量：一个项目和其中的若干任务（T-0082 起是工作台自己的数据），
      以及若干条通知
  python3 tests/e2e/seed.py timeline <工作空间ID> <任务ID> <实例名>
      时间轴浏览器验收（T-0106）：一个实例（Compose 验收里没有真实 OpenClaw）、它在这个任务上
      40 分钟前到 10 分钟前完成的一段执行，以及这段时间每分钟上报的 token；任务标为完成。输出实例 ID
  python3 tests/e2e/seed.py cleanup-files <工作空间ID>
      知识库图片清理（T-0106）：把这个工作空间的图片的上传时间改到 2 天前，再运行 Worker 的每日清理，
      输出删掉的张数（没有笔记引用的才会删）
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
from sqlalchemy import update
from app.domain import dashboard_defaults, events, knowledge_files, notifications
from app.domain.security import hash_password
from app.models import (
    AgentInstance, AgentUsageMinute, KnowledgeFile, Project, Task, TaskRun, Widget, WorkspaceMember,
)
from app.repositories import users, workspaces

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
                project = Project(workspace_id=workspace_id, title="perf-" + uuid.uuid4().hex[:8])
                db.add(project)
                await db.flush()
                now = datetime.now(UTC)
                for i in range(task_count):
                    db.add(Task(
                        workspace_id=workspace_id, project_id=project.id,
                        title=f"性能基准任务 {i}", done=i % 4 == 0,
                        done_at=now if i % 4 == 0 else None, priority=i % 6, labels=["perf"],
                        due_at=now + timedelta(hours=i - task_count // 2),
                    ))
                for i in range(notice_count):
                    await notifications.notify_user(
                        db, workspace_id, user_id, type_="e2e_notice", severity="info",
                        title=f"性能基准通知 {i}", body="", link=None,
                        key="perf-" + uuid.uuid4().hex,
                    )
            elif command == "timeline":
                workspace_id, task_id, name = uuid.UUID(sys.argv[2]), uuid.UUID(sys.argv[3]), sys.argv[4]
                now = datetime.now(UTC).replace(second=0, microsecond=0)
                instance = AgentInstance(
                    workspace_id=workspace_id, name=name, network_mode="intranet", status="online",
                    im_user_id="oc_" + uuid.uuid4().hex, im_nickname=name, im_ready=True,
                    last_seen_at=now, usage_reported_at=now,
                )
                db.add(instance)
                await db.flush()
                started, ended = now - timedelta(minutes=40), now - timedelta(minutes=10)
                db.add(TaskRun(
                    workspace_id=workspace_id, task_id=task_id, executor_kind="instance",
                    instance_id=instance.id, executor_name=name, started_at=started,
                    ended_at=ended, end_reason="done",
                ))
                for i in range(30):
                    db.add(AgentUsageMinute(
                        instance_id=instance.id, workspace_id=workspace_id,
                        minute=started + timedelta(minutes=i), input=1000 + i * 10, output=250,
                        cache_read=2000, cache_write=50,
                    ))
                await db.execute(update(Task).where(Task.id == task_id).values(
                    assignee_instance_id=instance.id, started_at=started, done=True, done_at=ended,
                ))
                print(instance.id)
            elif command == "cleanup-files":
                workspace_id = uuid.UUID(sys.argv[2])
                await db.execute(update(KnowledgeFile).where(KnowledgeFile.workspace_id == workspace_id)
                                 .values(created_at=datetime.now(UTC) - timedelta(days=2)))
                print(await knowledge_files.cleanup_unreferenced(db, knowledge_files.FileStores(settings)))
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


def seed_timeline(workspace_id: str, task_id: str, name: str) -> str:
    return _run("timeline", workspace_id, task_id, name)[-1]


def cleanup_files(workspace_id: str) -> int:
    return int(_run("cleanup-files", workspace_id)[-1])


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
        case ["timeline", workspace_id, task_id, name]:
            print(seed_timeline(workspace_id, task_id, name))
        case ["cleanup-files", workspace_id]:
            print(cleanup_files(workspace_id))
        case _:
            sys.exit(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
