"""Atomic global host identity claims; callers hold their server row lock."""

import re
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.host import AgentServer
from app.models.physical_host import PhysicalHostRegistration
from app.models.runner import RunnerHost


async def claim_physical_host(
    db: AsyncSession,
    physical_id: str,
    *,
    workspace_id: UUID,
    role: Literal["agent", "runner"],
    server_id: UUID,
) -> bool:
    """Idempotent only for the same machine, workspace, role and registration.

    A physical ID, or either server ID, is never silently reassigned. Database
    uniqueness arbitrates concurrent cross-pool/workspace claims. Return only
    a boolean, never another workspace's name, role or identifiers.
    """
    if not re.fullmatch(r"[a-f0-9]{64}", physical_id):
        return False
    model = AgentServer if role == "agent" else RunnerHost
    if (
        await db.scalar(
            select(model.id).where(
                model.id == server_id,
                model.workspace_id == workspace_id,
            )
        )
        is None
    ):
        return False
    column = (
        PhysicalHostRegistration.agent_server_id
        if role == "agent"
        else PhysicalHostRegistration.runner_host_id
    )
    await db.execute(
        insert(PhysicalHostRegistration)
        .values(
            physical_id=physical_id,
            workspace_id=workspace_id,
            **{column.key: server_id},
        )
        .on_conflict_do_nothing()
    )
    return (
        await db.scalar(
            select(PhysicalHostRegistration.physical_id).where(
                PhysicalHostRegistration.physical_id == physical_id,
                PhysicalHostRegistration.workspace_id == workspace_id,
                column == server_id,
            )
        )
        is not None
    )
