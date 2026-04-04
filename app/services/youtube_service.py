"""YouTube download via yt-dlp."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from collections import OrderedDict
from datetime import datetime
from typing import Optional

from fastapi import HTTPException

from app import config as cfg
from app.constants import (
    YT_JSON_CACHE_MAX,
    YT_META_TTL_SEC,
    YT_PROGRESS_CAP,
    YT_PROGRESS_EXTRACT_AUDIO,
    YT_PROGRESS_MERGE,
    YT_PROGRESS_MULT,
)
from app.models import TaskStatus
from app.services.download_service import terminate_child_process
from app.state import tm
from app.utils.paths import YTDLP

logger = logging.getLogger(__name__)

_RE_DIGITS_ONLY = re.compile(r"^\d+$")


def _sanitize_yt_quality(quality: str | None, format_type: str) -> str:
    """Clamp height (mp4) or audio bitrate (mp3) to safe numeric strings for yt-dlp -f."""
    default = "192" if format_type == "mp3" else "1080"
    s = (quality or default).strip()
    if not _RE_DIGITS_ONLY.fullmatch(s):
        return default
    n = int(s)
    if format_type == "mp3":
        return str(max(64, min(320, n)))
    return str(max(144, min(4320, n)))


class _LRUCache:
    def __init__(self, maxsize: int, ttl: float):
        self._data: OrderedDict[str, tuple[dict, float]] = OrderedDict()
        self._maxsize = maxsize
        self._ttl = ttl

    def get(self, key: str) -> dict | None:
        if key not in self._data:
            return None
        value, ts = self._data[key]
        if time.time() - ts > self._ttl:
            del self._data[key]
            return None
        self._data.move_to_end(key)
        return value

    def put(self, key: str, value: dict) -> None:
        self._data[key] = (value, time.time())
        self._data.move_to_end(key)
        if len(self._data) > self._maxsize:
            self._data.popitem(last=False)


_yt_cache = _LRUCache(maxsize=YT_JSON_CACHE_MAX, ttl=YT_META_TTL_SEC)


async def fetch_youtube_json(url: str) -> dict:
    """Single --dump-json fetch with TTL cache (shared by /youtube/info and /youtube/download)."""
    cached = _yt_cache.get(url)
    if cached is not None:
        return cached
    cmd = [YTDLP, "--dump-json", "--no-playlist", "--", url]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60.0)
    except asyncio.TimeoutError:
        await terminate_child_process(proc)
        raise HTTPException(status_code=408, detail="yt-dlp timed out") from None
    if proc.returncode != 0:
        logger.error("YT dump-json: %s", (stderr or b"").decode("utf-8", errors="ignore")[:500])
        raise HTTPException(status_code=400, detail="Failed to get video info")
    data = json.loads(stdout.decode("utf-8"))
    _yt_cache.put(url, data)
    return data


_RE_YT_PROGRESS = re.compile(r"\[download\]\s+(\d+\.?\d*)%")
_RE_YT_SPEED = re.compile(r"at\s+(\d+\.?\d*\s*[KMG]?i?B/s)")
_RE_YT_SIZE = re.compile(r"of\s+~?(\d+\.?\d*\s*[KMG]?i?B)")


async def run_youtube_download(
    task_id: str,
    url: str,
    filename: str,
    format_type: str,
    quality: str,
    thumbnail_url: Optional[str] = None,
) -> None:
    _ = thumbnail_url
    quality = _sanitize_yt_quality(quality, format_type)
    async with tm.semaphore:
        process: Optional[asyncio.subprocess.Process] = None
        try:
            await tm.update(
                task_id,
                status=TaskStatus.DOWNLOADING,
                message="Starting YouTube download...",
            )

            output_path = str(cfg.OUTPUT_DIR / filename)

            if format_type == "mp3":
                format_spec = f"bestaudio[abr<={quality}]/bestaudio/best"
                cmd = [
                    YTDLP,
                    "-f",
                    format_spec,
                    "-x",
                    "--audio-format",
                    "mp3",
                    "--audio-quality",
                    "0",
                    "--embed-thumbnail",
                    "--add-metadata",
                    "-o",
                    output_path.replace(".mp3", ".%(ext)s"),
                    "--no-playlist",
                    "--progress",
                    "--",
                    url,
                ]
            else:
                format_spec = f"bestvideo[height<={quality}]+bestaudio/best[height<={quality}]/best"
                cmd = [
                    YTDLP,
                    "-f",
                    format_spec,
                    "--merge-output-format",
                    "mp4",
                    "--embed-thumbnail",
                    "--add-metadata",
                    "-o",
                    output_path,
                    "--no-playlist",
                    "--progress",
                    "--",
                    url,
                ]

            logger.info("YT starting: %s (%s, %s)", filename, format_type, quality)
            start_time = datetime.now()

            process = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
            )

            async def _apply_yt_line(raw: str) -> None:
                text = raw.strip()
                if not text:
                    return
                m = _RE_YT_PROGRESS.search(text)
                if m:
                    progress = float(m.group(1))
                    await tm.update(
                        task_id,
                        progress=min(progress * YT_PROGRESS_MULT, YT_PROGRESS_CAP),
                    )
                sm = _RE_YT_SPEED.search(text)
                zm = _RE_YT_SIZE.search(text)
                if m:
                    t = tm.get(task_id)
                    pr = float(t.progress) if t else 0.0
                    msg = f"{pr:.0f}%"
                    if zm:
                        msg += f" of {zm.group(1)}"
                    if sm:
                        msg += f" ({sm.group(1)})"
                    await tm.update(task_id, message=msg)
                if "Merging" in text or "muxing" in text.lower():
                    await tm.update(task_id, progress=float(YT_PROGRESS_MERGE), message="Merging...")
                if "[ExtractAudio]" in text:
                    await tm.update(
                        task_id,
                        progress=float(YT_PROGRESS_EXTRACT_AUDIO),
                        message="Extracting audio...",
                    )

            buf = bytearray()
            assert process.stdout is not None
            while True:
                chunk = await process.stdout.read(4096)
                if not chunk:
                    break
                buf.extend(chunk)
                while True:
                    i_n = buf.find(b"\n")
                    i_r = buf.find(b"\r")
                    if i_n == -1 and i_r == -1:
                        break
                    if i_r != -1 and (i_n == -1 or i_r < i_n):
                        line = bytes(buf[:i_r])
                        del buf[: i_r + 1]
                        if buf and buf[0] == 0x0A:
                            del buf[:1]
                    else:
                        line = bytes(buf[:i_n])
                        del buf[: i_n + 1]
                    await _apply_yt_line(line.decode("utf-8", errors="ignore"))
            if buf:
                await _apply_yt_line(buf.decode("utf-8", errors="ignore"))

            await process.wait()
            download_time = (datetime.now() - start_time).total_seconds()

            actual_output = output_path
            if not os.path.exists(output_path):
                base = os.path.splitext(output_path)[0]
                for ext in [".mp4", ".mp3", ".webm", ".mkv", ".m4a"]:
                    if os.path.exists(base + ext):
                        actual_output = base + ext
                        break

            if os.path.exists(actual_output):
                file_size = os.path.getsize(actual_output)
                size_mb = file_size / (1024 * 1024)
                speed_mbps = size_mb / download_time if download_time > 0 else 0
                await tm.update(
                    task_id,
                    file_size=file_size,
                    status=TaskStatus.COMPLETED,
                    progress=100.0,
                    message=f"Done! {size_mb:.1f}MB ({speed_mbps:.1f}MB/s)",
                    completed_at=datetime.now().isoformat(),
                )
                logger.info("YT completed: %s (%.1fMB in %.1fs)", filename, size_mb, download_time)
            else:
                await tm.update(task_id, status=TaskStatus.ERROR, message="Download failed")
                logger.error("YT failed: %s", filename)

        except asyncio.CancelledError:
            if process is not None:
                await terminate_child_process(process)
            await tm.update(task_id, status=TaskStatus.ERROR, message="Cancelled")
            raise
        except Exception as e:
            await tm.update(task_id, status=TaskStatus.ERROR, message=str(e)[:50])
            logger.exception("YT error: %s", e)
        finally:
            tm.active_downloads.pop(task_id, None)
