from __future__ import annotations

import asyncio


async def rollback_shielded(session) -> None:
    task = asyncio.create_task(session.rollback())
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise


async def safe_close(session) -> None:
    task = asyncio.create_task(session.close())
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise


def safe_task_error() -> RuntimeError:
    return RuntimeError("PHASE1_WORKFLOW_DEPENDENCY_UNAVAILABLE")
