"""Periodic cleanup of completed/error tasks from memory."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from app import config as cfg
from app import state

logger = logging.getLogger(__name__)


async def task_gc_worker() -> None:
    ttl = timedelta(hours=cfg.TASK_TTL_HOURS)
    interval = max(30.0, cfg.TASK_GC_INTERVAL_SEC)
    while True:
        try:
            await asyncio.sleep(interval)
            now = datetime.now()
            expired: list[str] = []
            for tid, t in list(state.tasks.items()):
                st = t.get("status")
                if st in ("completed", "error"):
                    ca = t.get("completed_at")
                elif st == "stopped":
                    ca = t.get("stopped_at")
                else:
                    continue
                if not ca:
                    continue
                try:
                    ts = datetime.fromisoformat(ca)
                except ValueError:
                    continue
                if now - ts > ttl:
                    expired.append(tid)
            for tid in expired:
                state.tasks.pop(tid, None)
                state.task_credentials.pop(tid, None)
                logger.debug("task GC removed %s", tid)
            if expired:
                logger.info("task GC removed %s completed/error task(s)", len(expired))
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("task_gc_worker")
