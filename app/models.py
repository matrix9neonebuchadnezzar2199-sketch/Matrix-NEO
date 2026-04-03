"""Pydantic request/response models."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


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
