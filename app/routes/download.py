"""HLS / progressive download endpoint."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from fastapi import APIRouter

from app import config as cfg
from app import state
from app.models import DownloadRequest
from app.services import download_service
from app.task_id import new_task_id
from app.utils.validation import validate_http_url

router = APIRouter(tags=["download"])
logger = logging.getLogger(__name__)


@router.post("/download")
async def download(request: DownloadRequest):
    validate_http_url(request.url, block_private_ips=cfg.BLOCK_PRIVATE_IPS)

    task_id = new_task_id()
    filename = request.filename or f"video_{task_id}.mp4"
    if not filename.endswith(".mp4"):
        filename += ".mp4"
    from app.utils.filename import sanitize_filename_for_windows

    filename = sanitize_filename_for_windows(filename)

    if request.thumbnail_url:
        logger.info("Thumbnail URL received for: %s...", filename[:50])
    else:
        logger.info("NO thumbnail URL for: %s...", filename[:50])

    state.tasks[task_id] = {
        "task_id": task_id,
        "url": request.url,
        "filename": filename,
        "thumbnail_url": request.thumbnail_url,
        "quality": request.quality,
        "type": "hls",
        "cookie": request.cookie,
        "referer": request.referer,
        "status": "queued",
        "progress": 0,
        "message": "Queue...",
        "created_at": datetime.now().isoformat(),
    }

    task = asyncio.create_task(
        download_service.run_download(
            task_id,
            request.url,
            filename,
            request.thumbnail_url,
            request.quality,
            request.cookie,
            request.referer,
        )
    )
    state.active_downloads[task_id] = task

    return {"task_id": task_id, "status": "queued", "filename": filename}
