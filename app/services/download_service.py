"""HLS / progressive HTTP downloads (N_m3u8DL-RE, ffmpeg)."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from collections import deque
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import httpx

from app import config as cfg
from app.constants import (
    HTTP_CHUNK_SIZE,
    MAX_ERROR_MSG_LEN,
    MIN_VALID_FILE_BYTES,
    PROGRESS_DIRECT_DL_CAP,
    PROGRESS_DL_CAP,
    PROGRESS_MERGE,
    PROGRESS_NORMALIZE,
    PROGRESS_THUMB_QUEUE,
    TERMINATE_WAIT_SEC,
    THUMB_QUEUE_DELAY_SEC,
)
from app.models import TaskStatus
from app.services import http_client
from app.state import tm
from app.utils.disk import check_disk_space
from app.utils.file_ops import replace_or_move_overwrite
from app.utils.filename import sanitize_filename_for_windows
from app.utils.paths import FFMPEG, N_M3U8DL_RE
from app.utils.process import stderr_tail, subprocess_exit_code
from app.utils.timeutil import utcnow_iso
from app.utils.url_connection import url_with_pinned_ip

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
        await asyncio.wait_for(proc.wait(), timeout=TERMINATE_WAIT_SEC)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass


def m3u8_static_header_args() -> list[str]:
    if not cfg.M3U8_BROWSER_HEADERS:
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


async def _run_progressive(
    task_id: str,
    url: str,
    output_path: Path,
    cookie: Optional[str],
    referer: Optional[str],
    *,
    resolved_ips: Optional[list[str]] = None,
) -> Optional[Path]:
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

    req_url = url
    if resolved_ips:
        pinned, host_header = url_with_pinned_ip(url, resolved_ips)
        req_url = pinned
        if host_header:
            headers["Host"] = host_header

    dest_str = str(output_path)
    tmp = dest_str + ".part"

    # --- HTTP Range resume: reuse existing .part file ---
    existing_bytes = 0
    if os.path.isfile(tmp):
        existing_bytes = os.path.getsize(tmp)
        if existing_bytes > 0:
            headers["Range"] = f"bytes={existing_bytes}-"
            logger.info("Resuming from %s bytes: %s", existing_bytes, os.path.basename(tmp))

    timeout = httpx.Timeout(connect=30.0, read=300.0, write=30.0, pool=30.0)
    client = http_client.get_client()
    try:
        async with client.stream("GET", req_url, headers=headers, timeout=timeout) as response:
            status = response.status_code

            # If server does not support Range, start fresh
            if existing_bytes > 0 and status != 206:
                existing_bytes = 0
                try:
                    os.remove(tmp)
                except OSError:
                    pass

            if status not in (200, 206):
                logger.warning("direct HTTP status=%s", status)
                return None

            cl = int(response.headers.get("content-length") or 0)
            total = (existing_bytes + cl) if status == 206 else cl
            downloaded = existing_bytes
            mode = "ab" if existing_bytes > 0 and status == 206 else "wb"
            with open(tmp, mode) as f:
                async for chunk in response.aiter_bytes(chunk_size=HTTP_CHUNK_SIZE):
                    if not chunk:
                        continue
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total > 0:
                        p = min(
                            PROGRESS_DIRECT_DL_CAP,
                            int(downloaded * PROGRESS_DIRECT_DL_CAP / total),
                        )
                        await tm.update(task_id, progress=float(p))
                    else:
                        mb = downloaded // (1024 * 1024)
                        await tm.update(
                            task_id,
                            progress=float(min(PROGRESS_DIRECT_DL_CAP, 5 + mb * 3)),
                        )
            if downloaded < MIN_VALID_FILE_BYTES:
                logger.warning("direct file too small: %s bytes", downloaded)
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                return None
            os.replace(tmp, dest_str)
            return output_path
    except httpx.ConnectError:
        logger.exception("direct HTTP connect")
        await tm.update(task_id, status=TaskStatus.ERROR, message="Connection failed")
    except httpx.HTTPStatusError as e:
        logger.exception("direct HTTP status error")
        await tm.update(
            task_id,
            status=TaskStatus.ERROR,
            message=f"HTTP {e.response.status_code}",
        )
    except OSError as e:
        logger.exception("direct HTTP file")
        await tm.update(task_id, status=TaskStatus.ERROR, message=f"File error: {e.strerror}")
    except httpx.TimeoutError:
        logger.exception("direct HTTP timeout")
        await tm.update(task_id, status=TaskStatus.ERROR, message="Timed out")
    except Exception as e:
        logger.exception("direct HTTP: %s", e)
        await tm.update(task_id, status=TaskStatus.ERROR, message=str(e)[:MAX_ERROR_MSG_LEN])
    # Keep .part file for potential resume (do NOT delete)
    return None


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
            if sz > MIN_VALID_FILE_BYTES:
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


async def _remux_ts_to_mp4(raw_path: str, output_path: Path) -> bool:
    remux_cmd = [
        FFMPEG,
        "-y",
        "-fflags",
        "+genpts",
        "-i",
        raw_path,
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
        str(output_path),
    ]
    remux_proc = await asyncio.create_subprocess_exec(
        *remux_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    await remux_proc.wait()
    return bool(
        os.path.exists(str(output_path)) and os.path.getsize(str(output_path)) > MIN_VALID_FILE_BYTES
    )


async def _run_m3u8(
    task_id: str,
    url: str,
    filename: str,
    output_path: Path,
    cookie: Optional[str],
    referer: Optional[str],
) -> tuple[Optional[Path], bool]:
    """Run N_m3u8DL-RE; returns (media path or None, killed_stall)."""
    base_name = filename.rsplit(".", 1)[0]
    save_name_dl = "mneo_" + re.sub(r"[^0-9A-Za-z_]+", "_", task_id).strip("_")

    def _build_m3u8_cmd(use_mt: bool) -> list[str]:
        out_dir = str(cfg.OUTPUT_DIR)
        temp_dir = str(cfg.TEMP_DIR)
        c = [
            N_M3U8DL_RE,
            url,
            "--save-dir",
            out_dir,
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
            temp_dir,
            "--no-log",
            "--del-after-done",
        ]
        if cfg.M3U8_MUX_TS:
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

    try:
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
                await tm.update(task_id, message="Retrying download without -mt...")

            process = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
            )

            is_merging = False
            killed_stall = False
            last_lines: deque[str] = deque(maxlen=5)
            assert process.stdout is not None
            async for line in process.stdout:
                text = line.decode("utf-8", errors="ignore").strip()
                if not text:
                    continue
                last_lines.append(text)
                logger.debug("N_m3u8DL %s", text)

                if stall_monitor.feed(text):
                    killed_stall = True
                    logger.warning("Stall at 0.00Bps for %ss — terminating", cfg.M3U8_STALL_SEC)
                    await tm.update(task_id, message="Stalled — stopping downloader...")
                    await terminate_child_process(process)
                    break

                tl = text.lower()
                if "mux" in tl or "merge" in tl or "muxing" in tl:
                    if not is_merging:
                        is_merging = True
                        await tm.update(
                            task_id,
                            status=TaskStatus.MERGING,
                            progress=float(PROGRESS_MERGE),
                            message="Merging segments...",
                        )
                    continue

                progress_match = _RE_PROGRESS_PCT.search(text)
                if progress_match and not is_merging:
                    raw_progress = float(progress_match.group(1))
                    await tm.update(
                        task_id,
                        progress=min(raw_progress * 0.8, PROGRESS_DL_CAP),
                    )

                speed_match = _RE_SPEED.search(text)
                size_match = _RE_SIZE_SLASH.search(text)
                if speed_match and not is_merging:
                    t = tm.get(task_id)
                    cur_prog = float(t.progress) if t else 0.0
                    msg = f"{cur_prog:.0f}%"
                    if size_match:
                        msg += f" - {size_match.group(1)}"
                    msg += f" ({speed_match.group(1)})"
                    await tm.update(task_id, message=msg)

            await process.wait()
            logger.info("N_m3u8DL exit code: %s", process.returncode)
            if process.returncode != 0 and not killed_stall:
                tail = " | ".join(last_lines)
                logger.warning(
                    "N_m3u8DL non-zero exit=%s for %s tail=%s",
                    process.returncode,
                    filename,
                    tail[:400],
                )
                await tm.update(task_id, message=f"N_m3u8DL exit {process.returncode}")

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
    except asyncio.CancelledError:
        if process is not None and process.returncode is None:
            await terminate_child_process(process)
        raise

    def _pick_downloaded_media(prefix: str) -> Optional[str]:
        od = str(cfg.OUTPUT_DIR)
        for ext in (".ts", ".mp4", ".m4s", ".mkv"):
            p = os.path.join(od, prefix + ext)
            if os.path.isfile(p) and os.path.getsize(p) > MIN_VALID_FILE_BYTES:
                return p
        return None

    raw_output = _pick_downloaded_media(save_name_dl)
    if not raw_output:
        raw_output = _pick_downloaded_media(base_name)
    outp = str(output_path)
    if not raw_output and os.path.isfile(outp) and os.path.getsize(outp) > MIN_VALID_FILE_BYTES:
        raw_output = outp

    if not raw_output:
        return None, killed_stall

    file_exists = os.path.exists(raw_output)
    file_size = os.path.getsize(raw_output) if file_exists else 0
    if not file_exists or file_size <= MIN_VALID_FILE_BYTES:
        return None, killed_stall

    # Rename/move to user output_path when downloader used ASCII save_name
    if raw_output.endswith(".mp4") and os.path.normcase(os.path.normpath(raw_output)) != os.path.normcase(
        os.path.normpath(outp)
    ):
        try:
            replace_or_move_overwrite(raw_output, outp)
            logger.info(
                "Renamed %s → %s",
                os.path.basename(raw_output),
                os.path.basename(outp),
            )
            raw_output = outp
            file_size = os.path.getsize(outp)
        except OSError as e:
            logger.error("Rename to output_path failed: %s", e)

    return Path(raw_output), killed_stall


async def _finalize_with_optional_thumbnail(
    task_id: str,
    filename: str,
    video_path: str,
    file_size: int,
    download_time: float,
    thumbnail_url: Optional[str],
    cookie: Optional[str],
    referer: Optional[str],
) -> None:
    """Shared finalization: update file_size, enqueue thumbnail or mark completed."""
    size_mb = file_size / (1024 * 1024)
    speed_mbps = size_mb / download_time if download_time > 0 else 0
    await tm.update(task_id, file_size=file_size)
    if thumbnail_url and tm.thumb_queue:
        await asyncio.sleep(THUMB_QUEUE_DELAY_SEC)
        await tm.update(
            task_id,
            status=TaskStatus.THUMBNAIL,
            progress=float(PROGRESS_THUMB_QUEUE),
            message=f"Done DL ({size_mb:.1f}MB) - Processing thumbnail...",
        )
        await tm.thumb_queue.put(
            {
                "video_path": video_path,
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
        await tm.update(
            task_id,
            status=TaskStatus.COMPLETED,
            progress=100.0,
            message=f"Done! {size_mb:.1f}MB ({speed_mbps:.1f}MB/s)",
            completed_at=utcnow_iso(),
        )
        logger.info("Completed: %s [no thumb]", filename)


async def _postprocess_and_finalize(
    task_id: str,
    filename: str,
    raw_path: Path,
    output_path: Path,
    thumbnail_url: Optional[str],
    cookie: Optional[str],
    referer: Optional[str],
    *,
    download_time: float,
    from_progressive: bool,
) -> None:
    outp = str(output_path)
    raw_s = str(raw_path)

    if from_progressive:
        file_size = os.path.getsize(outp)
        await tm.update(
            task_id,
            progress=float(PROGRESS_DIRECT_DL_CAP),
            file_size=file_size,
        )
        await _finalize_with_optional_thumbnail(
            task_id, filename, outp, file_size, download_time,
            thumbnail_url, cookie, referer,
        )
        return

    file_size = os.path.getsize(raw_s)
    size_mb = file_size / (1024 * 1024)

    if not raw_s.endswith(".mp4"):
        await tm.update(
            task_id,
            status=TaskStatus.MERGING,
            progress=float(PROGRESS_MERGE),
            message=f"Converting to MP4... ({size_mb:.1f}MB)",
        )
        logger.info("Remux TS→MP4: %s", os.path.basename(raw_s))

        if await _remux_ts_to_mp4(raw_s, output_path):
            os.remove(raw_s)
            file_size = os.path.getsize(outp)
            logger.info("Remux done: %s (%.1fMB)", filename, file_size / (1024 * 1024))
        else:
            logger.warning("Remux failed, keeping original: %s", os.path.basename(raw_s))
            if raw_s != outp:
                os.rename(raw_s, outp)
    else:
        await tm.update(
            task_id,
            status=TaskStatus.MERGING,
            progress=float(PROGRESS_NORMALIZE),
            message="Normalizing MP4...",
        )
        logger.info("Normalizing MP4: %s", filename)
        if await ffmpeg_normalize_container_to_mp4(outp):
            file_size = os.path.getsize(outp)
            logger.info("MP4 normalized OK (%.1fMB)", file_size / (1024 * 1024))
        else:
            logger.warning("MP4 normalize failed — thumbnail embed may fail")

    await _finalize_with_optional_thumbnail(
        task_id, filename, outp, file_size, download_time,
        thumbnail_url, cookie, referer,
    )


async def run_download(
    task_id: str,
    url: str,
    filename: str,
    thumbnail_url: Optional[str] = None,
    request_quality: Optional[str] = None,
    cookie: Optional[str] = None,
    referer: Optional[str] = None,
    resolved_ips: Optional[list[str]] = None,
) -> None:
    _ = request_quality
    async with tm.semaphore:
        wall_start = time.monotonic()
        try:
            # Disk space check before starting download
            disk_ok, free_bytes = check_disk_space()
            if not disk_ok:
                free_mb = free_bytes / (1024 * 1024)
                msg = f"Low disk space: {free_mb:.0f}MB free (need {cfg.MIN_FREE_DISK_MB}MB)"
                await tm.update(task_id, status=TaskStatus.ERROR, message=msg)
                logger.error("Disk space check failed: %s", msg)
                return
            filename = sanitize_filename_for_windows(filename)
            await tm.update(task_id, filename=filename, status=TaskStatus.DOWNLOADING, message="Starting...")
            output_path = cfg.OUTPUT_DIR / filename

            if is_direct_progressive_http_url(url):
                await tm.update(task_id, message="Downloading (HTTP)...")
                raw = await _run_progressive(
                    task_id, url, output_path, cookie, referer, resolved_ips=resolved_ips
                )
                download_time = time.monotonic() - wall_start
                if raw and raw.exists() and raw.stat().st_size >= MIN_VALID_FILE_BYTES:
                    # _run_progressive may have already set ERROR on httpx failures; re-check task
                    t = tm.get(task_id)
                    if t and t.status == TaskStatus.ERROR:
                        return
                    await _postprocess_and_finalize(
                        task_id,
                        filename,
                        raw,
                        output_path,
                        thumbnail_url,
                        cookie,
                        referer,
                        download_time=download_time,
                        from_progressive=True,
                    )
                else:
                    t = tm.get(task_id)
                    if t and t.status != TaskStatus.ERROR:
                        await tm.update(task_id, status=TaskStatus.ERROR, message="Direct download failed")
                    logger.error("Direct download failed: %s", filename)
                return

            raw_result, killed_stall = await _run_m3u8(
                task_id, url, filename, output_path, cookie, referer
            )
            download_time = time.monotonic() - wall_start

            if raw_result and raw_result.exists() and raw_result.stat().st_size > MIN_VALID_FILE_BYTES:
                await _postprocess_and_finalize(
                    task_id,
                    filename,
                    raw_result,
                    output_path,
                    thumbnail_url,
                    cookie,
                    referer,
                    download_time=download_time,
                    from_progressive=False,
                )
            else:
                if killed_stall:
                    await tm.update(
                        task_id,
                        status=TaskStatus.ERROR,
                        message=(
                            "Stalled (0 Bps). Set MATRIX_NEO_M3U8_MT=0 or increase "
                            "MATRIX_NEO_M3U8_STALL_SEC"
                        ),
                    )
                else:
                    await tm.update(task_id, status=TaskStatus.ERROR, message="Download failed")
                logger.error(
                    "Failed: %s (raw=%s stall=%s)",
                    filename,
                    raw_result,
                    killed_stall,
                )

        except asyncio.CancelledError:
            await tm.update(task_id, status=TaskStatus.ERROR, message="Cancelled")
            raise
        except Exception as e:
            await tm.update(task_id, status=TaskStatus.ERROR, message=str(e)[:MAX_ERROR_MSG_LEN])
            logger.exception("run_download: %s", e)
        finally:
            tm.active_downloads.pop(task_id, None)
