"""YouTube download via yt-dlp."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime
from typing import Optional

from fastapi import HTTPException

from app import config as cfg
from app import state
from app.services.download_service import terminate_child_process
from app.utils.paths import YTDLP

logger = logging.getLogger(__name__)

_YT_JSON_CACHE: dict[str, tuple[dict, float]] = {}
_YT_JSON_CACHE_MAX = 100
_YT_META_TTL = 300.0


async def fetch_youtube_json(url: str) -> dict:
    """Single --dump-json fetch with TTL cache (shared by /youtube/info and /youtube/download)."""
    now = time.time()
    if url in _YT_JSON_CACHE:
        data, ts = _YT_JSON_CACHE[url]
        if now - ts < _YT_META_TTL:
            return data
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
    _YT_JSON_CACHE[url] = (data, now)
    if len(_YT_JSON_CACHE) > _YT_JSON_CACHE_MAX:
        oldest_key = min(_YT_JSON_CACHE, key=lambda k: _YT_JSON_CACHE[k][1])
        del _YT_JSON_CACHE[oldest_key]
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
    async with state.semaphore:
        process: Optional[asyncio.subprocess.Process] = None
        try:
            state.tasks[task_id]["status"] = "downloading"
            state.tasks[task_id]["message"] = "Starting YouTube download..."

            output_path = os.path.join(cfg.OUTPUT_DIR, filename)

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

            # yt-dlp はプログレス表示に \r（行上書き）を使うことがあるため、\n だけの split では
            # 進捗が取れない。bytearray で \r / \n の両方をデリミタにし、O(n²) の文字列連結を避ける。
            def _apply_yt_line(raw: str) -> None:
                text = raw.strip()
                if not text:
                    return
                m = _RE_YT_PROGRESS.search(text)
                if m:
                    progress = float(m.group(1))
                    state.tasks[task_id]["progress"] = min(progress * 0.9, 90)
                sm = _RE_YT_SPEED.search(text)
                zm = _RE_YT_SIZE.search(text)
                if m:
                    msg = f"{state.tasks[task_id]['progress']:.0f}%"
                    if zm:
                        msg += f" of {zm.group(1)}"
                    if sm:
                        msg += f" ({sm.group(1)})"
                    state.tasks[task_id]["message"] = msg
                if "Merging" in text or "muxing" in text.lower():
                    state.tasks[task_id]["progress"] = 90
                    state.tasks[task_id]["message"] = "Merging..."
                if "[ExtractAudio]" in text:
                    state.tasks[task_id]["progress"] = 85
                    state.tasks[task_id]["message"] = "Extracting audio..."

            buf = bytearray()
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
                    _apply_yt_line(line.decode("utf-8", errors="ignore"))
            if buf:
                _apply_yt_line(buf.decode("utf-8", errors="ignore"))

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
                state.tasks[task_id]["file_size"] = file_size
                state.tasks[task_id]["status"] = "completed"
                state.tasks[task_id]["progress"] = 100
                state.tasks[task_id]["message"] = f"Done! {size_mb:.1f}MB ({speed_mbps:.1f}MB/s)"
                state.tasks[task_id]["completed_at"] = datetime.now().isoformat()
                logger.info("YT completed: %s (%.1fMB in %.1fs)", filename, size_mb, download_time)
            else:
                state.tasks[task_id]["status"] = "error"
                state.tasks[task_id]["message"] = "Download failed"
                logger.error("YT failed: %s", filename)

        except asyncio.CancelledError:
            if process is not None:
                await terminate_child_process(process)
            state.tasks[task_id]["status"] = "error"
            state.tasks[task_id]["message"] = "Cancelled"
            raise
        except Exception as e:
            state.tasks[task_id]["status"] = "error"
            state.tasks[task_id]["message"] = str(e)[:50]
            logger.exception("YT error: %s", e)
        finally:
            state.active_downloads.pop(task_id, None)
