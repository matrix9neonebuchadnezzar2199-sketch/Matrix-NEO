"""Server-Sent Events for real-time task progress.

Sends only changed tasks as individual ``event: task-update`` messages
instead of serializing the full task list on every tick.  A periodic
``event: heartbeat`` keeps the connection alive.
"""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.state import tm

router = APIRouter(tags=["events"])


def _task_snapshot() -> dict[str, tuple[str, float, str]]:
    """Cheap per-task fingerprint: (status, progress_rounded, message)."""
    return {
        t.task_id: (t.status.value, round(t.progress, 2), t.message)
        for t in tm.all_tasks()
    }


@router.get("/tasks/events")
async def task_events():
    async def generate():
        prev: dict[str, tuple[str, float, str]] = {}
        tick = 0
        try:
            while True:
                cur = _task_snapshot()

                # Detect changed or new tasks
                changed_ids: list[str] = []
                for tid, fp in cur.items():
                    if prev.get(tid) != fp:
                        changed_ids.append(tid)

                # Detect removed tasks
                removed_ids = [tid for tid in prev if tid not in cur]

                # Emit per-task updates (smaller payloads)
                for tid in changed_ids:
                    task = tm.get(tid)
                    if task is None:
                        continue
                    payload = json.dumps(task.to_api_dict(), ensure_ascii=False)
                    yield f"event: task-update\ndata: {payload}\n\n"

                for tid in removed_ids:
                    yield f"event: task-remove\ndata: {json.dumps({'task_id': tid})}\n\n"

                prev = cur

                # Heartbeat every ~15 seconds to keep connection alive
                tick += 1
                if tick % 15 == 0:
                    yield f"event: heartbeat\ndata: {{}}\n\n"

                await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            return

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
