"""Simple in-memory sliding-window rate limiter (per client key)."""

from __future__ import annotations

import time


class RateLimiter:
    def __init__(self, max_requests: int = 30, window_sec: float = 60.0):
        self.max_requests = max_requests
        self.window_sec = window_sec
        self._hits: dict[str, list[float]] = {}

    def is_allowed(self, key: str) -> bool:
        now = time.monotonic()
        if key not in self._hits:
            self._hits[key] = [now]
            return True
        hits = self._hits[key]
        hits[:] = [t for t in hits if now - t < self.window_sec]
        if not hits:
            del self._hits[key]
            self._hits[key] = [now]
            return True
        if len(hits) >= self.max_requests:
            return False
        hits.append(now)
        return True
