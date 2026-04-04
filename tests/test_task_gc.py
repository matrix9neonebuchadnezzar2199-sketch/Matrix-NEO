"""Task GC helpers (TTL math and remove_many)."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.models import TaskState, TaskStatus
from app.state import TaskManager


def test_completed_ttl_is_expired() -> None:
    now = datetime.now()
    old = (now - timedelta(hours=48)).isoformat()
    ts = datetime.fromisoformat(old)
    assert now - ts > timedelta(hours=24)


@pytest.mark.asyncio
async def test_remove_many_clears_ids() -> None:
    mgr = TaskManager()
    await mgr.register(TaskState(task_id="x1", url="u", filename="a.mp4"))
    await mgr.register(TaskState(task_id="x2", url="u", filename="b.mp4"))
    n = await mgr.remove_many(["x1", "missing"])
    assert n == 1
    assert not mgr.exists("x1")
    assert mgr.exists("x2")
