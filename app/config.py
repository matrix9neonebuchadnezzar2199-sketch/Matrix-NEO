"""Environment-driven configuration (single source of truth)."""

from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path

from dotenv import load_dotenv

# Load .env before reading os.environ (see .env.example)
load_dotenv()


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _bool_env(key: str, default: bool = True) -> bool:
    raw = os.environ.get(key, "").strip().lower()
    if not raw:
        return default
    return raw not in ("0", "false", "no", "off")


BASE_DIR = _base_dir()
OUTPUT_DIR: Path = BASE_DIR / "output"
TEMP_DIR: Path = BASE_DIR / "temp"

PORT = int(os.environ.get("MATRIX_NEO_PORT", "6850"))

MAX_CONCURRENT = int(os.environ.get("MAX_CONCURRENT_DOWNLOADS", "10"))
MAX_THREADS = max(1, int(os.environ.get("MATRIX_NEO_M3U8_THREADS", "32")))
M3U8_DOWNLOAD_RETRY = max(1, int(os.environ.get("MATRIX_NEO_M3U8_RETRY", "50")))
M3U8_HTTP_TIMEOUT = int(os.environ.get("MATRIX_NEO_M3U8_HTTP_TIMEOUT", "120"))

M3U8_USE_MT = _bool_env("MATRIX_NEO_M3U8_MT", default=True)

M3U8_MAX_SPEED = os.environ.get("MATRIX_NEO_M3U8_MAX_SPEED", "").strip()
M3U8_STALL_SEC = float(os.environ.get("MATRIX_NEO_M3U8_STALL_SEC", "120"))

M3U8_RETRY_NO_MT_ON_STALL = _bool_env("MATRIX_NEO_M3U8_RETRY_NO_MT_ON_STALL", default=True)

# SSRF: enable to block private/link-local URLs (may break LAN / localhost streams)
BLOCK_PRIVATE_IPS = _bool_env("MATRIX_NEO_BLOCK_PRIVATE_IPS", default=False)

M3U8_BROWSER_HEADERS: bool = _bool_env("MATRIX_NEO_M3U8_BROWSER_HEADERS", default=True)
M3U8_MUX_TS: bool = _bool_env("MATRIX_NEO_M3U8_MUX_TS", default=True)

# Task GC
TASK_TTL_HOURS = float(os.environ.get("MATRIX_NEO_TASK_TTL_HOURS", "24"))
TASK_GC_INTERVAL_SEC = float(os.environ.get("MATRIX_NEO_TASK_GC_INTERVAL_SEC", "300"))

LOG_LEVEL = os.environ.get("MATRIX_NEO_LOG_LEVEL", "INFO").upper()

# Authentication: Bearer token (empty = disabled for backward compat)
AUTH_TOKEN: str = os.environ.get("MATRIX_NEO_AUTH_TOKEN", "").strip()
AUTH_TOKEN_AUTO: bool = _bool_env("MATRIX_NEO_AUTH_TOKEN_AUTO", default=False)

# If AUTO is enabled and no token is set, generate one on startup
if AUTH_TOKEN_AUTO and not AUTH_TOKEN:
    AUTH_TOKEN = secrets.token_urlsafe(32)

# Thumbnail worker concurrency
THUMB_WORKERS: int = max(1, int(os.environ.get("MATRIX_NEO_THUMB_WORKERS", "2")))

DEFAULT_UA = os.environ.get(
    "MATRIX_NEO_M3U8_USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
)

# /proxy-image: sliding window per client
PROXY_IMAGE_RATE_LIMIT = int(os.environ.get("MATRIX_NEO_PROXY_IMAGE_RATE_LIMIT", "30"))
PROXY_IMAGE_RATE_WINDOW_SEC = float(
    os.environ.get("MATRIX_NEO_PROXY_IMAGE_RATE_WINDOW_SEC", "60")
)

# Disk space check
MIN_FREE_DISK_MB: int = max(100, int(os.environ.get("MATRIX_NEO_MIN_FREE_DISK_MB", "500")))

_default_vpn_kw = (
    "vpn,nord,express,surfshark,private,proxy,hosting,server,data center,datacenter,"
    "packethub,m247,datacamp,ovh,leaseweb,zscaler,cloudflare warp,mullvad,cyberghost,pia,proton"
)
VPN_KEYWORDS = [
    kw.strip().lower()
    for kw in os.environ.get("MATRIX_NEO_VPN_KEYWORDS", _default_vpn_kw).split(",")
    if kw.strip()
]
