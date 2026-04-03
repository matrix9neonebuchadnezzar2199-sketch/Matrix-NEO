"""Unique task identifiers."""

from __future__ import annotations

import uuid


def new_task_id() -> str:
    return uuid.uuid4().hex
