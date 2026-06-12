"""Stop / resume / bulk task controls."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException

from app import config as cfg
from app.models import TaskState, TaskStatus
from app.services import youtube_service
from app.services.download_service import cleanup_hls_artifacts, run_download
from app.services.task_dispatch import download_in_flight_key
from app.state import STOPPABLE_STATUSES, tm
from app.utils.timeutil import utcnow_iso
from app.utils.validation import validate_http_url

router = APIRouter(tags=["tasks"])
logger = logging.getLogger(__name__)

_YTDLP_TYPES = frozenset({"youtube", "yt-dlp"})


async def _stop_task_impl(task_id: str) -> dict:
    task = tm.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if task.status not in STOPPABLE_STATUSES and task.status != TaskStatus.STOPPED:
        tm.unregister_active_download(task_id)
        return {
            "status": "already_finished",
            "task_id": task_id,
            "task_status": task.status.value,
            "can_resume": False,
        }

    if task_id in tm.active_downloads:
        dl_task = tm.active_downloads[task_id]
        dl_task.cancel()
        try:
            await dl_task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("stop_task await download")
    tm.unregister_active_download(task_id)

    if task.status in STOPPABLE_STATUSES:
        out = cfg.OUTPUT_DIR / task.filename
        part = Path(str(out) + ".part")
        if out.is_file():
            try:
                out.unlink()
            except OSError as e:
                logger.debug("stop remove output: %s", e)
        if part.is_file():
            logger.info("stop kept partial for resume: %s", part.name)
        cleanup_hls_artifacts(task_id)

        await tm.update(
            task_id,
            status=TaskStatus.STOPPED,
            message="Stopped",
            stopped_at=utcnow_iso(),
        )
        logger.info("Task stopped: %s", task_id)
        return {"status": "stopped", "task_id": task_id, "can_resume": True}

    return {"status": "stopped", "task_id": task_id, "can_resume": True}


@router.post("/task/{task_id}/stop")
async def stop_task(task_id: str):
    return await _stop_task_impl(task_id)


async def _start_resumed_runner(task_id: str, cur: TaskState) -> None:
    """Restart download for a stopped task, reusing the same task_id."""
    _, resolved_ips = validate_http_url(cur.url, block_private_ips=cfg.BLOCK_PRIVATE_IPS)
    cred = tm.task_credentials.get(task_id, {})
    ck, rk = cred.get("cookie"), cred.get("referer")
    in_flight_key = download_in_flight_key(cur.url, cur.quality)

    async def _run() -> None:
        try:
            if cur.type in _YTDLP_TYPES:
                await youtube_service.run_youtube_download(
                    task_id,
                    cur.url,
                    cur.filename,
                    cur.format or "mp4",
                    cur.quality or "1080",
                    cur.thumbnail_url,
                )
            else:
                await run_download(
                    task_id,
                    cur.url,
                    cur.filename,
                    cur.thumbnail_url,
                    cur.quality,
                    ck,
                    rk,
                    resolved_ips=resolved_ips,
                )
        finally:
            await tm.release_in_flight(in_flight_key, task_id)
            tm.unregister_active_download(task_id)

    tm.active_downloads[task_id] = asyncio.create_task(_run())


@router.post("/task/{task_id}/resume")
async def resume_task(task_id: str):
    cur = tm.get(task_id)
    if not cur or cur.status != TaskStatus.STOPPED:
        raise HTTPException(status_code=404, detail="Stopped task not found")

    if task_id in tm.active_downloads:
        raise HTTPException(status_code=409, detail="Task already running")

    in_flight_key = download_in_flight_key(cur.url, cur.quality)
    existing = await tm.find_in_flight_task(in_flight_key)
    if existing is not None and existing.task_id != task_id:
        raise HTTPException(
            status_code=409,
            detail=f"Another download in flight: {existing.task_id}",
        )

    cleanup_hls_artifacts(task_id)
    await tm.bind_in_flight(in_flight_key, task_id)
    await tm.update(
        task_id,
        status=TaskStatus.QUEUED,
        progress=0.0,
        message="Resuming...",
        stopped_at=None,
    )

    await _start_resumed_runner(task_id, cur)
    logger.info("Task resumed: %s", task_id)
    return {"status": "resumed", "task_id": task_id}


@router.post("/tasks/stop-all")
async def stop_all_tasks():
    tids = list(tm.active_downloads.keys())
    if not tids:
        return {"status": "ok", "stopped_count": 0}

    results = await asyncio.gather(
        *(_stop_task_impl(tid) for tid in tids),
        return_exceptions=True,
    )
    stopped_count = sum(
        1 for r in results if isinstance(r, dict) and r.get("status") == "stopped"
    )
    for r in results:
        if isinstance(r, Exception) and not isinstance(r, HTTPException):
            logger.exception("stop-all: %s", r)
    logger.info("stop-all: %s tasks", stopped_count)
    return {"status": "ok", "stopped_count": stopped_count}


@router.delete("/tasks/clear-stopped")
async def clear_stopped_tasks():
    cleared = [
        task_id
        for task_id, t in tm.tasks.items()
        if t.status == TaskStatus.STOPPED
    ]
    n = await tm.remove_many(cleared)
    logger.info("clear-stopped: %s tasks", n)
    return {"status": "ok", "cleared_count": n}


@router.delete("/tasks/clear-finished")
async def clear_finished_tasks():
    cleared = [
        task_id
        for task_id, t in tm.tasks.items()
        if t.status in (TaskStatus.COMPLETED, TaskStatus.ERROR)
    ]
    for tid in cleared:
        tm.unregister_active_download(tid)
    n = await tm.remove_many(cleared)
    logger.info("clear-finished: %s tasks", n)
    return {"status": "ok", "cleared_count": n}
