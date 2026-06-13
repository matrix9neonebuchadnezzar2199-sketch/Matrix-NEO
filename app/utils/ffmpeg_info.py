"""FFmpeg binary metadata for health / diagnostics."""

from __future__ import annotations

import asyncio
import logging
import os
from functools import lru_cache

from app.utils.paths import FFMPEG

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def ffmpeg_version_line() -> str:
    """First line of ``ffmpeg -version`` (empty if binary missing or fails)."""
    if not FFMPEG or FFMPEG == "ffmpeg" or not os.path.isfile(FFMPEG):
        return ""
    try:
        import subprocess

        proc = subprocess.run(
            [FFMPEG, "-version"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
        line = (proc.stdout or proc.stderr or "").splitlines()
        return line[0].strip() if line else ""
    except Exception as e:
        logger.debug("ffmpeg -version: %s", e)
        return ""


async def ffmpeg_health_fields() -> dict[str, str]:
    """Async wrapper so /health does not block the event loop long."""
    line = await asyncio.to_thread(ffmpeg_version_line)
    path = FFMPEG if FFMPEG and os.path.isfile(FFMPEG) else ""
    return {
        "ffmpeg_path": path,
        "ffmpeg_version": line,
    }
