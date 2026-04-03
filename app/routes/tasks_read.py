"""Task listing and status (secrets redacted)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from app import state
from app.utils.task_sanitize import sanitize_task, sanitize_tasks_list

router = APIRouter(tags=["tasks"])
logger = logging.getLogger(__name__)


@router.get("/tasks")
async def get_tasks():
    return {"tasks": sanitize_tasks_list(list(state.tasks.values()))}


@router.get("/status/{task_id}")
async def get_status(task_id: str):
    if task_id not in state.tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    return sanitize_task(state.tasks[task_id])


@router.delete("/task/{task_id}")
async def delete_task(task_id: str):
    if task_id in state.active_downloads:
        state.active_downloads[task_id].cancel()
        del state.active_downloads[task_id]
    if task_id in state.tasks:
        del state.tasks[task_id]
        state.task_credentials.pop(task_id, None)
        return {"status": "deleted"}
    raise HTTPException(status_code=404, detail="Task not found")


@router.get("/tasks/stopped")
async def get_stopped_tasks():
    result = []
    for task_id, task in state.tasks.items():
        if task.get("status") == "stopped":
            result.append({"task_id": task_id, **sanitize_task(task)})
    return result
