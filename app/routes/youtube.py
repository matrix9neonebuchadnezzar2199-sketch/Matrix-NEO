"""yt-dlp info and download (YouTube, Dailymotion, etc.)."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime

from fastapi import APIRouter, HTTPException

from app import config as cfg
from app.models import TaskState, TaskStatus, YouTubeRequest
from app.services import youtube_service
from app.state import tm
from app.task_id import new_task_id
from app.utils.validation import validate_http_url

router = APIRouter(tags=["youtube"])
logger = logging.getLogger(__name__)

_RE_YOUTUBE = re.compile(
    r"youtube\.com/watch\?|youtu\.be/|youtube\.com/shorts/",
)


def _detect_task_type(url: str) -> str:
    """Return 'youtube' for YouTube URLs, 'yt-dlp' for all other /youtube/download targets."""
    if _RE_YOUTUBE.search(url):
        return "youtube"
    return "yt-dlp"


@router.get("/youtube/info")
async def youtube_info(url: str):
    _url, _ = validate_http_url(url, block_private_ips=cfg.BLOCK_PRIVATE_IPS)
    try:
        info = await youtube_service.fetch_youtube_json(url)
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
            "is_youtube": _RE_YOUTUBE.search(url) is not None,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("yt-dlp info: %s", e)
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/youtube/download")
async def youtube_download(request: YouTubeRequest):
    _url, _ = validate_http_url(request.url, block_private_ips=cfg.BLOCK_PRIVATE_IPS)
    task_id = new_task_id()
    task_type = _detect_task_type(request.url)

    try:
        info = await youtube_service.fetch_youtube_json(request.url)
        title = info.get("title", "video")
        thumbnail_url = info.get("thumbnail", "")
    except HTTPException:
        raise
    except Exception:
        title = "video"
        thumbnail_url = ""

    filename = request.filename or title
    filename = re.sub(r'[<>:"/\\|?*]', "_", filename)[:80]
    if request.format_type == "mp3":
        filename += ".mp3"
    else:
        filename += ".mp4"

    ft = request.format_type or "mp4"
    await tm.register(
        TaskState(
            task_id=task_id,
            url=request.url,
            filename=filename,
            status=TaskStatus.QUEUED,
            progress=0.0,
            message="Queue...",
            type=task_type,
            format=ft,
            quality=request.quality,
            thumbnail_url=thumbnail_url if request.thumbnail else None,
            created_at=datetime.now().isoformat(),
        ),
    )

    task = asyncio.create_task(
        youtube_service.run_youtube_download(
            task_id,
            request.url,
            filename,
            ft,
            request.quality or "1080",
            thumbnail_url if request.thumbnail else None,
        )
    )
    tm.active_downloads[task_id] = task

    logger.info("yt-dlp queued [%s]: %s (%s, %s)", task_type, filename, ft, request.quality)

    return {
        "task_id": task_id,
        "status": "queued",
        "filename": filename,
        "format": ft,
    }
