"""Periodic cleanup of completed/error tasks from memory."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from app import config as cfg
from app.models import TaskStatus
from app.state import tm
from app.utils.timeutil import utcnow

logger = logging.getLogger(__name__)


async def task_gc_worker() -> None:
    ttl = timedelta(hours=cfg.TASK_TTL_HOURS)
    interval = max(30.0, cfg.TASK_GC_INTERVAL_SEC)
    while True:
        try:
            await asyncio.sleep(interval)
            now = utcnow()
            expired: list[str] = []
            for tid, t in list(tm.tasks.items()):
                st = t.status
                if st in (TaskStatus.COMPLETED, TaskStatus.ERROR):
                    ca = t.completed_at
                elif st == TaskStatus.STOPPED:
                    ca = t.stopped_at
                else:
                    continue
                if not ca:
                    continue
                try:
                    ts = datetime.fromisoformat(ca)
                    # Make naive datetimes comparable with aware ones
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                except ValueError:
                    continue
                if now - ts > ttl:
                    expired.append(tid)
            if expired:
                n = await tm.remove_many(expired)
                for tid in expired:
                    logger.debug("task GC removed %s", tid)
                logger.info("task GC removed %s completed/error task(s)", n)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("task_gc_worker")
