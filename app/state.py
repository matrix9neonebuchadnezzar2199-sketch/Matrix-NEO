"""Process-wide mutable state managed through TaskManager."""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from app import config as cfg
from app.models import TaskState, TaskStatus


class TaskManager:
    """Central access point for all task state mutations."""

    def __init__(self) -> None:
        self.tasks: dict[str, TaskState] = {}
        self.active_downloads: dict[str, asyncio.Task] = {}
        self.task_credentials: dict[str, dict[str, str | None]] = {}
        self.semaphore = asyncio.Semaphore(cfg.MAX_CONCURRENT)
        self.thumb_queue: Optional[asyncio.Queue] = None
        self._lock = asyncio.Lock()

    def get(self, task_id: str) -> Optional[TaskState]:
        return self.tasks.get(task_id)

    def exists(self, task_id: str) -> bool:
        return task_id in self.tasks

    def all_tasks(self) -> list[TaskState]:
        return list(self.tasks.values())

    def tasks_by_status(self, status: TaskStatus) -> list[TaskState]:
        return [t for t in self.tasks.values() if t.status == status]

    async def register(
        self, task: TaskState, credentials: Optional[dict[str, str | None]] = None
    ) -> None:
        async with self._lock:
            self.tasks[task.task_id] = task
            if credentials:
                self.task_credentials[task.task_id] = credentials

    async def update(self, task_id: str, **fields: Any) -> None:
        async with self._lock:
            t = self.tasks.get(task_id)
            if t is None:
                return
            for k, v in fields.items():
                setattr(t, k, v)

    async def remove(self, task_id: str) -> Optional[TaskState]:
        async with self._lock:
            self.task_credentials.pop(task_id, None)
            return self.tasks.pop(task_id, None)

    async def remove_many(self, task_ids: list[str]) -> int:
        async with self._lock:
            count = 0
            for tid in task_ids:
                if self.tasks.pop(tid, None) is not None:
                    self.task_credentials.pop(tid, None)
                    count += 1
            return count

    def reset(self) -> None:
        self.tasks.clear()
        self.active_downloads.clear()
        self.task_credentials.clear()


tm = TaskManager()
