"""YouTube info and download."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime

from fastapi import APIRouter, HTTPException

from app import config as cfg
from app import state
from app.models import YouTubeRequest
from app.services import youtube_service
from app.task_id import new_task_id
from app.utils.paths import YTDLP
from app.utils.validation import validate_http_url

router = APIRouter(tags=["youtube"])
logger = logging.getLogger(__name__)


@router.get("/youtube/info")
async def youtube_info(url: str):
    validate_http_url(url, block_private_ips=cfg.BLOCK_PRIVATE_IPS)
    try:
        cmd = [YTDLP, "--dump-json", "--no-playlist", "--", url]
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            logger.error("YT info: %s", stderr.decode("utf-8", errors="ignore")[:500])
            raise HTTPException(status_code=400, detail="Failed to get video info")

        info = json.loads(stdout.decode("utf-8"))
        video_qualities = set()
        audio_qualities = set()
        for fmt in info.get("formats", []):
            if fmt.get("vcodec") != "none" and fmt.get("height"):
                video_qualities.add(fmt["height"])
            if fmt.get("acodec") != "none" and fmt.get("abr"):
                audio_qualities.add(int(fmt["abr"]))

        return {
            "title": info.get("title", "Unknown"),
            "duration": info.get("duration", 0),
            "thumbnail": info.get("thumbnail", ""),
            "channel": info.get("channel", ""),
            "video_qualities": sorted(video_qualities, reverse=True),
            "audio_qualities": sorted(audio_qualities, reverse=True),
            "is_youtube": True,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("youtube info: %s", e)
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/youtube/download")
async def youtube_download(request: YouTubeRequest):
    validate_http_url(request.url, block_private_ips=cfg.BLOCK_PRIVATE_IPS)
    task_id = new_task_id()

    try:
        cmd = [YTDLP, "--dump-json", "--no-playlist", "--", request.url]
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await process.communicate()
        info = json.loads(stdout.decode("utf-8"))
        title = info.get("title", "video")
        thumbnail_url = info.get("thumbnail", "")
    except Exception:
        title = "video"
        thumbnail_url = ""

    filename = request.filename or title
    filename = re.sub(r'[<>:"/\\|?*]', "_", filename)[:80]
    if request.format_type == "mp3":
        filename += ".mp3"
    else:
        filename += ".mp4"

    state.tasks[task_id] = {
        "task_id": task_id,
        "url": request.url,
        "filename": filename,
        "status": "queued",
        "progress": 0,
        "message": "Queue...",
        "type": "youtube",
        "format": request.format_type,
        "quality": request.quality,
        "thumbnail_url": thumbnail_url if request.thumbnail else None,
        "created_at": datetime.now().isoformat(),
    }

    task = asyncio.create_task(
        youtube_service.run_youtube_download(
            task_id,
            request.url,
            filename,
            request.format_type,
            request.quality or "1080",
            thumbnail_url if request.thumbnail else None,
        )
    )
    state.active_downloads[task_id] = task

    logger.info("YT queued: %s (%s, %s)", filename, request.format_type, request.quality)

    return {
        "task_id": task_id,
        "status": "queued",
        "filename": filename,
        "format": request.format_type,
    }
