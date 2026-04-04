"""Stop / resume / bulk task controls."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException

from app import config as cfg
from app.models import TaskState, TaskStatus
from app.services import youtube_service
from app.services.download_service import run_download
from app.state import tm
from app.task_id import new_task_id
from app.utils.validation import validate_http_url

router = APIRouter(tags=["tasks"])
logger = logging.getLogger(__name__)


@router.post("/task/{task_id}/stop")
async def stop_task(task_id: str):
    task = tm.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if task_id in tm.active_downloads:
        dl_task = tm.active_downloads[task_id]
        dl_task.cancel()
        try:
            await dl_task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("stop_task await download")
    tm.active_downloads.pop(task_id, None)

    partial = cfg.OUTPUT_DIR / task.filename
    if partial.is_file():
        try:
            partial.unlink()
        except OSError as e:
            logger.debug("stop remove partial: %s", e)

    await tm.update(
        task_id,
        status=TaskStatus.STOPPED,
        message="Stopped",
        stopped_at=datetime.now().isoformat(),
    )

    logger.info("Task stopped: %s", task_id)
    return {"status": "stopped", "task_id": task_id, "can_resume": True}


@router.post("/task/{task_id}/resume")
async def resume_task(task_id: str):
    cur = tm.get(task_id)
    if not cur or cur.status != TaskStatus.STOPPED:
        raise HTTPException(status_code=404, detail="Stopped task not found")

    _, resolved_ips = validate_http_url(cur.url, block_private_ips=cfg.BLOCK_PRIVATE_IPS)
    cred = tm.task_credentials.get(task_id, {})
    ck, rk = cred.get("cookie"), cred.get("referer")
    new_id = new_task_id()

    if cur.type == "youtube":
        await tm.register(
            TaskState(
                task_id=new_id,
                status=TaskStatus.QUEUED,
                progress=0.0,
                filename=cur.filename,
                message="Resuming...",
                url=cur.url,
                type="youtube",
                quality=cur.quality,
                thumbnail_url=cur.thumbnail_url,
                format=cur.format,
                created_at=datetime.now().isoformat(),
            ),
        )
        t = asyncio.create_task(
            youtube_service.run_youtube_download(
                new_id,
                cur.url,
                cur.filename,
                cur.format or "mp4",
                cur.quality or "1080",
                cur.thumbnail_url,
            )
        )
        tm.active_downloads[new_id] = t
    else:
        await tm.register(
            TaskState(
                task_id=new_id,
                status=TaskStatus.QUEUED,
                progress=0.0,
                filename=cur.filename,
                message="Resuming...",
                url=cur.url,
                type=cur.type or "hls",
                quality=cur.quality,
                thumbnail_url=cur.thumbnail_url,
                created_at=datetime.now().isoformat(),
            ),
            credentials={"cookie": ck, "referer": rk},
        )
        t = asyncio.create_task(
            run_download(
                new_id,
                cur.url,
                cur.filename,
                cur.thumbnail_url,
                cur.quality,
                ck,
                rk,
                resolved_ips=resolved_ips,
            )
        )
        tm.active_downloads[new_id] = t

    await tm.remove(task_id)

    logger.info("Task resumed: %s -> %s", task_id, new_id)
    return {"status": "resumed", "old_task_id": task_id, "new_task_id": new_id}


@router.post("/tasks/stop-all")
async def stop_all_tasks():
    stopped_count = 0
    for tid in list(tm.active_downloads.keys()):
        try:
            await stop_task(tid)
            stopped_count += 1
        except Exception:
            logger.exception("stop-all: %s", tid)
    logger.info("stop-all: %s tasks", stopped_count)
    return {"status": "ok", "stopped_count": stopped_count}


@router.delete("/tasks/clear-stopped")
async def clear_stopped_tasks():
    cleared = [
        task_id
        for task_id, t in tm.tasks.items()
        if t.status in (TaskStatus.STOPPED, TaskStatus.ERROR, TaskStatus.COMPLETED)
    ]
    n = await tm.remove_many(cleared)
    logger.info("clear-stopped: %s tasks", n)
    return {"status": "ok", "cleared_count": n}
