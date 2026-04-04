"""Dailymotion / yt-dlp task type routing."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.routes.youtube import _detect_task_type
from app.state import tm


def test_detect_task_type_youtube() -> None:
    assert _detect_task_type("https://www.youtube.com/watch?v=abc123") == "youtube"
    assert _detect_task_type("https://youtu.be/abc123") == "youtube"
    assert _detect_task_type("https://www.youtube.com/shorts/abc123") == "youtube"


def test_detect_task_type_dailymotion() -> None:
    assert _detect_task_type("https://www.dailymotion.com/video/x8iq88f") == "yt-dlp"
    assert _detect_task_type("https://dai.ly/x8iq88f") == "yt-dlp"
    assert _detect_task_type("https://dailymotion.com/video/x123abc") == "yt-dlp"


def test_detect_task_type_other_uses_yt_dlp_bucket() -> None:
    assert _detect_task_type("https://vimeo.com/12345") == "yt-dlp"
    assert _detect_task_type("https://example.com/video.m3u8") == "yt-dlp"


def test_youtube_download_dailymotion_task_type(client) -> None:
    mock_info = {
        "title": "Test DM Video",
        "thumbnail": "https://example.com/thumb.jpg",
        "formats": [],
    }
    with (
        patch(
            "app.routes.youtube.youtube_service.fetch_youtube_json",
            new_callable=AsyncMock,
            return_value=mock_info,
        ),
        patch(
            "app.routes.youtube.youtube_service.run_youtube_download",
            new_callable=AsyncMock,
        ),
    ):
        res = client.post(
            "/youtube/download",
            json={
                "url": "https://www.dailymotion.com/video/x8iq88f",
                "quality": "1080",
            },
        )
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "queued"
    assert data["task_id"]

    task = tm.get(data["task_id"])
    assert task is not None
    assert task.type == "yt-dlp"


def test_youtube_download_youtube_task_type(client) -> None:
    mock_info = {
        "title": "Test YT Video",
        "thumbnail": "https://i.ytimg.com/vi/abc/default.jpg",
        "formats": [],
    }
    with (
        patch(
            "app.routes.youtube.youtube_service.fetch_youtube_json",
            new_callable=AsyncMock,
            return_value=mock_info,
        ),
        patch(
            "app.routes.youtube.youtube_service.run_youtube_download",
            new_callable=AsyncMock,
        ),
    ):
        res = client.post(
            "/youtube/download",
            json={
                "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                "quality": "1080",
            },
        )
    assert res.status_code == 200
    data = res.json()
    task = tm.get(data["task_id"])
    assert task is not None
    assert task.type == "youtube"
