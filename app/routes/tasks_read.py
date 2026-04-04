"""Task listing and status (secrets redacted)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from app.models import TaskStatus
from app.state import tm
from app.utils.task_sanitize import sanitize_task, sanitize_tasks_list

router = APIRouter(tags=["tasks"])
logger = logging.getLogger(__name__)


@router.get("/tasks")
async def get_tasks():
    return {"tasks": sanitize_tasks_list(tm.all_tasks())}


@router.get("/status/{task_id}")
async def get_status(task_id: str):
    task = tm.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return sanitize_task(task)


@router.delete("/task/{task_id}")
async def delete_task(task_id: str):
    if task_id in tm.active_downloads:
        tm.active_downloads[task_id].cancel()
        del tm.active_downloads[task_id]
    if await tm.remove(task_id) is not None:
        return {"status": "deleted"}
    raise HTTPException(status_code=404, detail="Task not found")


@router.get("/tasks/stopped")
async def get_stopped_tasks():
    result = []
    for task_id, task in tm.tasks.items():
        if task.status == TaskStatus.STOPPED:
            result.append({"task_id": task_id, **sanitize_task(task)})
    return result
