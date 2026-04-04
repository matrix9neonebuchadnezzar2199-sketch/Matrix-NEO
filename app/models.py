"""Pydantic request/response models."""

from __future__ import annotations

import enum
from typing import Any, Optional

from pydantic import BaseModel, Field

from app.utils.timeutil import utcnow_iso


class DownloadRequest(BaseModel):
    url: str
    filename: Optional[str] = None
    format_type: Optional[str] = "mp4"
    quality: Optional[str] = None
    thumbnail_url: Optional[str] = None
    cookie: Optional[str] = None
    referer: Optional[str] = None


class YouTubeRequest(BaseModel):
    url: str
    filename: Optional[str] = None
    format_type: Optional[str] = "mp4"
    quality: Optional[str] = "1080"
    thumbnail: Optional[bool] = True


class ProxyImageRequest(BaseModel):
    url: str
    cookie: Optional[str] = None
    referer: Optional[str] = None


class TaskStatus(str, enum.Enum):
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    MERGING = "merging"
    THUMBNAIL = "thumbnail"
    COMPLETED = "completed"
    STOPPED = "stopped"
    ERROR = "error"


class TaskState(BaseModel):
    task_id: str
    url: str
    filename: str
    status: TaskStatus = TaskStatus.QUEUED
    progress: float = 0.0
    message: str = "Queue..."
    type: str = "hls"  # "hls" | "youtube" | "yt-dlp" (yt-dlp-backed downloads)
    quality: Optional[str] = None
    format: Optional[str] = None  # YouTube: "mp4" | "mp3"
    thumbnail_url: Optional[str] = None
    file_size: Optional[int] = None
    created_at: str = Field(default_factory=utcnow_iso)
    completed_at: Optional[str] = None
    stopped_at: Optional[str] = None

    def to_api_dict(self) -> dict[str, Any]:
        """Secret-free dict for API responses (JSON-serializable).

        Includes keys whose value is null so clients (e.g. Chrome extension) always see
        the same shape as the pre-TaskState dict responses.
        """
        return self.model_dump(mode="json")
