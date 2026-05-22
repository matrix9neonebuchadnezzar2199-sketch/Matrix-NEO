"""URL normalization for in-flight dedup."""

from app.utils.url_normalize import normalize_download_url


def test_strips_fragment() -> None:
    assert normalize_download_url("https://x.com/a.m3u8#frag") == normalize_download_url(
        "https://x.com/a.m3u8"
    )


def test_hls_quality_path_collapses() -> None:
    a = normalize_download_url("https://cdn.example/stream/1080p/video.m3u8?x=1")
    b = normalize_download_url("https://cdn.example/stream/720p/video.m3u8")
    assert a == b


def test_youtube_watch_v_param() -> None:
    u = normalize_download_url("https://www.youtube.com/watch?v=abc123&t=5")
    assert "v=abc123" in u
