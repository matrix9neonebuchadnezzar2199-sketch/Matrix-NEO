"""Strip secrets from task dicts returned to API clients."""

from __future__ import annotations

from typing import Any, Dict

_SENSITIVE = frozenset({"cookie", "referer"})


def sanitize_task(task: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in task.items() if k not in _SENSITIVE}


def sanitize_tasks_list(tasks: list[Dict[str, Any]]) -> list[Dict[str, Any]]:
    return [sanitize_task(t) for t in tasks]
