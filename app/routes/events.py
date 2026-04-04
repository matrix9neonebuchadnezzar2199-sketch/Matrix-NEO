"""Server-Sent Events for real-time task progress."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.state import tm

router = APIRouter(tags=["events"])


def _tasks_change_fingerprint() -> int:
    """Cheap change detection before full JSON serialization."""
    tasks = sorted(tm.all_tasks(), key=lambda t: t.task_id)
    return hash(
        tuple((t.task_id, t.status, round(t.progress, 2), t.message) for t in tasks)
    )


@router.get("/tasks/events")
async def task_events():
    async def generate():
        prev_fp: int | None = None
        try:
            while True:
                fp = _tasks_change_fingerprint()
                if fp != prev_fp:
                    current = json.dumps(
                        [t.to_api_dict() for t in sorted(tm.all_tasks(), key=lambda x: x.task_id)],
                        ensure_ascii=False,
                    )
                    yield f"data: {current}\n\n"
                    prev_fp = fp
                await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            return

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
