"""HLS / progressive download endpoint."""

from __future__ import annotations

import logging

from fastapi import APIRouter

from app import config as cfg
from app.models import DownloadRequest
from app.services import download_service
from app.services.task_dispatch import queue_download_task
from app.utils.validation import validate_http_url

router = APIRouter(tags=["download"])
logger = logging.getLogger(__name__)


@router.post("/download")
async def download(request: DownloadRequest):
    validated_url, resolved_ips = validate_http_url(
        request.url, block_private_ips=cfg.BLOCK_PRIVATE_IPS
    )

    if request.thumbnail_url:
        logger.info("Thumbnail URL received for: %s...", (request.filename or "")[:50])
    else:
        logger.info("NO thumbnail URL for: %s...", (request.filename or "")[:50])

    async def _runner(task_id: str, filename: str) -> None:
        await download_service.run_download(
            task_id,
            validated_url,
            filename,
            request.thumbnail_url,
            request.quality,
            request.cookie,
            request.referer,
            resolved_ips=resolved_ips,
        )

    return await queue_download_task(
        url=request.url,
        requested_filename=request.filename,
        task_type="hls",
        quality=request.quality,
        thumbnail_url=request.thumbnail_url,
        credentials={"cookie": request.cookie, "referer": request.referer},
        runner=_runner,
    )
