"""HLS / progressive download endpoint."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from fastapi import APIRouter

from app import config as cfg
from app.models import DownloadRequest, TaskState, TaskStatus
from app.services import download_service
from app.state import tm
from app.task_id import new_task_id
from app.utils.filename import sanitize_filename_for_windows
from app.utils.validation import validate_http_url

router = APIRouter(tags=["download"])
logger = logging.getLogger(__name__)


@router.post("/download")
async def download(request: DownloadRequest):
    validated_url, resolved_ips = validate_http_url(
        request.url, block_private_ips=cfg.BLOCK_PRIVATE_IPS
    )

    task_id = new_task_id()
    filename = request.filename or f"video_{task_id}"
    filename = sanitize_filename_for_windows(filename)

    if request.thumbnail_url:
        logger.info("Thumbnail URL received for: %s...", filename[:50])
    else:
        logger.info("NO thumbnail URL for: %s...", filename[:50])

    await tm.register(
        TaskState(
            task_id=task_id,
            url=request.url,
            filename=filename,
            thumbnail_url=request.thumbnail_url,
            quality=request.quality,
            type="hls",
            status=TaskStatus.QUEUED,
            progress=0.0,
            message="Queue...",
            created_at=datetime.now().isoformat(),
        ),
        credentials={"cookie": request.cookie, "referer": request.referer},
    )

    task = asyncio.create_task(
        download_service.run_download(
            task_id,
            validated_url,
            filename,
            request.thumbnail_url,
            request.quality,
            request.cookie,
            request.referer,
            resolved_ips=resolved_ips,
        )
    )
    tm.active_downloads[task_id] = task

    return {"task_id": task_id, "status": "queued", "filename": filename}
