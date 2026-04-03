"""Process-wide mutable state (in-memory task registry)."""

from __future__ import annotations

import asyncio
from typing import Any, Dict

import app.config as cfg

tasks: Dict[str, Dict[str, Any]] = {}
active_downloads: Dict[str, asyncio.Task] = {}
stopped_tasks: Dict[str, Dict[str, Any]] = {}

semaphore = asyncio.Semaphore(cfg.MAX_CONCURRENT)
thumb_queue: asyncio.Queue | None = None
