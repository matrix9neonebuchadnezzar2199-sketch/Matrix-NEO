"""YouTube / yt-dlp quality string sanitization."""

from app.services.youtube_service import _sanitize_yt_quality


def test_sanitize_mp4_numeric_clamped() -> None:
    assert _sanitize_yt_quality("1080", "mp4") == "1080"
    assert _sanitize_yt_quality("99999", "mp4") == "4320"
    assert _sanitize_yt_quality("144", "mp4") == "144"
    assert _sanitize_yt_quality("bad", "mp4") == "1080"


def test_sanitize_mp3_abr_clamped() -> None:
    assert _sanitize_yt_quality("192", "mp3") == "192"
    assert _sanitize_yt_quality("9999", "mp3") == "320"
    assert _sanitize_yt_quality("10", "mp3") == "64"
    assert _sanitize_yt_quality("oops", "mp3") == "192"
