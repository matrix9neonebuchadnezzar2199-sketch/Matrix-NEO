"""Simple in-memory sliding-window rate limiter (per client key)."""

from __future__ import annotations

import time


class RateLimiter:
    def __init__(
        self,
        max_requests: int = 30,
        window_sec: float = 60.0,
        *,
        max_keys: int = 4096,
    ):
        self.max_requests = max_requests
        self.window_sec = window_sec
        self._max_keys = max(1, max_keys)
        self._hits: dict[str, list[float]] = {}

    def _prune_stale_keys(self, now: float) -> None:
        """Drop keys whose last hit is outside the window (idle clients)."""
        cutoff = now - self.window_sec
        dead = [k for k, hits in self._hits.items() if not hits or max(hits) < cutoff]
        for k in dead:
            del self._hits[k]

    def _enforce_max_keys(self) -> None:
        """Prevent unbounded growth if many unique keys appear once."""
        while len(self._hits) > self._max_keys:
            oldest_k = min(
                self._hits.keys(),
                key=lambda k: max(self._hits[k]) if self._hits[k] else 0.0,
            )
            del self._hits[oldest_k]

    def is_allowed(self, key: str) -> bool:
        now = time.monotonic()
        self._prune_stale_keys(now)

        if key not in self._hits:
            self._hits[key] = [now]
            self._enforce_max_keys()
            return True
        hits = self._hits[key]
        hits[:] = [t for t in hits if now - t < self.window_sec]
        if not hits:
            del self._hits[key]
            self._hits[key] = [now]
            self._enforce_max_keys()
            return True
        if len(hits) >= self.max_requests:
            return False
        hits.append(now)
        self._enforce_max_keys()
        return True
