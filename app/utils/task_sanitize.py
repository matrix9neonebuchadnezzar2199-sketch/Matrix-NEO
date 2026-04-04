"""Strip secrets from task dicts returned to API clients."""

from __future__ import annotations

from typing import Any, Dict

from app.models import TaskState

_SENSITIVE = frozenset({"cookie", "referer"})


def sanitize_task(task: Dict[str, Any] | TaskState) -> Dict[str, Any]:
    if isinstance(task, TaskState):
        return task.to_api_dict()
    return {k: v for k, v in task.items() if k not in _SENSITIVE}


def sanitize_tasks_list(tasks: list[Dict[str, Any] | TaskState]) -> list[Dict[str, Any]]:
    return [sanitize_task(t) for t in tasks]
