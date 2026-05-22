"""Normalize media URLs for deduplication keys."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse, urlunparse

_HLS_QUALITY_SUFFIX = re.compile(r"/(1080p|720p|480p|360p|240p)/video\.m3u8.*$", re.I)
_HLS_PLAYLIST_SUFFIX = re.compile(r"/(playlist|master|index)\.m3u8.*$", re.I)


def normalize_download_url(url: str) -> str:
    """Stable key for in-flight dedup (strip fragment, normalize HLS variant paths)."""
    u = (url or "").strip()
    if not u:
        return ""
    try:
        p = urlparse(u)
        path = _HLS_QUALITY_SUFFIX.sub("", p.path or "")
        path = _HLS_PLAYLIST_SUFFIX.sub("", path)
        query = ""
        if p.hostname and "youtube.com" in p.hostname and p.path == "/watch":
            vid = (parse_qs(p.query).get("v") or [None])[0]
            if vid:
                query = f"v={vid}"
        netloc = (p.netloc or "").lower()
        return urlunparse((p.scheme.lower(), netloc, path.rstrip("/"), "", query, ""))
    except Exception:
        return u.split("#")[0].rstrip("/")
