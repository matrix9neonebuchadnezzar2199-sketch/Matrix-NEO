"""Subprocess helpers."""

from __future__ import annotations

from typing import Optional


def subprocess_exit_code(rc: Optional[int]) -> int:
    if rc is None:
        return -1
    if rc > 0x7FFFFFFF:
        return rc - 0x100000000
    return rc


def stderr_tail(data: Optional[bytes], limit: int = 1800) -> str:
    if not data:
        return ""
    try:
        s = data.decode("utf-8", errors="replace").strip()
    except Exception:
        s = str(data)
    if len(s) > limit:
        s = s[-limit:]
    return s
