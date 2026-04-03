"""Central logging configuration."""

from __future__ import annotations

import logging
import sys

from app.config import LOG_LEVEL


def setup_logging() -> None:
    level = getattr(logging, LOG_LEVEL, logging.INFO)
    if not logging.root.handlers:
        logging.basicConfig(
            level=level,
            format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
            handlers=[logging.StreamHandler(sys.stdout)],
        )
    else:
        logging.getLogger().setLevel(level)
