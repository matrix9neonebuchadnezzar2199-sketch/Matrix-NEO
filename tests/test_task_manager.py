"""TaskManager unit tests."""

from __future__ import annotations

import pytest

from app.models import TaskState, TaskStatus
from app.state import TaskManager


@pytest.fixture
def mgr() -> TaskManager:
    return TaskManager()


@pytest.mark.asyncio
async def test_register_get_exists(mgr: TaskManager) -> None:
    t = TaskState(task_id="a1", url="http://example.com/x", filename="f.mp4")
    await mgr.register(t, credentials={"cookie": "c", "referer": "http://r"})
    assert mgr.exists("a1")
    assert mgr.get("a1") is t
    assert mgr.task_credentials["a1"]["cookie"] == "c"


@pytest.mark.asyncio
async def test_update_ignores_missing(mgr: TaskManager) -> None:
    await mgr.update("missing", status=TaskStatus.COMPLETED)


@pytest.mark.asyncio
async def test_update_mutates(mgr: TaskManager) -> None:
    t = TaskState(task_id="b1", url="http://x", filename="y.mp4")
    await mgr.register(t)
    await mgr.update("b1", progress=50.0, message="hi")
    assert mgr.get("b1") is not None
    assert mgr.get("b1").progress == 50.0
    assert mgr.get("b1").message == "hi"


@pytest.mark.asyncio
async def test_remove(mgr: TaskManager) -> None:
    t = TaskState(task_id="c1", url="http://x", filename="y.mp4")
    await mgr.register(t, credentials={"cookie": None, "referer": None})
    assert await mgr.remove("c1") is t
    assert mgr.get("c1") is None
    assert "c1" not in mgr.task_credentials


@pytest.mark.asyncio
async def test_remove_many(mgr: TaskManager) -> None:
    for i in range(3):
        await mgr.register(
            TaskState(task_id=f"id{i}", url="http://x", filename="a.mp4"),
        )
    n = await mgr.remove_many(["id0", "id2", "nope"])
    assert n == 2
    assert mgr.exists("id1")


@pytest.mark.asyncio
async def test_tasks_by_status_all_tasks(mgr: TaskManager) -> None:
    await mgr.register(TaskState(task_id="q", url="u", filename="f.mp4", status=TaskStatus.QUEUED))
    await mgr.register(
        TaskState(task_id="d", url="u", filename="g.mp4", status=TaskStatus.DOWNLOADING)
    )
    assert len(mgr.all_tasks()) == 2
    assert len(mgr.tasks_by_status(TaskStatus.QUEUED)) == 1


@pytest.mark.asyncio
async def test_reset(mgr: TaskManager) -> None:
    await mgr.register(TaskState(task_id="z", url="u", filename="f.mp4"))
    mgr.reset()
    assert not mgr.tasks


@pytest.mark.asyncio
async def test_update_rejects_unknown_field(mgr: TaskManager) -> None:
    await mgr.register(TaskState(task_id="bad", url="http://x", filename="a.mp4"))
    with pytest.raises(ValueError, match="no field"):
        await mgr.update("bad", not_a_real_field=123)
