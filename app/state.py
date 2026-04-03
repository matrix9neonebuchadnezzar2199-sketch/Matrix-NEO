"""Process-wide mutable state (in-memory task registry)."""

from __future__ import annotations

import asyncio
from typing import Any, Dict

import app.config as cfg

tasks: Dict[str, Dict[str, Any]] = {}
active_downloads: Dict[str, asyncio.Task] = {}
# cookie/referer のみ（API レスポンスには出さない）。タスク ID 単位で保持し GC/削除で破棄。
task_credentials: Dict[str, Dict[str, Any]] = {}

semaphore = asyncio.Semaphore(cfg.MAX_CONCURRENT)
thumb_queue: asyncio.Queue | None = None
