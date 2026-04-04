"""Progressive download URL classification."""

from __future__ import annotations

from app.services.download_service import is_direct_progressive_http_url


def test_direct_progressive_file_extensions() -> None:
    assert is_direct_progressive_http_url("https://cdn.example/video.mp4") is True
    assert is_direct_progressive_http_url("https://cdn.example/p/x.m4v") is True
    assert is_direct_progressive_http_url("https://cdn.example/a.webm") is True


def test_direct_progressive_query_string() -> None:
    assert is_direct_progressive_http_url("https://x.com/get?id=1&file.mp4?") is True


def test_not_hls_or_other() -> None:
    assert is_direct_progressive_http_url("https://x/stream.m3u8") is False
    assert is_direct_progressive_http_url("ftp://x/a.mp4") is False
