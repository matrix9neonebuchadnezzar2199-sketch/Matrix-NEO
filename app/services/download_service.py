"""HLS / progressive HTTP downloads (N_m3u8DL-RE, ffmpeg)."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse

import aiofiles
import httpx

from app import config as cfg
from app import state
from app.services import http_client
from app.utils.filename import sanitize_filename_for_windows
from app.utils.paths import FFMPEG, N_M3U8DL_RE
from app.utils.process import stderr_tail, subprocess_exit_code

logger = logging.getLogger(__name__)

_RE_PROGRESS_PCT = re.compile(r"(\d+\.?\d*)%")
_RE_SPEED = re.compile(r"(\d+\.?\d*\s*[KMG]?B/s)")
_RE_SIZE_SLASH = re.compile(r"(\d+\.?\d*\s*[KMG]?B)\s*/")


class M3u8StallMonitor:
    """N_m3u8DL の進捗行で a/b が進まず 0.00Bps が続く場合に True を返す。"""

    __slots__ = ("stall_sec", "_last_ab", "_zero_since")

    def __init__(self, stall_sec: float):
        self.stall_sec = float(stall_sec)
        self._last_ab: Optional[tuple[int, int]] = None
        self._zero_since: Optional[float] = None

    def feed(self, text: str) -> bool:
        if self.stall_sec <= 0:
            return False
        tl = text.lower()
        if "merge" in tl or "muxing" in tl:
            self._zero_since = None
            return False
        m = re.search(r"(\d+)/(\d+)\s+[\d.]+%", text)
        if not m:
            return False
        a, b = int(m.group(1)), int(m.group(2))
        if a >= b:
            self._zero_since = None
            return False
        if not re.search(r"0\.00Bps", text):
            self._last_ab = (a, b)
            self._zero_since = None
            return False
        if self._last_ab == (a, b):
            if self._zero_since is None:
                self._zero_since = time.monotonic()
            elif time.monotonic() - self._zero_since >= self.stall_sec:
                return True
        else:
            self._last_ab = (a, b)
            self._zero_since = time.monotonic()
        return False


async def terminate_child_process(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        return
    try:
        proc.kill()
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(proc.wait(), timeout=20.0)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass


def m3u8_static_header_args() -> list:
    if os.environ.get("MATRIX_NEO_M3U8_BROWSER_HEADERS", "1").strip().lower() in (
        "0",
        "false",
        "no",
        "off",
    ):
        return []
    ua = cfg.DEFAULT_UA
    return [
        "--header",
        f"User-Agent: {ua}",
        "--header",
        "Accept: */*",
        "--header",
        "Accept-Language: ja,en-US;q=0.9,en;q=0.8",
    ]


def _replace_or_move_overwrite(src: str, dst: str) -> None:
    import shutil

    try:
        os.replace(src, dst)
    except OSError:
        shutil.copy2(src, dst)
        try:
            os.remove(src)
        except OSError:
            pass


def is_direct_progressive_http_url(url: str) -> bool:
    try:
        p = urlparse(url)
        if p.scheme not in ("http", "https"):
            return False
        path = (p.path or "").lower()
        if path.endswith((".mp4", ".m4v", ".webm")):
            return True
        u = url.lower()
        if ".mp4?" in u or ".m4v?" in u or ".webm?" in u:
            return True
        return False
    except Exception:
        return False


async def download_http_progressive_file(
    task_id: str,
    url: str,
    dest_path: str,
    cookie: Optional[str],
    referer: Optional[str],
) -> bool:
    ua = cfg.DEFAULT_UA
    headers = {
        "User-Agent": ua,
        "Accept": "*/*",
        "Accept-Encoding": "identity",
    }
    if cookie and cookie.strip():
        headers["Cookie"] = cookie.strip()
    if referer and referer.strip():
        headers["Referer"] = referer.strip()

    tmp = dest_path + ".part"
    try:
        if os.path.isfile(tmp):
            os.remove(tmp)
    except OSError:
        pass

    timeout = httpx.Timeout(3600.0, connect=60.0)
    client = http_client.get_client()
    try:
        async with client.stream("GET", url, headers=headers, timeout=timeout) as response:
            if response.status_code < 200 or response.status_code >= 400:
                logger.warning("direct HTTP status=%s", response.status_code)
                return False
            total = int(response.headers.get("content-length") or 0)
            downloaded = 0
            async with aiofiles.open(tmp, "wb") as f:
                async for chunk in response.aiter_bytes(chunk_size=256 * 1024):
                    if not chunk:
                        continue
                    await f.write(chunk)
                    downloaded += len(chunk)
                    if total > 0:
                        state.tasks[task_id]["progress"] = min(88, int(downloaded * 88 / total))
                    else:
                        mb = downloaded // (1024 * 1024)
                        state.tasks[task_id]["progress"] = min(88, 5 + mb * 3)
            if downloaded < 1024 * 1024:
                logger.warning("direct file too small: %s bytes", downloaded)
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                return False
            os.replace(tmp, dest_path)
            return True
    except Exception as e:
        logger.exception("direct HTTP: %s", e)
        try:
            if os.path.isfile(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return False


async def ffmpeg_normalize_container_to_mp4(src_path: str) -> bool:
    tmp = src_path + ".neo_norm.mp4"
    attempts = [
        ([], "default"),
        (["-f", "mpegts"], "mpegts"),
        (
            ["-probesize", str(500 * 1024 * 1024), "-analyzeduration", str(100 * 1000000)],
            "deep_probe",
        ),
    ]
    for extra, label in attempts:
        cmd = [FFMPEG, "-y", "-hide_banner", "-loglevel", "warning"]
        cmd.extend(extra)
        cmd.extend(
            [
                "-i",
                src_path,
                "-map",
                "0",
                "-c",
                "copy",
                "-movflags",
                "+faststart",
                tmp,
            ]
        )
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            out, err = await proc.communicate()
        except OSError as e:
            logger.warning("normalize (%s) spawn: %s", label, e)
            continue
        if proc.returncode == 0 and os.path.exists(tmp):
            sz = os.path.getsize(tmp)
            if sz > 1024 * 1024:
                try:
                    os.replace(tmp, src_path)
                    return True
                except OSError as e:
                    logger.error("normalize replace: %s", e)
            try:
                os.remove(tmp)
            except OSError:
                pass
        elif err:
            rc = subprocess_exit_code(proc.returncode)
            logger.warning("normalize (%s) exit=%s %s", label, rc, stderr_tail(err, 500))
        elif os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    return False


async def run_download(
    task_id: str,
    url: str,
    filename: str,
    thumbnail_url: Optional[str] = None,
    request_quality: Optional[str] = None,
    cookie: Optional[str] = None,
    referer: Optional[str] = None,
) -> None:
    async with state.semaphore:
        try:
            filename = sanitize_filename_for_windows(filename)
            if task_id in state.tasks:
                state.tasks[task_id]["filename"] = filename

            state.tasks[task_id]["status"] = "downloading"
            state.tasks[task_id]["message"] = "Starting..."

            output_path = os.path.join(cfg.OUTPUT_DIR, filename)
            base_name = filename.rsplit(".", 1)[0]

            if is_direct_progressive_http_url(url):
                state.tasks[task_id]["message"] = "Downloading (HTTP)..."
                wall_start = datetime.now()
                ok = await download_http_progressive_file(
                    task_id, url, output_path, cookie, referer
                )
                download_time = (datetime.now() - wall_start).total_seconds()
                if (
                    not ok
                    or not os.path.isfile(output_path)
                    or os.path.getsize(output_path) < 1024 * 1024
                ):
                    state.tasks[task_id]["status"] = "error"
                    state.tasks[task_id]["message"] = "Direct download failed"
                    logger.error("Direct download failed: %s", filename)
                    return
                file_size = os.path.getsize(output_path)
                size_mb = file_size / (1024 * 1024)
                speed_mbps = size_mb / download_time if download_time > 0 else 0
                state.tasks[task_id]["progress"] = 88
                state.tasks[task_id]["file_size"] = file_size
                logger.info("Direct HTTP OK: %s (%.1fMB)", filename, size_mb)
                if thumbnail_url and state.thumb_queue:
                    await asyncio.sleep(0.75)
                    state.tasks[task_id]["status"] = "thumbnail"
                    state.tasks[task_id]["progress"] = 90
                    state.tasks[task_id]["message"] = (
                        f"Done DL ({size_mb:.1f}MB) - Processing thumbnail..."
                    )
                    await state.thumb_queue.put(
                        {
                            "video_path": output_path,
                            "thumb_url": thumbnail_url,
                            "task_id": task_id,
                            "cookie": cookie,
                            "referer": referer,
                        }
                    )
                    logger.info(
                        "Completed: %s (%.1f MB, %.1f MB/s) thumb queued",
                        filename,
                        size_mb,
                        speed_mbps,
                    )
                else:
                    state.tasks[task_id]["status"] = "completed"
                    state.tasks[task_id]["progress"] = 100
                    state.tasks[task_id]["message"] = f"Done! {size_mb:.1f}MB ({speed_mbps:.1f}MB/s)"
                    state.tasks[task_id]["completed_at"] = datetime.now().isoformat()
                    logger.info("Completed: %s [no thumb]", filename)
                return

            save_name_dl = "mneo_" + re.sub(r"[^0-9A-Za-z_]+", "_", task_id).strip("_")

            _mux_ts = os.environ.get("MATRIX_NEO_M3U8_MUX_TS", "1").strip().lower() not in (
                "0",
                "false",
                "no",
                "off",
            )

            def _build_m3u8_cmd(use_mt: bool) -> list:
                c = [
                    N_M3U8DL_RE,
                    url,
                    "--save-dir",
                    cfg.OUTPUT_DIR,
                    "--save-name",
                    save_name_dl,
                    "--auto-select",
                    "--thread-count",
                    str(cfg.MAX_THREADS),
                    "--download-retry-count",
                    str(cfg.M3U8_DOWNLOAD_RETRY),
                    "--http-request-timeout",
                    str(cfg.M3U8_HTTP_TIMEOUT),
                    "--tmp-dir",
                    cfg.TEMP_DIR,
                    "--no-log",
                    "--del-after-done",
                ]
                if _mux_ts:
                    c.extend(
                        [
                            "--binary-merge",
                            "-M",
                            "format=ts:muxer=ffmpeg:keep=false",
                        ]
                    )
                if use_mt:
                    c.append("-mt")
                if cfg.M3U8_MAX_SPEED:
                    c.extend(["--max-speed", cfg.M3U8_MAX_SPEED])
                if FFMPEG and FFMPEG != "ffmpeg" and os.path.isfile(FFMPEG):
                    c.extend(["--ffmpeg-binary-path", FFMPEG])
                c.extend(m3u8_static_header_args())
                if cookie and cookie.strip():
                    c.extend(["--header", f"Cookie: {cookie.strip()}"])
                if referer and referer.strip():
                    c.extend(["--header", f"Referer: {referer.strip()}"])
                return c

            attempt_mt = cfg.M3U8_USE_MT
            killed_stall = False
            process: Optional[asyncio.subprocess.Process] = None
            wall_start = datetime.now()

            for attempt_round in range(2):
                cmd = _build_m3u8_cmd(attempt_mt)
                stall_monitor = M3u8StallMonitor(cfg.M3U8_STALL_SEC)

                if attempt_round == 0:
                    logger.info(
                        "Starting HLS: %s [save_name=%s threads=%s retry=%s mt=%s stall=%ss]",
                        filename,
                        save_name_dl,
                        cfg.MAX_THREADS,
                        cfg.M3U8_DOWNLOAD_RETRY,
                        attempt_mt,
                        cfg.M3U8_STALL_SEC,
                    )
                else:
                    logger.info("Retry (stall recovery): mt=%s", attempt_mt)
                    state.tasks[task_id]["message"] = "Retrying download without -mt..."

                process = await asyncio.create_subprocess_exec(
                    *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
                )

                is_merging = False
                killed_stall = False
                async for line in process.stdout:
                    text = line.decode("utf-8", errors="ignore").strip()
                    if not text:
                        continue
                    logger.debug("N_m3u8DL %s", text)

                    if stall_monitor.feed(text):
                        killed_stall = True
                        logger.warning(
                            "Stall at 0.00Bps for %ss — terminating", cfg.M3U8_STALL_SEC
                        )
                        state.tasks[task_id]["message"] = "Stalled — stopping downloader..."
                        await terminate_child_process(process)
                        break

                    if "mux" in text.lower() or "merge" in text.lower() or "muxing" in text.lower():
                        if not is_merging:
                            is_merging = True
                            state.tasks[task_id]["status"] = "merging"
                            state.tasks[task_id]["progress"] = 85
                            state.tasks[task_id]["message"] = "Merging segments..."
                        continue

                    progress_match = _RE_PROGRESS_PCT.search(text)
                    if progress_match and not is_merging:
                        raw_progress = float(progress_match.group(1))
                        state.tasks[task_id]["progress"] = min(raw_progress * 0.8, 80)

                    speed_match = _RE_SPEED.search(text)
                    size_match = _RE_SIZE_SLASH.search(text)
                    if speed_match and not is_merging:
                        msg = f"{state.tasks[task_id]['progress']:.0f}%"
                        if size_match:
                            msg += f" - {size_match.group(1)}"
                        msg += f" ({speed_match.group(1)})"
                        state.tasks[task_id]["message"] = msg

                await process.wait()
                logger.info("N_m3u8DL exit code: %s", process.returncode)

                if (
                    killed_stall
                    and attempt_round == 0
                    and cfg.M3U8_USE_MT
                    and cfg.M3U8_RETRY_NO_MT_ON_STALL
                    and attempt_mt
                ):
                    attempt_mt = False
                    continue
                break

            download_time = (datetime.now() - wall_start).total_seconds()

            def _pick_downloaded_media(prefix: str) -> Optional[str]:
                for ext in (".ts", ".mp4", ".m4s", ".mkv"):
                    p = os.path.join(cfg.OUTPUT_DIR, prefix + ext)
                    if os.path.isfile(p) and os.path.getsize(p) > 1024 * 1024:
                        return p
                return None

            raw_output = _pick_downloaded_media(save_name_dl)
            if not raw_output:
                raw_output = _pick_downloaded_media(base_name)
            if not raw_output and os.path.isfile(output_path) and os.path.getsize(output_path) > 1024 * 1024:
                raw_output = output_path

            file_exists = raw_output is not None and os.path.exists(raw_output)
            file_size = os.path.getsize(raw_output) if file_exists else 0

            if file_exists and file_size > 1024 * 1024:
                size_mb = file_size / (1024 * 1024)
                speed_mbps = size_mb / download_time if download_time > 0 else 0

                if not raw_output.endswith(".mp4"):
                    state.tasks[task_id]["status"] = "merging"
                    state.tasks[task_id]["progress"] = 85
                    state.tasks[task_id]["message"] = f"Converting to MP4... ({size_mb:.1f}MB)"
                    logger.info("Remux TS→MP4: %s", os.path.basename(raw_output))

                    remux_cmd = [
                        FFMPEG,
                        "-y",
                        "-fflags",
                        "+genpts",
                        "-i",
                        raw_output,
                        "-map",
                        "0:v:0",
                        "-map",
                        "0:a:0",
                        "-c",
                        "copy",
                        "-bsf:v",
                        "h264_mp4toannexb,h264_redundant_pps",
                        "-movflags",
                        "+faststart",
                        output_path,
                    ]
                    remux_proc = await asyncio.create_subprocess_exec(
                        *remux_cmd,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.STDOUT,
                    )
                    await remux_proc.wait()

                    if os.path.exists(output_path) and os.path.getsize(output_path) > 1024 * 1024:
                        os.remove(raw_output)
                        file_size = os.path.getsize(output_path)
                        size_mb = file_size / (1024 * 1024)
                        logger.info("Remux done: %s (%.1fMB)", filename, size_mb)
                    else:
                        logger.warning("Remux failed, keeping original: %s", os.path.basename(raw_output))
                        if raw_output != output_path:
                            os.rename(raw_output, output_path)
                else:
                    state.tasks[task_id]["status"] = "merging"
                    state.tasks[task_id]["progress"] = 88
                    state.tasks[task_id]["message"] = "Normalizing MP4..."
                    logger.info("Normalizing MP4: %s", filename)
                    if await ffmpeg_normalize_container_to_mp4(output_path):
                        file_size = os.path.getsize(output_path)
                        size_mb = file_size / (1024 * 1024)
                        logger.info("MP4 normalized OK (%.1fMB)", size_mb)
                    else:
                        logger.warning("MP4 normalize failed — thumbnail embed may fail")

                state.tasks[task_id]["file_size"] = file_size

                if thumbnail_url and state.thumb_queue:
                    await asyncio.sleep(0.75)
                    state.tasks[task_id]["status"] = "thumbnail"
                    state.tasks[task_id]["progress"] = 90
                    state.tasks[task_id]["message"] = (
                        f"Done DL ({size_mb:.1f}MB) - Processing thumbnail..."
                    )

                    await state.thumb_queue.put(
                        {
                            "video_path": output_path,
                            "thumb_url": thumbnail_url,
                            "task_id": task_id,
                            "cookie": cookie,
                            "referer": referer,
                        }
                    )
                    logger.info(
                        "Completed: %s (%.1f MB in %.1fs) thumb queued",
                        filename,
                        size_mb,
                        download_time,
                    )
                else:
                    state.tasks[task_id]["status"] = "completed"
                    state.tasks[task_id]["progress"] = 100
                    state.tasks[task_id]["message"] = f"Done! {size_mb:.1f}MB ({speed_mbps:.1f}MB/s)"
                    state.tasks[task_id]["completed_at"] = datetime.now().isoformat()
                    logger.info("Completed: %s [no thumb]", filename)
            else:
                state.tasks[task_id]["status"] = "error"
                if killed_stall:
                    state.tasks[task_id]["message"] = (
                        "Stalled (0 Bps). Set MATRIX_NEO_M3U8_MT=0 or increase MATRIX_NEO_M3U8_STALL_SEC"
                    )
                else:
                    state.tasks[task_id]["message"] = "Download failed"
                logger.error(
                    "Failed: %s (exists=%s size=%s stall=%s)",
                    filename,
                    file_exists,
                    file_size,
                    killed_stall,
                )

        except asyncio.CancelledError:
            state.tasks[task_id]["status"] = "error"
            state.tasks[task_id]["message"] = "Cancelled"
        except Exception as e:
            state.tasks[task_id]["status"] = "error"
            state.tasks[task_id]["message"] = str(e)[:50]
            logger.exception("run_download: %s", e)
        finally:
            state.active_downloads.pop(task_id, None)
