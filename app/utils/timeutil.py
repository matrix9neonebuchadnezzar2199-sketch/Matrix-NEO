"""Timezone-aware datetime helpers."""

from __future__ import annotations

from datetime import datetime, timezone


def utcnow_iso() -> str:
    """Return current UTC time as ISO-8601 string with offset."""
    return datetime.now(timezone.utc).isoformat()


def utcnow() -> datetime:
    """Return current UTC time (timezone-aware)."""
    return datetime.now(timezone.utc)
