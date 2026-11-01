"""投影写入的并发（T-0061）：API 删除任务与定时对账同时写同一个集成的投影时不能死锁。

复现 CI 里出现过的死锁：对账每次都会重存项目，项目的搜索触发器会改写这个项目下所有任务的
搜索文档（先锁 search_documents，再锁任务行）；API 删除任务的顺序相反（先锁任务行，
再删它的搜索文档）。修复后，同一个集成的投影写入事务按集成串行（事务级 advisory lock）。
"""

import asyncio
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest

from app.models import ExternalResource
from app.repositories import projection as repo
from tests.vikunja_support import Env, app_of, open_env

pytestmark = pytest.mark.integration


@pytest.fixture
async def env(make_client: Any, migrated_database_url: str) -> AsyncIterator[Env]:
    async with open_env(make_client, migrated_database_url) as value:
        work = value.fake.add_project("工作")
        value.fake.add_task(work["id"], "写周报")
        await value.connect()
        yield value


async def _resave(db: Any, row: ExternalResource) -> None:
    """与同步时一样：按已有的值再 upsert 一次（触发器会重建搜索文档）。"""
    await repo.upsert(
        db,
        workspace_id=row.workspace_id,
        integration_id=row.integration_id,
        resource_type=row.resource_type,
        external_id=row.external_id,
        title=row.title,
        source_url=row.source_url,
        metadata=dict(row.metadata_json),
        source_updated_at=row.source_updated_at,
    )


async def test_tombstone_and_resync_of_the_same_integration_do_not_deadlock(env: Env) -> None:
    sessions = app_of(env.client).state.sessionmaker
    task_id = uuid.UUID((await env.task_by_title("写周报"))["id"])
    async with sessions() as db:
        task = await db.get(ExternalResource, task_id)
        assert task is not None
        project = await repo.get_by_external(
            db,
            integration_id=task.integration_id,
            resource_type=repo.PROJECT,
            external_id=str(task.metadata_json["project_external_id"]),
        )
        assert project is not None

    async with sessions() as sync, sessions() as api:
        # 对账：先重存项目（锁住任务的搜索文档），事务不提交
        project_row = await sync.get(ExternalResource, project.id)
        task_row = await sync.get(ExternalResource, task_id)
        assert project_row is not None and task_row is not None
        await _resave(sync, project_row)

        async def delete_in_api() -> None:
            row = await api.get(ExternalResource, task_id)
            assert row is not None
            await repo.tombstone(api, row)
            await api.commit()

        deleting = asyncio.create_task(delete_in_api())
        await asyncio.sleep(0.3)  # 让 API 的删除先走到等锁的地方
        # 对账接着存任务：修复前这里与 API 的删除互相等待，PostgreSQL 判定死锁让其中一个失败
        await _resave(sync, task_row)
        await sync.commit()
        await asyncio.wait_for(deleting, timeout=10)

    async with sessions() as db:
        row = await db.get(ExternalResource, task_id)
        assert row is not None and row.deleted_at is not None
