"""FastAPI application factory and lifespan."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app import config as cfg
from app import state
from app.logging_setup import setup_logging
from app.routes import download, health, proxy, stop_resume, tasks_read, youtube
from app.services import http_client
from app.services import task_gc
from app.services.thumbnail_service import thumbnail_worker

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    await http_client.start_client()
    os.makedirs(cfg.OUTPUT_DIR, exist_ok=True)
    os.makedirs(cfg.TEMP_DIR, exist_ok=True)
    state.thumb_queue = asyncio.Queue()
    bg_tasks: list[asyncio.Task] = []
    bg_tasks.append(asyncio.create_task(thumbnail_worker()))
    bg_tasks.append(asyncio.create_task(task_gc.task_gc_worker()))

    _mux_ts_on = os.environ.get("MATRIX_NEO_M3U8_MUX_TS", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )
    logger.info(
        "MATRIX-NEO v%s — output=%s temp=%s max_concurrent=%s",
        __version__,
        cfg.OUTPUT_DIR,
        cfg.TEMP_DIR,
        cfg.MAX_CONCURRENT,
    )
    logger.info(
        "m3u8DL threads=%s retry=%s http_timeout=%ss mt=%s stall=%s max_speed=%s browser_headers=%s mux_ts=%s",
        cfg.MAX_THREADS,
        cfg.M3U8_DOWNLOAD_RETRY,
        cfg.M3U8_HTTP_TIMEOUT,
        cfg.M3U8_USE_MT,
        cfg.M3U8_STALL_SEC if cfg.M3U8_STALL_SEC > 0 else "off",
        cfg.M3U8_MAX_SPEED or "(none)",
        "on"
        if os.environ.get("MATRIX_NEO_M3U8_BROWSER_HEADERS", "1").strip().lower()
        not in ("0", "false", "no", "off")
        else "off",
        "on" if _mux_ts_on else "off",
    )
    logger.info("YouTube support: enabled")

    yield

    for t in bg_tasks:
        t.cancel()
    await asyncio.gather(*bg_tasks, return_exceptions=True)
    await http_client.stop_client()
    logger.info("MATRIX-NEO shutdown complete")


def create_app() -> FastAPI:
    app = FastAPI(title="MATRIX-NEO Server", lifespan=lifespan, version=__version__)

    # Local tool + Chrome extension: regex avoids wildcard credential issues with "*"
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"chrome-extension://.*|https?://(localhost|127\.0\.0\.1)(:\d+)?",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(tasks_read.router)
    app.include_router(download.router)
    app.include_router(youtube.router)
    app.include_router(proxy.router)
    app.include_router(stop_resume.router)

    return app


app = create_app()
