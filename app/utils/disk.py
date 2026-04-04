"""Disk space utilities."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from app import config as cfg

logger = logging.getLogger(__name__)


def check_disk_space(target_dir: Path | None = None) -> tuple[bool, int]:
    """Check if enough free disk space exists.

    Returns (ok, free_bytes).  *ok* is False when free space is below
    ``cfg.MIN_FREE_DISK_MB`` megabytes.
    """
    d = target_dir or cfg.OUTPUT_DIR
    try:
        usage = shutil.disk_usage(str(d))
        free = usage.free
        threshold = cfg.MIN_FREE_DISK_MB * 1024 * 1024
        return free >= threshold, free
    except OSError as e:
        logger.warning("disk_usage check failed: %s", e)
        # Optimistic: allow download when we cannot check
        return True, 0
