from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
import asyncio
import os
import random
import time
import shutil
import re
import json
import aiohttp
import aiofiles
import httpx
from datetime import datetime
import sys
from pathlib import Path

def _neo_base_dir() -> Path:
    """PyInstaller: exe のあるフォルダ。開発時: このファイルのあるフォルダ。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _tool_path(stem: str) -> str:
    """tools/ に同梱された実行ファイルを優先。無ければ PATH の名前だけ渡す。"""
    tools = _neo_base_dir() / "tools"
    name = f"{stem}.exe" if sys.platform == "win32" else stem
    if sys.platform == "win32":
        for cand in (tools / name, tools / stem):
            if cand.is_file():
                return str(cand)
    else:
        p = tools / stem
        if p.is_file():
            return str(p)
    # zip 展開などで tools 直下でなくサブフォルダにある場合
    try:
        found = []
        for p in tools.rglob(name):
            s = str(p).replace("\\", "/")
            if "__MACOSX" in s or p.name.startswith("._"):
                continue
            found.append(p)
        if found:
            found.sort(key=lambda x: len(x.parts))
            return str(found[0])
    except OSError:
        pass
    return stem


BASE_DIR = _neo_base_dir()
OUTPUT_DIR = str(BASE_DIR / "output")
TEMP_DIR = str(BASE_DIR / "temp")
N_M3U8DL_RE = _tool_path("N_m3u8DL-RE")
YTDLP = _tool_path("yt-dlp")
FFMPEG = _tool_path("ffmpeg")

app = FastAPI(title="MATRIX-NEO Server")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

MAX_CONCURRENT = int(os.environ.get("MAX_CONCURRENT_DOWNLOADS", "10"))
# 既定は最大速度寄り。429 / Force Exit が出る場合は THREADS を下げるか MT をオフ、README 参照
MAX_THREADS = max(1, int(os.environ.get("MATRIX_NEO_M3U8_THREADS", "32")))
M3U8_DOWNLOAD_RETRY = max(1, int(os.environ.get("MATRIX_NEO_M3U8_RETRY", "50")))
M3U8_HTTP_TIMEOUT = int(os.environ.get("MATRIX_NEO_M3U8_HTTP_TIMEOUT", "120"))
# -mt: 動画+音声などを同時 DL（速い）。429 時は MATRIX_NEO_M3U8_MT=0
_m3u8_mt_env = os.environ.get("MATRIX_NEO_M3U8_MT", "1").strip().lower()
M3U8_USE_MT = _m3u8_mt_env not in ("0", "false", "no", "off", "")
# 例: 8M（429 対策で帯域を抑える）。空なら --max-speed 指定なし＝無制限
M3U8_MAX_SPEED = os.environ.get("MATRIX_NEO_M3U8_MAX_SPEED", "").strip()
# 同一セグメントが 0.00Bps のまま続く場合、N_m3u8DL が終わらないことがある → 打ち切り（秒）。0 で無効
M3U8_STALL_SEC = float(os.environ.get("MATRIX_NEO_M3U8_STALL_SEC", "120"))
# 上記で打ち切ったあと、初回が -mt ありなら 1 回だけ -mt なしで再試行
_m3u8_retry_no_mt = os.environ.get("MATRIX_NEO_M3U8_RETRY_NO_MT_ON_STALL", "1").strip().lower()
M3U8_RETRY_NO_MT_ON_STALL = _m3u8_retry_no_mt not in ("0", "false", "no", "off", "")


def _is_ascii_basename(path: str) -> bool:
    try:
        os.path.basename(path).encode("ascii")
        return True
    except UnicodeEncodeError:
        return False


def sanitize_filename_for_windows(name: str) -> str:
    """
    Windows ではファイル名の [ ] がディレクトリ走査でワイルドカード扱いになり、
    N_m3u8DL の最終リネーム（*.copy.mp4 等）が失敗して moov の無い壊れた MP4 になることがある。
    <>:"/\\|?* も NTFS で問題になりうるため除去・置換する。
    """
    if not name or not str(name).strip():
        return "video.mp4"
    name = str(name).strip()
    for c in '<>:"/\\|?*\x00-\x1f':
        name = name.replace(c, "_")
    name = name.replace("[", "（").replace("]", "）")
    name = name.strip(" .")
    if not name.lower().endswith(".mp4"):
        name = f"{name}.mp4"
    if name.lower() == ".mp4" or len(name) < 5:
        name = "video.mp4"
    return name


class M3u8StallMonitor:
    """N_m3u8DL の進捗行で a/b が進まず 0.00Bps が続く場合に True を返す（最終セグメント固着対策）。"""

    __slots__ = ("stall_sec", "_last_ab", "_zero_since")

    def __init__(self, stall_sec: float):
        self.stall_sec = float(stall_sec)
        self._last_ab: Optional[tuple] = None
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


async def _terminate_child_process(proc: asyncio.subprocess.Process) -> None:
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


def _m3u8_static_header_args() -> list:
    """CDN がダウンローダ既定 UA を弾く場合に効くことがある。無効: MATRIX_NEO_M3U8_BROWSER_HEADERS=0"""
    if os.environ.get("MATRIX_NEO_M3U8_BROWSER_HEADERS", "1").strip().lower() in ("0", "false", "no", "off"):
        return []
    ua = os.environ.get(
        "MATRIX_NEO_M3U8_USER_AGENT",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    )
    return [
        "--header", f"User-Agent: {ua}",
        "--header", "Accept: */*",
        "--header", "Accept-Language: ja,en-US;q=0.9,en;q=0.8",
    ]

tasks: Dict[str, Dict[str, Any]] = {}
active_downloads: Dict[str, asyncio.Task] = {}
semaphore = asyncio.Semaphore(MAX_CONCURRENT)
thumb_queue: asyncio.Queue = None

class DownloadRequest(BaseModel):
    url: str
    filename: Optional[str] = None
    format_type: Optional[str] = "mp4"
    quality: Optional[str] = None
    thumbnail_url: Optional[str] = None
    cookie: Optional[str] = None
    referer: Optional[str] = None

class YouTubeRequest(BaseModel):
    url: str
    filename: Optional[str] = None
    format_type: Optional[str] = "mp4"
    quality: Optional[str] = "1080"
    thumbnail: Optional[bool] = True


class ProxyImageRequest(BaseModel):
    """拡張 UI のサムネ表示用（Cookie/Referer 付きで取得）"""
    url: str
    cookie: Optional[str] = None
    referer: Optional[str] = None

@app.on_event("startup")
async def startup():
    global thumb_queue
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(TEMP_DIR, exist_ok=True)
    thumb_queue = asyncio.Queue()
    asyncio.create_task(thumbnail_worker())

    print(f"[SERVER] MATRIX-NEO v3.0.0 (HLS + YouTube, tools from ./tools)...")
    print(f"[SERVER] Output: {OUTPUT_DIR}")
    print(f"[SERVER] Temp: {TEMP_DIR}")
    print(f"[SERVER] Max concurrent: {MAX_CONCURRENT}")
    _mux_ts_on = os.environ.get("MATRIX_NEO_M3U8_MUX_TS", "1").strip().lower() not in (
        "0", "false", "no", "off",
    )
    print(
        f"[SERVER] m3u8DL: threads={MAX_THREADS} retry={M3U8_DOWNLOAD_RETRY} "
        f"http_timeout={M3U8_HTTP_TIMEOUT}s concurrent_mt={M3U8_USE_MT} "
        f"stall_sec={M3U8_STALL_SEC if M3U8_STALL_SEC > 0 else 'off'} "
        f"retry_no_mt_on_stall={'on' if M3U8_RETRY_NO_MT_ON_STALL else 'off'} "
        f"max_speed={M3U8_MAX_SPEED or '(none)'} browser_headers={'on' if _m3u8_static_header_args() else 'off'} "
        f"mux_ts={'on' if _mux_ts_on else 'off'}"
    )
    print(f"[SERVER] YouTube support: enabled")

async def thumbnail_worker():
    while True:
        try:
            job = await thumb_queue.get()
            video_path = job["video_path"]
            thumb_url = job["thumb_url"]
            task_id = job["task_id"]

            if task_id in tasks:
                tasks[task_id]["status"] = "thumbnail"
                tasks[task_id]["progress"] = 92
                tasks[task_id]["message"] = "Downloading thumbnail..."

            print(f"[THUMB-WORKER] Processing: {os.path.basename(video_path)}")
            thumb_path = os.path.join(TEMP_DIR, f"{task_id}_thumb.jpg")

            if await download_thumbnail(
                thumb_url,
                thumb_path,
                cookie=job.get("cookie"),
                referer=job.get("referer"),
            ):
                if not await normalize_thumbnail_to_jpeg_for_embed(thumb_path):
                    print(
                        f"[THUMB] WARN: thumbnail is not JPEG/PNG and conversion failed; "
                        f"embed may fail ({os.path.basename(thumb_path)})"
                    )
                if task_id in tasks:
                    tasks[task_id]["progress"] = 95
                    tasks[task_id]["message"] = "Embedding thumbnail..."

                start = datetime.now()
                if sys.platform == "win32" and not _is_ascii_basename(video_path):
                    success = await embed_thumbnail_via_ascii_workdir(
                        video_path, thumb_path, task_id
                    )
                else:
                    success = await embed_thumbnail_atomic(
                        video_path, thumb_path, task_id=task_id
                    )
                elapsed = (datetime.now() - start).total_seconds()

                if success:
                    print(f"[THUMB-WORKER] Done: {os.path.basename(video_path)} ({elapsed:.1f}s)")
                    if task_id in tasks:
                        tasks[task_id]["status"] = "completed"
                        tasks[task_id]["progress"] = 100
                        file_size = tasks[task_id].get("file_size", 0)
                        size_mb = file_size / (1024 * 1024) if file_size else 0
                        tasks[task_id]["message"] = f"Done! {size_mb:.1f}MB [+thumb]"
                else:
                    print(f"[THUMB-WORKER] Failed embed: {os.path.basename(video_path)}")
                    if task_id in tasks:
                        tasks[task_id]["status"] = "completed"
                        tasks[task_id]["progress"] = 100
                        tasks[task_id]["message"] = tasks[task_id].get("message", "Done!") + " [no thumb]"

                try:
                    if os.path.exists(thumb_path):
                        os.remove(thumb_path)
                except:
                    pass
            else:
                print(f"[THUMB-WORKER] Failed download thumbnail for: {os.path.basename(video_path)}")
                if task_id in tasks:
                    tasks[task_id]["status"] = "completed"
                    tasks[task_id]["progress"] = 100
                    tasks[task_id]["message"] = tasks[task_id].get("message", "Done!") + " [thumb failed]"

            thumb_queue.task_done()
        except Exception as e:
            print(f"[THUMB-WORKER] Error: {e}")

@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "version": "3.0.0",
        "product": "MATRIX-NEO",
        "threads": MAX_THREADS,
        "youtube": True,
    }


@app.get("/vpn-status")
async def vpn_status():
    """Check VPN status by getting external IP from container"""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            services = [
                "https://ipapi.co/json/",
                "https://ipinfo.io/json",
                "http://ip-api.com/json/"
            ]
            
            for service_url in services:
                try:
                    response = await client.get(service_url)
                    if response.status_code == 200:
                        data = response.json()
                        
                        ip = data.get("ip") or data.get("query") or "Unknown"
                        country = data.get("country_name") or data.get("country") or "Unknown"
                        country_code = data.get("country_code") or data.get("countryCode") or ""
                        city = data.get("city") or ""
                        org = data.get("org") or data.get("isp") or ""
                        
                        vpn_keywords = ["vpn", "nord", "express", "surfshark", "private", "proxy", "hosting", "server", "data center", "datacenter", "packethub", "m247", "datacamp", "ovh", "leaseweb", "zscaler", "cloudflare warp", "mullvad", "cyberghost", "pia", "proton"]
                        is_likely_vpn = any(kw in org.lower() for kw in vpn_keywords)
                        is_japan = country_code.upper() == "JP"
                        
                        return {
                            "success": True,
                            "ip": ip,
                            "country": country,
                            "country_code": country_code,
                            "city": city,
                            "org": org,
                            "is_vpn": is_likely_vpn,
                            "is_home_country": is_japan,
                            "warning": is_japan and not is_likely_vpn
                        }
                except Exception as e:
                    print(f"[VPN] Service {service_url} failed: {e}")
                    continue
            
            return {"success": False, "error": "All IP services failed"}
            
    except Exception as e:
        print(f"[VPN] Error checking status: {e}")
        return {"success": False, "error": str(e)}

@app.get("/tasks")
async def get_tasks():
    return {"tasks": list(tasks.values())}

@app.get("/status/{task_id}")
async def get_status(task_id: str):
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    return tasks[task_id]

@app.delete("/task/{task_id}")
async def delete_task(task_id: str):
    if task_id in active_downloads:
        active_downloads[task_id].cancel()
        del active_downloads[task_id]
    if task_id in tasks:
        del tasks[task_id]
        return {"status": "deleted"}
    raise HTTPException(status_code=404, detail="Task not found")


@app.get("/youtube/info")
async def youtube_info(url: str):
    """Get YouTube video info with available formats"""
    try:
        cmd = [
            YTDLP,
            "--dump-json",
            "--no-playlist",
            url
        ]
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        
        if process.returncode != 0:
            error_msg = stderr.decode("utf-8", errors="ignore")
            print(f"[YT] Error getting info: {error_msg}")
            raise HTTPException(status_code=400, detail="Failed to get video info")
        
        info = json.loads(stdout.decode("utf-8"))
        
        # Extract available video qualities
        video_qualities = set()
        audio_qualities = set()
        
        for fmt in info.get("formats", []):
            if fmt.get("vcodec") != "none" and fmt.get("height"):
                video_qualities.add(fmt["height"])
            if fmt.get("acodec") != "none" and fmt.get("abr"):
                audio_qualities.add(int(fmt["abr"]))
        
        return {
            "title": info.get("title", "Unknown"),
            "duration": info.get("duration", 0),
            "thumbnail": info.get("thumbnail", ""),
            "channel": info.get("channel", ""),
            "video_qualities": sorted(video_qualities, reverse=True),
            "audio_qualities": sorted(audio_qualities, reverse=True),
            "is_youtube": True
        }
    except HTTPException:
        raise
    except Exception as e:
        print(f"[YT] Error: {e}")
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/youtube/download")
async def youtube_download(request: YouTubeRequest):
    """Download YouTube video or audio"""
    task_id = datetime.now().strftime("%Y%m%d_%H%M%S_") + str(datetime.now().microsecond)
    
    # Get video info first for filename
    try:
        cmd = [YTDLP, "--dump-json", "--no-playlist", request.url]
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await process.communicate()
        info = json.loads(stdout.decode("utf-8"))
        title = info.get("title", "video")
        thumbnail_url = info.get("thumbnail", "")
    except:
        title = "video"
        thumbnail_url = ""
    
    # Clean filename
    filename = request.filename or title
    filename = re.sub(r'[<>:"/\\|?*]', '_', filename)[:80]
    
    if request.format_type == "mp3":
        filename += ".mp3"
    else:
        filename += ".mp4"
    
    tasks[task_id] = {
        "task_id": task_id,
        "url": request.url,
        "filename": filename,
        "status": "queued",
        "progress": 0,
        "message": "Queue...",
        "type": "youtube",
        "format": request.format_type,
        "created_at": datetime.now().isoformat()
    }
    
    task = asyncio.create_task(run_youtube_download(
        task_id, request.url, filename, request.format_type, 
        request.quality, thumbnail_url if request.thumbnail else None
    ))
    active_downloads[task_id] = task
    
    print(f"[YT] Queued: {filename} ({request.format_type}, {request.quality})")
    
    return {
        "task_id": task_id,
        "status": "queued",
        "filename": filename,
        "format": request.format_type
    }

async def run_youtube_download(task_id: str, url: str, filename: str, format_type: str, quality: str, thumbnail_url: Optional[str] = None):
    async with semaphore:
        try:
            tasks[task_id]["status"] = "downloading"
            tasks[task_id]["message"] = "Starting YouTube download..."
            
            output_path = os.path.join(OUTPUT_DIR, filename)
            
            if format_type == "mp3":
                # Audio only
                format_spec = f"bestaudio[abr<={quality}]/bestaudio/best"
                cmd = [
                    YTDLP,
                    "-f", format_spec,
                    "-x",
                    "--audio-format", "mp3",
                    "--audio-quality", "0",
                    "--embed-thumbnail",
                    "--add-metadata",
                    "-o", output_path.replace(".mp3", ".%(ext)s"),
                    "--no-playlist",
                    "--progress",
                    url
                ]
            else:
                # Video + Audio
                format_spec = f"bestvideo[height<={quality}]+bestaudio/best[height<={quality}]/best"
                cmd = [
                    YTDLP,
                    "-f", format_spec,
                    "--merge-output-format", "mp4",
                    "--embed-thumbnail",
                    "--add-metadata",
                    "-o", output_path,
                    "--no-playlist",
                    "--progress",
                    url
                ]
            
            print(f"[YT] Starting: {filename} ({format_type}, {quality})")
            start_time = datetime.now()
            
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT
            )
            
            buffer = ""
            while True:
                chunk = await process.stdout.read(1024)
                if not chunk:
                    break
                
                buffer += chunk.decode("utf-8", errors="ignore")
                lines = buffer.split("\n")
                buffer = lines[-1]  # Keep incomplete line
                
                for text in lines[:-1]:
                    text = text.strip()
                    if not text:
                        continue
                    
                    # yt-dlp progress: [download]  45.2% of 100.00MiB at 5.00MiB/s
                    progress_match = re.search(r"\[download\]\s+(\d+\.?\d*)%", text)
                    if progress_match:
                        progress = float(progress_match.group(1))
                        tasks[task_id]["progress"] = min(progress * 0.9, 90)
                    
                    speed_match = re.search(r"at\s+(\d+\.?\d*\s*[KMG]?i?B/s)", text)
                    size_match = re.search(r"of\s+~?(\d+\.?\d*\s*[KMG]?i?B)", text)
                    
                    if progress_match:
                        msg = f"{tasks[task_id]['progress']:.0f}%"
                        if size_match:
                            msg += f" of {size_match.group(1)}"
                        if speed_match:
                            msg += f" ({speed_match.group(1)})"
                        tasks[task_id]["message"] = msg
                    
                    if "Merging" in text or "muxing" in text.lower():
                        tasks[task_id]["progress"] = 90
                        tasks[task_id]["message"] = "Merging..."
                    
                    if "[ExtractAudio]" in text:
                        tasks[task_id]["progress"] = 85
                        tasks[task_id]["message"] = "Extracting audio..."
            
            await process.wait()
            
            download_time = (datetime.now() - start_time).total_seconds()
            
            # Check for output file (yt-dlp may change extension)
            actual_output = output_path
            if not os.path.exists(output_path):
                # Try to find the file
                base = os.path.splitext(output_path)[0]
                for ext in [".mp4", ".mp3", ".webm", ".mkv", ".m4a"]:
                    if os.path.exists(base + ext):
                        actual_output = base + ext
                        break
            
            if os.path.exists(actual_output):
                file_size = os.path.getsize(actual_output)
                size_mb = file_size / (1024 * 1024)
                speed_mbps = size_mb / download_time if download_time > 0 else 0
                
                tasks[task_id]["file_size"] = file_size
                tasks[task_id]["status"] = "completed"
                tasks[task_id]["progress"] = 100
                tasks[task_id]["message"] = f"Done! {size_mb:.1f}MB ({speed_mbps:.1f}MB/s)"
                
                print(f"[YT] Completed: {filename} ({size_mb:.1f}MB in {download_time:.1f}s)")
            else:
                tasks[task_id]["status"] = "error"
                tasks[task_id]["message"] = "Download failed"
                print(f"[YT] Failed: {filename}")
                
        except asyncio.CancelledError:
            tasks[task_id]["status"] = "error"
            tasks[task_id]["message"] = "Cancelled"
        except Exception as e:
            tasks[task_id]["status"] = "error"
            tasks[task_id]["message"] = str(e)[:50]
            print(f"[YT] Error: {e}")
        finally:
            if task_id in active_downloads:
                del active_downloads[task_id]

@app.post("/download")
async def download(request: DownloadRequest):
    task_id = datetime.now().strftime("%Y%m%d_%H%M%S_") + str(datetime.now().microsecond)

    filename = request.filename or f"video_{task_id}.mp4"
    if not filename.endswith(".mp4"):
        filename += ".mp4"
    filename = sanitize_filename_for_windows(filename)

    if request.thumbnail_url:
        print(f"[DL] Thumbnail URL received for: {filename[:50]}...")
    else:
        print(f"[DL] NO thumbnail URL for: {filename[:50]}...")

    tasks[task_id] = {
        "task_id": task_id,
        "url": request.url,
        "filename": filename,
        "thumbnail_url": request.thumbnail_url,
        "quality": request.quality,
        "cookie": request.cookie,
        "referer": request.referer,
        "status": "queued",
        "progress": 0,
        "message": "Queue...",
        "created_at": datetime.now().isoformat()
    }


    task = asyncio.create_task(run_download(
        task_id, request.url, filename, request.thumbnail_url, request.quality,
        request.cookie, request.referer
    ))

    active_downloads[task_id] = task

    return {"task_id": task_id, "status": "queued", "filename": filename}


async def fetch_thumbnail_http_bytes(
    url: str,
    cookie: Optional[str] = None,
    referer: Optional[str] = None,
) -> tuple[Optional[bytes], Optional[str]]:
    try:
        ua = os.environ.get(
            "MATRIX_NEO_M3U8_USER_AGENT",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        )
        headers: Dict[str, str] = {
            "User-Agent": ua,
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
        }
        if referer and referer.strip():
            headers["Referer"] = referer.strip()
        if cookie and cookie.strip():
            headers["Cookie"] = cookie.strip()
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=45), headers=headers) as response:
                if response.status == 200:
                    content = await response.read()
                    ct = response.headers.get("Content-Type", "image/jpeg")
                    if ";" in ct:
                        ct = ct.split(";")[0].strip()
                    return content, ct
                else:
                    print(f"[THUMB] Download failed, status: {response.status} url={url[:80]}...")
    except Exception as e:
        print(f"[THUMB] Download error: {e}")
    return None, None


def _thumbnail_bytes_look_like_jpeg_or_png(path: str) -> bool:
    """AtomicParsley は実体が JPEG/PNG であることのみ受け付ける（拡張子だけ .jpg では不可）。"""
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
    """
    WebP / AVIF / GIF 等は拡張子が .jpg でも AtomicParsley が拒否する。
    ffmpeg で 1 フレーム JPEG に正規化する（既に JPEG/PNG ならスキップ）。
    """
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
            _replace_or_move_overwrite(tmp, src_path)
            return True
        if err:
            print(f"[THUMB] normalize to JPEG: {_stderr_tail(err, 400)}")
    except Exception as e:
        print(f"[THUMB] normalize to JPEG error: {e}")
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
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        async with aiofiles.open(output_path, 'wb') as f:
            await f.write(content)
        return True
    except Exception as e:
        print(f"[THUMB] Save error: {e}")
    return False


@app.post("/proxy-image")
async def proxy_image(req: ProxyImageRequest):
    """拡張サイドパネル用: ブラウザの img 直リンクでは Cookie が付かず 403 になるためサーバー経由で返す"""
    parsed = urlparse(req.url.strip())
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(status_code=400, detail="Invalid URL")
    body, media_type = await fetch_thumbnail_http_bytes(req.url, req.cookie, req.referer)
    if not body:
        raise HTTPException(status_code=502, detail="Upstream image fetch failed")
    return Response(content=body, media_type=media_type or "image/jpeg")

def _subprocess_exit_code(rc: Optional[int]) -> int:
    """Windows で負の終了コードが 32bit 符号なし整数として見える場合がある。"""
    if rc is None:
        return -1
    if rc > 0x7FFFFFFF:
        return rc - 0x100000000
    return rc


def _replace_or_move_overwrite(src: str, dst: str) -> None:
    """同一ボリュームは os.replace、失敗時はコピー＋削除。"""
    try:
        os.replace(src, dst)
    except OSError:
        shutil.copy2(src, dst)
        try:
            os.remove(src)
        except OSError:
            pass


def _stderr_tail(data: Optional[bytes], limit: int = 1800) -> str:
    if not data:
        return ""
    try:
        s = data.decode("utf-8", errors="replace").strip()
    except Exception:
        s = str(data)
    if len(s) > limit:
        s = s[-limit:]
    return s


def _is_direct_progressive_http_url(url: str) -> bool:
    """HLS ではなくブラウザが直接取得する .mp4/.m4v/.webm 等（プログレッシブ）。"""
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


async def _download_http_progressive_file(
    task_id: str,
    url: str,
    dest_path: str,
    cookie: Optional[str],
    referer: Optional[str],
) -> bool:
    ua = os.environ.get(
        "MATRIX_NEO_M3U8_USER_AGENT",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    )
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
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            async with client.stream("GET", url, headers=headers) as response:
                if response.status_code < 200 or response.status_code >= 400:
                    print(f"[DL] direct HTTP status={response.status_code}")
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
                            tasks[task_id]["progress"] = min(88, int(downloaded * 88 / total))
                        else:
                            mb = downloaded // (1024 * 1024)
                            tasks[task_id]["progress"] = min(88, 5 + mb * 3)
                if downloaded < 1024 * 1024:
                    print(f"[DL] direct file too small: {downloaded} bytes")
                    try:
                        os.remove(tmp)
                    except OSError:
                        pass
                    return False
                os.replace(tmp, dest_path)
                return True
    except Exception as e:
        print(f"[DL] direct HTTP error: {e}")
        try:
            if os.path.isfile(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return False


async def embed_thumbnail_atomic(
    video_path: str, thumb_path: str, task_id: Optional[str] = None
) -> bool:
    try:
        if not os.path.exists(thumb_path) or not os.path.exists(video_path):
            return False
        # ffmpeg で埋め込み（-disposition:v:1 attached_pic 等、再生互換性が高い）
        if task_id:
            return await embed_thumbnail_ffmpeg_with_temp_out(video_path, thumb_path, task_id)
        return await embed_thumbnail_ffmpeg(video_path, thumb_path)
    except Exception as e:
        print(f"[THUMB] embed error: {e}")
        if task_id:
            return await embed_thumbnail_ffmpeg_with_temp_out(video_path, thumb_path, task_id)
        return await embed_thumbnail_ffmpeg(video_path, thumb_path)

async def ffmpeg_normalize_container_to_mp4(src_path: str) -> bool:
    """
    N_m3u8DL のマージ結果が moov 欠落・不正な MP4 になることがあり、
    後続の ffmpeg（サムネ埋め込み）が開けない。ストリームをコピーし直して
    標準的な MP4（faststart）にする。
    """
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
            print(f"[DL] normalize ({label}) spawn error: {e}")
            continue
        if proc.returncode == 0 and os.path.exists(tmp):
            sz = os.path.getsize(tmp)
            if sz > 1024 * 1024:
                try:
                    os.replace(tmp, src_path)
                    return True
                except OSError as e:
                    print(f"[DL] normalize replace failed: {e}")
            try:
                os.remove(tmp)
            except OSError:
                pass
        elif err:
            rc = _subprocess_exit_code(proc.returncode)
            print(f"[DL] normalize ({label}) exit={rc} {_stderr_tail(err, 500)}")
        elif os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    return False


async def embed_thumbnail_ffmpeg(video_path: str, thumb_path: str, temp_output: Optional[str] = None) -> bool:
    """HLS マージ MP4 に JPEG を埋め込む。全局 -c copy だけだとカバー用 2 本目映像のコピーが mux で失敗しやすい。"""
    out_path = temp_output if temp_output else (video_path + ".thumb.mp4")
    try:
        cmd = [
            FFMPEG, "-y",
            "-i", video_path,
            "-i", thumb_path,
            "-map", "0:v:0", "-map", "0:a:0", "-map", "1:0",
            "-c:v:0", "copy",
            "-c:a", "copy",
            "-c:v:1", "mjpeg",
            "-disposition:v:0", "default",
            "-disposition:v:1", "attached_pic",
            "-movflags", "+faststart",
            out_path,
        ]
        process = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, err = await process.communicate()
        if process.returncode == 0 and os.path.exists(out_path):
            _replace_or_move_overwrite(out_path, video_path)
            return True
        if err or out:
            rc = _subprocess_exit_code(process.returncode)
            print(f"[THUMB] ffmpeg exit={rc} {_stderr_tail(err or out)}")
        if os.path.exists(out_path):
            try:
                os.remove(out_path)
            except OSError:
                pass
    except Exception as e:
        print(f"[THUMB] ffmpeg error: {e}")
    return False


async def embed_thumbnail_ffmpeg_with_temp_out(
    video_path: str, thumb_path: str, task_id: str
) -> bool:
    """出力も TEMP 内 ASCII パスに限定（日本語パスで .thumb.mp4 側が壊れるのを避ける）。"""
    out_path = os.path.join(TEMP_DIR, f"{task_id}_thumb_out.mp4")
    return await embed_thumbnail_ffmpeg(video_path, thumb_path, temp_output=out_path)


async def embed_thumbnail_via_ascii_workdir(
    video_path: str, thumb_path: str, task_id: str
) -> bool:
    """
    Windows の ffmpeg/libavformat が非 ASCII を含む -i パスを誤解釈し
    moov atom not found となることがあるため、TEMP 配下の ASCII 名にコピーしてから埋め込み、
    成功時のみ元ファイルを置換する。
    """
    work_video = os.path.join(TEMP_DIR, f"{task_id}_embed.mp4")
    try:
        shutil.copy2(video_path, work_video)
    except OSError as e:
        print(f"[THUMB] copy to ASCII temp failed: {e}")
        return await embed_thumbnail_atomic(video_path, thumb_path, task_id=task_id)

    ok = await embed_thumbnail_atomic(work_video, thumb_path, task_id=task_id)
    if ok:
        try:
            _replace_or_move_overwrite(work_video, video_path)
        except OSError as e:
            print(f"[THUMB] replace result failed: {e}")
            return False
        return True

    try:
        if os.path.exists(work_video):
            os.remove(work_video)
    except OSError:
        pass
    return False

# ========== STOP/RESUME API (v2.2.0) ==========
stopped_tasks: Dict[str, Dict[str, Any]] = {}  # 停止したタスクを保持

@app.post("/task/{task_id}/stop")
async def stop_task(task_id: str):
    """タスクを停止し、再開用に情報を保持"""
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    
    task = tasks[task_id]
    
    # 進行中のダウンロードをキャンセル（finally で既に active_downloads から消えていることがある）
    if task_id in active_downloads:
        dl_task = active_downloads[task_id]
        dl_task.cancel()
        try:
            await dl_task
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
    active_downloads.pop(task_id, None)
    
    # 再開用に情報を保存
    stopped_tasks[task_id] = {
        "url": task.get("url"),
        "filename": task.get("filename"),
        "thumbnail_url": task.get("thumbnail_url"),
        "type": task.get("type", "hls"),
        "quality": task.get("quality"),
        "cookie": task.get("cookie"),
        "referer": task.get("referer"),
        "stopped_at": datetime.now().isoformat(),
        "original_task": task.copy()
    }
    
    # 一時ファイルを削除
    if task.get("output_path") and os.path.exists(task["output_path"]):
        try:
            os.remove(task["output_path"])
        except:
            pass
    
    # タスクを停止状態に更新
    tasks[task_id]["status"] = "stopped"
    tasks[task_id]["message"] = "停止しました"
    
    print(f"[STOP] Task stopped: {task_id}")
    return {"status": "stopped", "task_id": task_id, "can_resume": True}

@app.post("/task/{task_id}/resume")
async def resume_task(task_id: str):
    """停止したタスクを再開（再ダウンロード）"""
    # 停止タスクから情報を取得
    if task_id in stopped_tasks:
        info = stopped_tasks[task_id]
    elif task_id in tasks and tasks[task_id].get("status") == "stopped":
        info = {
            "url": tasks[task_id].get("url"),
            "filename": tasks[task_id].get("filename"),
            "thumbnail_url": tasks[task_id].get("thumbnail_url"),
            "type": tasks[task_id].get("type", "hls"),
            "quality": tasks[task_id].get("quality"),
            "cookie": tasks[task_id].get("cookie"),
            "referer": tasks[task_id].get("referer"),
        }
    else:
        raise HTTPException(status_code=404, detail="Stopped task not found")
    
    # 新しいタスクIDで再開
    new_task_id = datetime.now().strftime("%Y%m%d_%H%M%S_") + str(random.randint(100000, 999999))
    
    # タスクタイプに応じて再ダウンロード
    if info.get("type") == "youtube":
        tasks[new_task_id] = {
            "status": "queued",
            "progress": 0,
            "filename": info["filename"],
            "message": "再開中...",
            "url": info["url"],
            "type": "youtube",
            "quality": info.get("quality"),
            "thumbnail_url": info.get("thumbnail_url")
        }
        task = asyncio.create_task(run_youtube_download(
            new_task_id, info["url"], info["filename"], 
            "mp4", info.get("quality", "best"), info.get("thumbnail_url")
        ))
        active_downloads[new_task_id] = task
    else:
        tasks[new_task_id] = {
            "status": "queued",
            "progress": 0,
            "filename": info["filename"],
            "message": "再開中...",
            "url": info["url"],
            "type": info.get("type", "hls"),
            "thumbnail_url": info.get("thumbnail_url"),
            "cookie": info.get("cookie"),
            "referer": info.get("referer"),
        }
        task = asyncio.create_task(run_download(
            new_task_id, info["url"], info["filename"],
            info.get("thumbnail_url"), info.get("quality"),
            info.get("cookie"), info.get("referer")
        ))

        active_downloads[new_task_id] = task
    
    # 古いタスクを削除
    if task_id in tasks:
        del tasks[task_id]
    if task_id in stopped_tasks:
        del stopped_tasks[task_id]
    
    print(f"[RESUME] Task resumed: {task_id} -> {new_task_id}")
    return {"status": "resumed", "old_task_id": task_id, "new_task_id": new_task_id}

@app.post("/tasks/stop-all")
async def stop_all_tasks():
    """全ての進行中タスクを停止"""
    stopped_count = 0
    for task_id in list(active_downloads.keys()):
        try:
            await stop_task(task_id)
            stopped_count += 1
        except:
            pass
    
    print(f"[STOP-ALL] Stopped {stopped_count} tasks")
    return {"status": "ok", "stopped_count": stopped_count}

@app.delete("/tasks/clear-stopped")
async def clear_stopped_tasks():
    """停止したタスクをクリア"""
    cleared = []
    for task_id in list(tasks.keys()):
        if tasks[task_id].get("status") in ["stopped", "error", "completed"]:
            cleared.append(task_id)
            del tasks[task_id]
    
    stopped_tasks.clear()
    
    print(f"[CLEAR] Cleared {len(cleared)} tasks")
    return {"status": "ok", "cleared_count": len(cleared)}

@app.get("/tasks/stopped")
async def get_stopped_tasks():
    """停止したタスクの一覧を取得"""
    result = []
    for task_id, task in tasks.items():
        if task.get("status") == "stopped":
            result.append({"task_id": task_id, **task})
    return result
# ========== END STOP/RESUME API ==========


async def run_download(
    task_id: str,
    url: str,
    filename: str,
    thumbnail_url: Optional[str] = None,
    request_quality: Optional[str] = None,
    cookie: Optional[str] = None,
    referer: Optional[str] = None,
):
    async with semaphore:
        try:
            filename = sanitize_filename_for_windows(filename)
            if task_id in tasks:
                tasks[task_id]["filename"] = filename

            tasks[task_id]["status"] = "downloading"
            tasks[task_id]["message"] = "Starting..."

            output_path = os.path.join(OUTPUT_DIR, filename)
            base_name = filename.rsplit(".", 1)[0]

            if _is_direct_progressive_http_url(url):
                tasks[task_id]["message"] = "Downloading (HTTP)..."
                wall_start = datetime.now()
                ok = await _download_http_progressive_file(
                    task_id, url, output_path, cookie, referer
                )
                download_time = (datetime.now() - wall_start).total_seconds()
                if (
                    not ok
                    or not os.path.isfile(output_path)
                    or os.path.getsize(output_path) < 1024 * 1024
                ):
                    tasks[task_id]["status"] = "error"
                    tasks[task_id]["message"] = "Direct download failed"
                    print(f"[DL] Direct download failed: {filename}")
                    return
                file_size = os.path.getsize(output_path)
                size_mb = file_size / (1024 * 1024)
                speed_mbps = size_mb / download_time if download_time > 0 else 0
                tasks[task_id]["progress"] = 88
                tasks[task_id]["file_size"] = file_size
                print(f"[DL] Direct HTTP OK: {filename} ({size_mb:.1f}MB)")
                if thumbnail_url and thumb_queue:
                    await asyncio.sleep(0.75)
                    tasks[task_id]["status"] = "thumbnail"
                    tasks[task_id]["progress"] = 90
                    tasks[task_id]["message"] = (
                        f"Done DL ({size_mb:.1f}MB) - Processing thumbnail..."
                    )
                    await thumb_queue.put(
                        {
                            "video_path": output_path,
                            "thumb_url": thumbnail_url,
                            "task_id": task_id,
                            "cookie": cookie,
                            "referer": referer,
                        }
                    )
                    print(
                        f"[DL] Completed: {filename} ({size_mb:.1f} MB in {download_time:.1f}s, "
                        f"{speed_mbps:.1f} MB/s)"
                    )
                    print(f"[DL] Thumbnail queued")
                else:
                    tasks[task_id]["status"] = "completed"
                    tasks[task_id]["progress"] = 100
                    tasks[task_id]["message"] = (
                        f"Done! {size_mb:.1f}MB ({speed_mbps:.1f}MB/s)"
                    )
                    print(
                        f"[DL] Completed: {filename} ({size_mb:.1f} MB in {download_time:.1f}s, "
                        f"{speed_mbps:.1f} MB/s) [no thumb]"
                    )
                return

            # 日本語ファイル名は N_m3u8DL 内部で化け、実際の .ts/.mp4 と base_name が一致しない。
            # ASCII のみの save-name にし、完了後に希望ファイル名へ remux/rename する。
            save_name_dl = "mneo_" + re.sub(r"[^0-9A-Za-z_]+", "_", task_id).strip("_")

            # N_m3u8DL が ffmpeg で直接 MP4 マージすると moov 欠落の壊れたファイルになることがある。
            # TS にまとめたあと、下の TS→MP4 remux で正規の MP4 にする（サムネ・再生の互換用）。
            _mux_ts = os.environ.get("MATRIX_NEO_M3U8_MUX_TS", "1").strip().lower() not in (
                "0", "false", "no", "off",
            )

            def _build_m3u8_cmd(use_mt: bool) -> list:
                c = [
                    N_M3U8DL_RE, url,
                    "--save-dir", OUTPUT_DIR,
                    "--save-name", save_name_dl,
                    "--auto-select",
                    "--thread-count", str(MAX_THREADS),
                    "--download-retry-count", str(M3U8_DOWNLOAD_RETRY),
                    "--http-request-timeout", str(M3U8_HTTP_TIMEOUT),
                    "--tmp-dir", TEMP_DIR,
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
                if M3U8_MAX_SPEED:
                    c.extend(["--max-speed", M3U8_MAX_SPEED])
                if FFMPEG and FFMPEG != "ffmpeg" and os.path.isfile(FFMPEG):
                    c.extend(["--ffmpeg-binary-path", FFMPEG])
                c.extend(_m3u8_static_header_args())
                if cookie and cookie.strip():
                    c.extend(["--header", f"Cookie: {cookie.strip()}"])
                if referer and referer.strip():
                    c.extend(["--header", f"Referer: {referer.strip()}"])
                return c

            attempt_mt = M3U8_USE_MT
            killed_stall = False
            process: Optional[asyncio.subprocess.Process] = None
            wall_start = datetime.now()

            for attempt_round in range(2):
                cmd = _build_m3u8_cmd(attempt_mt)
                stall_monitor = M3u8StallMonitor(M3U8_STALL_SEC)

                if attempt_round == 0:
                    print(
                        f"[DL] Starting: {filename} [save_name={save_name_dl} threads={MAX_THREADS} "
                        f"retry={M3U8_DOWNLOAD_RETRY} mt={attempt_mt} stall={M3U8_STALL_SEC}s "
                        f"max_speed={M3U8_MAX_SPEED or '-'}]"
                    )
                else:
                    print(f"[DL] Retry (stall recovery): mt={attempt_mt}")
                    tasks[task_id]["message"] = "Retrying download without -mt..."

                process = await asyncio.create_subprocess_exec(
                    *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
                )

                is_merging = False
                killed_stall = False
                async for line in process.stdout:
                    text = line.decode("utf-8", errors="ignore").strip()
                    if not text:
                        continue
                    print(f"[N_m3u8DL] {text}")

                    if stall_monitor.feed(text):
                        killed_stall = True
                        print(
                            f"[N_m3u8DL] No progress at 0.00Bps for {M3U8_STALL_SEC:.0f}s — terminating "
                            f"(set MATRIX_NEO_M3U8_MT=0 or adjust MATRIX_NEO_M3U8_STALL_SEC)"
                        )
                        tasks[task_id]["message"] = "Stalled — stopping downloader..."
                        await _terminate_child_process(process)
                        break

                    if "mux" in text.lower() or "merge" in text.lower() or "muxing" in text.lower():
                        if not is_merging:
                            is_merging = True
                            tasks[task_id]["status"] = "merging"
                            tasks[task_id]["progress"] = 85
                            tasks[task_id]["message"] = "Merging segments..."
                            print(f"[DL] Merging: {filename}")
                        continue

                    progress_match = re.search(r"(\d+\.?\d*)%", text)
                    if progress_match and not is_merging:
                        raw_progress = float(progress_match.group(1))
                        progress = min(raw_progress * 0.8, 80)
                        tasks[task_id]["progress"] = progress

                    speed_match = re.search(r"(\d+\.?\d*\s*[KMG]?B/s)", text)
                    size_match = re.search(r"(\d+\.?\d*\s*[KMG]?B)\s*/", text)

                    if speed_match and not is_merging:
                        msg = f"{tasks[task_id]['progress']:.0f}%"
                        if size_match:
                            msg += f" - {size_match.group(1)}"
                        msg += f" ({speed_match.group(1)})"
                        tasks[task_id]["message"] = msg

                await process.wait()
                print(f"[N_m3u8DL] Exit code: {process.returncode}")

                if (
                    killed_stall
                    and attempt_round == 0
                    and M3U8_USE_MT
                    and M3U8_RETRY_NO_MT_ON_STALL
                    and attempt_mt
                ):
                    attempt_mt = False
                    continue
                break

            download_time = (datetime.now() - wall_start).total_seconds()

            # binary-merge は .ts、直マージは .mp4 等。save_name_dl（ASCII）を最優先で拾う。
            # output_path に古い失敗残骸（数百バイト）があると誤検出するためサイズで弾く。

            def _pick_downloaded_media(prefix: str) -> Optional[str]:
                for ext in (".ts", ".mp4", ".m4s", ".mkv"):
                    p = os.path.join(OUTPUT_DIR, prefix + ext)
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

                # TS→MP4 remux（raw_outputがMP4でない場合）
                if not raw_output.endswith('.mp4'):
                    tasks[task_id]["status"] = "merging"
                    tasks[task_id]["progress"] = 85
                    tasks[task_id]["message"] = f"Converting to MP4... ({size_mb:.1f}MB)"
                    print(f"[DL] Remuxing TS→MP4: {os.path.basename(raw_output)}")

                    remux_cmd = [
                        FFMPEG, "-y",
                        "-fflags", "+genpts",
                        "-i", raw_output,
                        "-map", "0:v:0", "-map", "0:a:0",
                        "-c", "copy",
                        "-bsf:v", "h264_mp4toannexb,h264_redundant_pps",
                        "-movflags", "+faststart",
                        output_path
                    ]
                    remux_proc = await asyncio.create_subprocess_exec(
                        *remux_cmd,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.STDOUT
                    )
                    await remux_proc.wait()

                    if os.path.exists(output_path) and os.path.getsize(output_path) > 1024 * 1024:
                        os.remove(raw_output)
                        file_size = os.path.getsize(output_path)
                        size_mb = file_size / (1024 * 1024)
                        print(f"[DL] Remux done: {filename} ({size_mb:.1f}MB)")
                    else:
                        # remux失敗時はTSをそのままリネーム
                        print(f"[DL] Remux failed, keeping original: {os.path.basename(raw_output)}")
                        if raw_output != output_path:
                            os.rename(raw_output, output_path)
                else:
                    # 既に .mp4 だが N_m3u8DL マージ結果が moov 不正なことがあり、ffmpeg が開けない
                    tasks[task_id]["status"] = "merging"
                    tasks[task_id]["progress"] = 88
                    tasks[task_id]["message"] = "Normalizing MP4..."
                    print(f"[DL] Normalizing MP4 (fix container): {filename}")
                    if await ffmpeg_normalize_container_to_mp4(output_path):
                        file_size = os.path.getsize(output_path)
                        size_mb = file_size / (1024 * 1024)
                        print(f"[DL] MP4 normalized OK ({size_mb:.1f}MB)")
                    else:
                        print(f"[DL] WARN: MP4 normalize failed — thumbnail embed may fail")

                tasks[task_id]["file_size"] = file_size

                if thumbnail_url and thumb_queue:
                    # マージ直後はファイルハンドル解放・ディスク flush の遅れで moov 読み損ねることがある
                    await asyncio.sleep(0.75)
                    tasks[task_id]["status"] = "thumbnail"
                    tasks[task_id]["progress"] = 90
                    tasks[task_id]["message"] = f"Done DL ({size_mb:.1f}MB) - Processing thumbnail..."

                    await thumb_queue.put({
                        "video_path": output_path,
                        "thumb_url": thumbnail_url,
                        "task_id": task_id,
                        "cookie": cookie,
                        "referer": referer,
                    })
                    print(f"[DL] Completed: {filename} ({size_mb:.1f} MB in {download_time:.1f}s, {speed_mbps:.1f} MB/s)")
                    print(f"[DL] Thumbnail queued")
                else:
                    tasks[task_id]["status"] = "completed"
                    tasks[task_id]["progress"] = 100
                    msg = f"Done! {size_mb:.1f}MB ({speed_mbps:.1f}MB/s)"
                    tasks[task_id]["message"] = msg
                    print(f"[DL] Completed: {filename} ({size_mb:.1f} MB in {download_time:.1f}s, {speed_mbps:.1f} MB/s) [no thumb]")
            else:
                tasks[task_id]["status"] = "error"
                if killed_stall:
                    tasks[task_id]["message"] = (
                        "Stalled (0 Bps). Set MATRIX_NEO_M3U8_MT=0 or increase MATRIX_NEO_M3U8_STALL_SEC"
                    )
                else:
                    tasks[task_id]["message"] = "Download failed"
                print(f"[DL] Failed: {filename} (file_exists={file_exists}, size={file_size}, stall_kill={killed_stall})")

        except asyncio.CancelledError:
            tasks[task_id]["status"] = "error"
            tasks[task_id]["message"] = "Cancelled"
        except Exception as e:
            tasks[task_id]["status"] = "error"
            tasks[task_id]["message"] = str(e)[:50]
            print(f"[DL] Error: {e}")
        finally:
            if task_id in active_downloads:
                del active_downloads[task_id]

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=6850)
