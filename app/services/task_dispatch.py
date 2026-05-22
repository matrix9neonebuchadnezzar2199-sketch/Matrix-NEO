"""Shared helpers to queue download tasks with deduplication."""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, Optional

from app.models import TaskState, TaskStatus
from app.state import tm
from app.task_id import new_task_id
from app.utils.filename_allocate import unique_output_filename
from app.utils.timeutil import utcnow_iso
from app.utils.url_normalize import normalize_download_url

logger = logging.getLogger(__name__)

_ACTIVE_STATUSES = frozenset(
    {
        TaskStatus.QUEUED,
        TaskStatus.DOWNLOADING,
        TaskStatus.MERGING,
        TaskStatus.THUMBNAIL,
    }
)


def download_in_flight_key(url: str, quality: Optional[str] = None) -> str:
    q = (quality or "").strip()
    return f"{normalize_download_url(url)}|{q}"


async def find_active_task_for_url(url: str, quality: Optional[str] = None) -> Optional[TaskState]:
    """Return an existing in-flight task for the same normalized URL (+ quality)."""
    key = download_in_flight_key(url, quality)
    return await tm.find_in_flight_task(key)


async def queue_download_task(
    *,
    url: str,
    requested_filename: Optional[str],
    task_type: str,
    quality: Optional[str] = None,
    thumbnail_url: Optional[str] = None,
    format_type: Optional[str] = None,
    credentials: Optional[dict[str, str | None]] = None,
    runner: Callable[[str, str], Awaitable[None]],
    ext: str = ".mp4",
) -> dict:
    """
    Register a task, bind in-flight key, and start asyncio runner.
    Returns API payload; sets deduplicated=True when reusing an active task.
    """
    existing = await find_active_task_for_url(url, quality)
    if existing is not None:
        logger.info("Dedup download: %s -> %s", url[:60], existing.task_id)
        return {
            "task_id": existing.task_id,
            "status": existing.status.value,
            "filename": existing.filename,
            "deduplicated": True,
        }

    task_id = new_task_id()
    filename = unique_output_filename(requested_filename, task_id, ext=ext)
    in_flight_key = download_in_flight_key(url, quality)

    await tm.register(
        TaskState(
            task_id=task_id,
            url=url,
            filename=filename,
            thumbnail_url=thumbnail_url,
            quality=quality,
            type=task_type,
            format=format_type,
            status=TaskStatus.QUEUED,
            progress=0.0,
            message="Queue...",
            created_at=utcnow_iso(),
        ),
        credentials=credentials,
    )
    await tm.bind_in_flight(in_flight_key, task_id)

    async def _run() -> None:
        try:
            await runner(task_id, filename)
        finally:
            await tm.release_in_flight(in_flight_key, task_id)

    task = asyncio.create_task(_run())
    tm.active_downloads[task_id] = task

    return {
        "task_id": task_id,
        "status": "queued",
        "filename": filename,
        "deduplicated": False,
    }
