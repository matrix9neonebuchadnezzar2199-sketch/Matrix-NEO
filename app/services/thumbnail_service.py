"""Thumbnail download, normalization, embed, and background worker."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

import httpx

from app import config as cfg
from app.constants import PROGRESS_THUMB_DL, PROGRESS_THUMB_EMBED
from app.models import TaskStatus
from app.services import http_client
from app.state import tm
from app.utils.file_ops import replace_or_move_overwrite
from app.utils.filename import is_ascii_basename
from app.utils.process import stderr_tail, subprocess_exit_code
from app.utils.paths import FFMPEG

logger = logging.getLogger(__name__)


async def fetch_thumbnail_http_bytes(
    url: str,
    cookie: Optional[str] = None,
    referer: Optional[str] = None,
) -> tuple[Optional[bytes], Optional[str]]:
    client = http_client.get_client()
    headers: Dict[str, str] = {
        "User-Agent": cfg.DEFAULT_UA,
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
    }
    if referer and referer.strip():
        headers["Referer"] = referer.strip()
    if cookie and cookie.strip():
        headers["Cookie"] = cookie.strip()
    try:
        r = await client.get(url, headers=headers, timeout=httpx.Timeout(45.0))
        if r.status_code == 200:
            ct = r.headers.get("Content-Type", "image/jpeg")
            if ";" in ct:
                ct = ct.split(";")[0].strip()
            return r.content, ct
        logger.warning("Thumbnail HTTP status=%s url=%s...", r.status_code, url[:80])
    except Exception as e:
        logger.exception("Thumbnail download error: %s", e)
    return None, None


def _thumbnail_bytes_look_like_jpeg_or_png(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            head = f.read(16)
    except OSError:
        return False
    if len(head) >= 3 and head[:3] == b"\xff\xd8\xff":
        return True
    if len(head) >= 8 and head[:8] == b"\x89PNG\r\n\x1a\n":
        return True
    return False


async def normalize_thumbnail_to_jpeg_for_embed(src_path: str) -> bool:
    if _thumbnail_bytes_look_like_jpeg_or_png(src_path):
        return True
    tmp = src_path + ".__neo.jpg"
    cmd = [
        FFMPEG,
        "-y",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-i",
        src_path,
        "-frames:v",
        "1",
        "-q:v",
        "2",
        tmp,
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, err = await proc.communicate()
        if proc.returncode == 0 and os.path.exists(tmp) and os.path.getsize(tmp) > 64:
            replace_or_move_overwrite(tmp, src_path)
            return True
        if err:
            logger.warning("normalize to JPEG: %s", stderr_tail(err, 400))
    except Exception as e:
        logger.exception("normalize to JPEG: %s", e)
    if os.path.exists(tmp):
        try:
            os.remove(tmp)
        except OSError:
            pass
    return False


async def download_thumbnail(
    url: str,
    output_path: str,
    cookie: Optional[str] = None,
    referer: Optional[str] = None,
) -> bool:
    content, _ = await fetch_thumbnail_http_bytes(url, cookie, referer)
    if not content:
        return False
    try:
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "wb") as f:
            f.write(content)
        return True
    except Exception as e:
        logger.exception("thumbnail save: %s", e)
    return False


async def _ffmpeg_embed(
    video_path: Path, thumb_path: Path, temp_output: Optional[Path]
) -> bool:
    out_path = temp_output if temp_output is not None else Path(str(video_path) + ".thumb.mp4")
    out_str = str(out_path)
    vp = str(video_path)
    tp = str(thumb_path)
    try:
        cmd = [
            FFMPEG,
            "-y",
            "-i",
            vp,
            "-i",
            tp,
            "-map",
            "0:v:0",
            "-map",
            "0:a:0",
            "-map",
            "1:0",
            "-c:v:0",
            "copy",
            "-c:a",
            "copy",
            "-c:v:1",
            "mjpeg",
            "-disposition:v:0",
            "default",
            "-disposition:v:1",
            "attached_pic",
            "-movflags",
            "+faststart",
            out_str,
        ]
        process = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        out, err = await process.communicate()
        if process.returncode == 0 and os.path.exists(out_str):
            replace_or_move_overwrite(out_str, vp)
            return True
        if err or out:
            rc = subprocess_exit_code(process.returncode)
            logger.warning("ffmpeg embed exit=%s %s", rc, stderr_tail(err or out))
        if os.path.exists(out_str):
            try:
                os.remove(out_str)
            except OSError:
                pass
    except Exception as e:
        logger.exception("ffmpeg embed: %s", e)
    return False


def _build_strategies(
    video_path: Path, task_id: str
) -> list[tuple[str, Path, Optional[Path]]]:
    temp_out: Optional[Path] = cfg.TEMP_DIR / f"{task_id}_thumb_out.mp4"
    if sys.platform == "win32" and not is_ascii_basename(str(video_path)):
        work = cfg.TEMP_DIR / f"{task_id}_embed.mp4"
        shutil.copy2(video_path, work)
        return [
            ("win-ascii-temp", work, temp_out),
            ("win-ascii-direct", work, None),
        ]
    return [
        ("temp-output", video_path, temp_out),
        ("direct", video_path, None),
    ]


async def embed_thumbnail(video_path: Path, thumb_path: Path, task_id: str) -> bool:
    """Try embedding strategies in order, return on first success."""
    if not thumb_path.is_file() or not video_path.is_file():
        return False
    try:
        strategies = _build_strategies(video_path, task_id)
    except OSError as e:
        logger.warning("embed strategies: %s", e)
        return False
    for label, video_for_embed, temp_output in strategies:
        ok = await _ffmpeg_embed(video_for_embed, thumb_path, temp_output)
        if ok:
            if video_for_embed != video_path:
                replace_or_move_overwrite(str(video_for_embed), str(video_path))
            return True
        logger.warning("embed strategy '%s' failed", label)
    return False


async def thumbnail_worker() -> None:
    while True:
        job = None
        try:
            if tm.thumb_queue is None:
                await asyncio.sleep(0.5)
                continue
            job = await tm.thumb_queue.get()
            video_path = job["video_path"]
            thumb_url = job["thumb_url"]
            task_id = job["task_id"]

            if tm.exists(task_id):
                await tm.update(
                    task_id,
                    status=TaskStatus.THUMBNAIL,
                    progress=float(PROGRESS_THUMB_DL),
                    message="Downloading thumbnail...",
                )

            logger.info("THUMB-WORKER processing: %s", os.path.basename(video_path))
            thumb_path = str(cfg.TEMP_DIR / f"{task_id}_thumb.jpg")
            vpath = Path(video_path)

            if await download_thumbnail(
                thumb_url,
                thumb_path,
                cookie=job.get("cookie"),
                referer=job.get("referer"),
            ):
                if not await normalize_thumbnail_to_jpeg_for_embed(thumb_path):
                    logger.warning(
                        "thumbnail not JPEG/PNG and conversion failed; embed may fail (%s)",
                        os.path.basename(thumb_path),
                    )
                if tm.exists(task_id):
                    await tm.update(
                        task_id,
                        progress=float(PROGRESS_THUMB_EMBED),
                        message="Embedding thumbnail...",
                    )

                start = datetime.now()
                success = await embed_thumbnail(vpath, Path(thumb_path), task_id)
                elapsed = (datetime.now() - start).total_seconds()

                if success:
                    logger.info(
                        "THUMB-WORKER done: %s (%.1fs)", os.path.basename(video_path), elapsed
                    )
                    if tm.exists(task_id):
                        t = tm.get(task_id)
                        fs = t.file_size if t else 0
                        size_mb = fs / (1024 * 1024) if fs else 0
                        await tm.update(
                            task_id,
                            status=TaskStatus.COMPLETED,
                            progress=100.0,
                            message=f"Done! {size_mb:.1f}MB [+thumb]",
                            completed_at=datetime.now().isoformat(),
                        )
                else:
                    logger.warning("THUMB-WORKER failed embed: %s", os.path.basename(video_path))
                    if tm.exists(task_id):
                        t = tm.get(task_id)
                        prev = t.message if t else "Done!"
                        await tm.update(
                            task_id,
                            status=TaskStatus.COMPLETED,
                            progress=100.0,
                            message=f"{prev} [no thumb]",
                            completed_at=datetime.now().isoformat(),
                        )

                try:
                    if os.path.exists(thumb_path):
                        os.remove(thumb_path)
                except OSError as e:
                    logger.debug("thumb temp remove: %s", e)
            else:
                logger.warning(
                    "THUMB-WORKER failed download thumbnail: %s", os.path.basename(video_path)
                )
                if tm.exists(task_id):
                    t = tm.get(task_id)
                    prev = t.message if t else "Done!"
                    await tm.update(
                        task_id,
                        status=TaskStatus.COMPLETED,
                        progress=100.0,
                        message=f"{prev} [thumb failed]",
                        completed_at=datetime.now().isoformat(),
                    )

        except Exception:
            logger.exception("thumbnail_worker")
            if job and job.get("task_id") and tm.exists(job["task_id"]):
                tid = job["task_id"]
                t = tm.get(tid)
                if t and t.status == TaskStatus.THUMBNAIL:
                    prev = t.message if t else "Done!"
                    await tm.update(
                        tid,
                        status=TaskStatus.COMPLETED,
                        progress=100.0,
                        message=f"{prev} [thumb error]",
                        completed_at=datetime.now().isoformat(),
                    )
        finally:
            if tm.thumb_queue is not None and job is not None:
                tm.thumb_queue.task_done()
