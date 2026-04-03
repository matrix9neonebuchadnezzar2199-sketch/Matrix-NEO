"""Rate limiter."""

import time

from app.utils.rate_limit import RateLimiter


def test_rate_limiter_allows_within_cap():
    r = RateLimiter(max_requests=3, window_sec=60.0)
    assert r.is_allowed("a") is True
    assert r.is_allowed("a") is True
    assert r.is_allowed("a") is True
    assert r.is_allowed("a") is False


def test_rate_limiter_resets_after_window():
    r = RateLimiter(max_requests=1, window_sec=0.05)
    assert r.is_allowed("k") is True
    assert r.is_allowed("k") is False
    time.sleep(0.06)
    assert r.is_allowed("k") is True


def test_rate_limiter_keys_independent():
    r = RateLimiter(max_requests=1, window_sec=60.0)
    assert r.is_allowed("u1") is True
    assert r.is_allowed("u2") is True
