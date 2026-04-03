"""Thumbnail download, normalization, embed, and background worker."""

from __future__ import annotations

import logging
import os
import shutil
import sys
from datetime import datetime
from typing import Dict, Optional

import asyncio
import httpx

from app import config as cfg
from app import state
from app.services import http_client
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


async def embed_thumbnail_ffmpeg(
    video_path: str, thumb_path: str, temp_output: Optional[str] = None
) -> bool:
    out_path = temp_output if temp_output else (video_path + ".thumb.mp4")
    try:
        cmd = [
            FFMPEG,
            "-y",
            "-i",
            video_path,
            "-i",
            thumb_path,
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
            out_path,
        ]
        process = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        out, err = await process.communicate()
        if process.returncode == 0 and os.path.exists(out_path):
            replace_or_move_overwrite(out_path, video_path)
            return True
        if err or out:
            rc = subprocess_exit_code(process.returncode)
            logger.warning("ffmpeg embed exit=%s %s", rc, stderr_tail(err or out))
        if os.path.exists(out_path):
            try:
                os.remove(out_path)
            except OSError:
                pass
    except Exception as e:
        logger.exception("ffmpeg embed: %s", e)
    return False


async def embed_thumbnail_ffmpeg_with_temp_out(
    video_path: str, thumb_path: str, task_id: str
) -> bool:
    out_path = os.path.join(cfg.TEMP_DIR, f"{task_id}_thumb_out.mp4")
    return await embed_thumbnail_ffmpeg(video_path, thumb_path, temp_output=out_path)


async def embed_thumbnail_atomic(
    video_path: str, thumb_path: str, task_id: Optional[str] = None
) -> bool:
    if not os.path.exists(thumb_path) or not os.path.exists(video_path):
        return False
    try:
        if task_id:
            return await embed_thumbnail_ffmpeg_with_temp_out(video_path, thumb_path, task_id)
        return await embed_thumbnail_ffmpeg(video_path, thumb_path)
    except Exception as e:
        logger.exception("embed_thumbnail_atomic primary: %s", e)
    # Single fallback without re-entering the same failing path twice
    try:
        if task_id:
            return await embed_thumbnail_ffmpeg(video_path, thumb_path)
        return False
    except Exception as e:
        logger.exception("embed_thumbnail_atomic fallback: %s", e)
        return False


async def embed_thumbnail_via_ascii_workdir(
    video_path: str, thumb_path: str, task_id: str
) -> bool:
    work_video = os.path.join(cfg.TEMP_DIR, f"{task_id}_embed.mp4")
    try:
        shutil.copy2(video_path, work_video)
    except OSError as e:
        logger.warning("copy to ASCII temp failed: %s", e)
        return await embed_thumbnail_atomic(video_path, thumb_path, task_id=task_id)

    ok = await embed_thumbnail_atomic(work_video, thumb_path, task_id=task_id)
    if ok:
        try:
            replace_or_move_overwrite(work_video, video_path)
        except OSError as e:
            logger.error("replace result failed: %s", e)
            return False
        return True

    try:
        if os.path.exists(work_video):
            os.remove(work_video)
    except OSError:
        pass
    return False


async def thumbnail_worker() -> None:
    while True:
        job = None
        try:
            if state.thumb_queue is None:
                await asyncio.sleep(0.5)
                continue
            job = await state.thumb_queue.get()
            video_path = job["video_path"]
            thumb_url = job["thumb_url"]
            task_id = job["task_id"]

            if task_id in state.tasks:
                state.tasks[task_id]["status"] = "thumbnail"
                state.tasks[task_id]["progress"] = 92
                state.tasks[task_id]["message"] = "Downloading thumbnail..."

            logger.info("THUMB-WORKER processing: %s", os.path.basename(video_path))
            thumb_path = os.path.join(cfg.TEMP_DIR, f"{task_id}_thumb.jpg")

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
                if task_id in state.tasks:
                    state.tasks[task_id]["progress"] = 95
                    state.tasks[task_id]["message"] = "Embedding thumbnail..."

                start = datetime.now()
                if sys.platform == "win32" and not is_ascii_basename(video_path):
                    success = await embed_thumbnail_via_ascii_workdir(
                        video_path, thumb_path, task_id
                    )
                else:
                    success = await embed_thumbnail_atomic(
                        video_path, thumb_path, task_id=task_id
                    )
                elapsed = (datetime.now() - start).total_seconds()

                if success:
                    logger.info(
                        "THUMB-WORKER done: %s (%.1fs)", os.path.basename(video_path), elapsed
                    )
                    if task_id in state.tasks:
                        state.tasks[task_id]["status"] = "completed"
                        state.tasks[task_id]["progress"] = 100
                        fs = state.tasks[task_id].get("file_size", 0)
                        size_mb = fs / (1024 * 1024) if fs else 0
                        state.tasks[task_id]["message"] = f"Done! {size_mb:.1f}MB [+thumb]"
                        state.tasks[task_id]["completed_at"] = datetime.now().isoformat()
                else:
                    logger.warning("THUMB-WORKER failed embed: %s", os.path.basename(video_path))
                    if task_id in state.tasks:
                        state.tasks[task_id]["status"] = "completed"
                        state.tasks[task_id]["progress"] = 100
                        state.tasks[task_id]["message"] = (
                            state.tasks[task_id].get("message", "Done!") + " [no thumb]"
                        )
                        state.tasks[task_id]["completed_at"] = datetime.now().isoformat()

                try:
                    if os.path.exists(thumb_path):
                        os.remove(thumb_path)
                except OSError as e:
                    logger.debug("thumb temp remove: %s", e)
            else:
                logger.warning(
                    "THUMB-WORKER failed download thumbnail: %s", os.path.basename(video_path)
                )
                if task_id in state.tasks:
                    state.tasks[task_id]["status"] = "completed"
                    state.tasks[task_id]["progress"] = 100
                    state.tasks[task_id]["message"] = (
                        state.tasks[task_id].get("message", "Done!") + " [thumb failed]"
                    )
                    state.tasks[task_id]["completed_at"] = datetime.now().isoformat()

        except Exception:
            logger.exception("thumbnail_worker")
            if job and job.get("task_id") in state.tasks:
                tid = job["task_id"]
                if state.tasks[tid].get("status") == "thumbnail":
                    state.tasks[tid]["status"] = "completed"
                    state.tasks[tid]["progress"] = 100
                    state.tasks[tid]["message"] = (
                        state.tasks[tid].get("message", "Done!") + " [thumb error]"
                    )
                    state.tasks[tid]["completed_at"] = datetime.now().isoformat()
        finally:
            if state.thumb_queue is not None and job is not None:
                state.thumb_queue.task_done()
