"""Stop / resume / bulk task controls."""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime

from fastapi import APIRouter, HTTPException

from app import state
from app.services import youtube_service
from app.services.download_service import run_download
from app.task_id import new_task_id

router = APIRouter(tags=["tasks"])
logger = logging.getLogger(__name__)


@router.post("/task/{task_id}/stop")
async def stop_task(task_id: str):
    if task_id not in state.tasks:
        raise HTTPException(status_code=404, detail="Task not found")

    task = state.tasks[task_id]

    if task_id in state.active_downloads:
        dl_task = state.active_downloads[task_id]
        dl_task.cancel()
        try:
            await dl_task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("stop_task await download")
    state.active_downloads.pop(task_id, None)

    if task.get("output_path") and isinstance(task["output_path"], str) and os.path.exists(
        task["output_path"]
    ):
        try:
            os.remove(task["output_path"])
        except OSError as e:
            logger.debug("stop remove partial: %s", e)

    state.tasks[task_id]["status"] = "stopped"
    state.tasks[task_id]["message"] = "停止しました"
    state.tasks[task_id]["stopped_at"] = datetime.now().isoformat()

    logger.info("Task stopped: %s", task_id)
    return {"status": "stopped", "task_id": task_id, "can_resume": True}


@router.post("/task/{task_id}/resume")
async def resume_task(task_id: str):
    if task_id not in state.tasks or state.tasks[task_id].get("status") != "stopped":
        raise HTTPException(status_code=404, detail="Stopped task not found")

    info = state.tasks[task_id]
    new_id = new_task_id()

    if info.get("type") == "youtube":
        state.tasks[new_id] = {
            "task_id": new_id,
            "status": "queued",
            "progress": 0,
            "filename": info["filename"],
            "message": "再開中...",
            "url": info["url"],
            "type": "youtube",
            "quality": info.get("quality"),
            "thumbnail_url": info.get("thumbnail_url"),
            "format": info.get("format"),
            "created_at": datetime.now().isoformat(),
        }
        t = asyncio.create_task(
            youtube_service.run_youtube_download(
                new_id,
                info["url"],
                info["filename"],
                info.get("format", "mp4"),
                info.get("quality") or "1080",
                info.get("thumbnail_url"),
            )
        )
        state.active_downloads[new_id] = t
    else:
        state.tasks[new_id] = {
            "task_id": new_id,
            "status": "queued",
            "progress": 0,
            "filename": info["filename"],
            "message": "再開中...",
            "url": info["url"],
            "type": info.get("type", "hls"),
            "thumbnail_url": info.get("thumbnail_url"),
            "cookie": info.get("cookie"),
            "referer": info.get("referer"),
            "created_at": datetime.now().isoformat(),
        }
        t = asyncio.create_task(
            run_download(
                new_id,
                info["url"],
                info["filename"],
                info.get("thumbnail_url"),
                info.get("quality"),
                info.get("cookie"),
                info.get("referer"),
            )
        )
        state.active_downloads[new_id] = t

    state.tasks.pop(task_id, None)

    logger.info("Task resumed: %s -> %s", task_id, new_id)
    return {"status": "resumed", "old_task_id": task_id, "new_task_id": new_id}


@router.post("/tasks/stop-all")
async def stop_all_tasks():
    stopped_count = 0
    for tid in list(state.active_downloads.keys()):
        try:
            await stop_task(tid)
            stopped_count += 1
        except Exception:
            logger.exception("stop-all: %s", tid)
    logger.info("stop-all: %s tasks", stopped_count)
    return {"status": "ok", "stopped_count": stopped_count}


@router.delete("/tasks/clear-stopped")
async def clear_stopped_tasks():
    cleared = []
    for task_id in list(state.tasks.keys()):
        if state.tasks[task_id].get("status") in ("stopped", "error", "completed"):
            cleared.append(task_id)
            del state.tasks[task_id]
    logger.info("clear-stopped: %s tasks", len(cleared))
    return {"status": "ok", "cleared_count": len(cleared)}
