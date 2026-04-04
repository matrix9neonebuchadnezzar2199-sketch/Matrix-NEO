"""Server-Sent Events for real-time task progress."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.state import tm

router = APIRouter(tags=["events"])


@router.get("/tasks/events")
async def task_events():
    async def generate():
        prev_snapshot = ""
        try:
            while True:
                current = json.dumps(
                    [t.to_api_dict() for t in tm.all_tasks()],
                    ensure_ascii=False,
                )
                if current != prev_snapshot:
                    yield f"data: {current}\n\n"
                    prev_snapshot = current
                await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            return

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
