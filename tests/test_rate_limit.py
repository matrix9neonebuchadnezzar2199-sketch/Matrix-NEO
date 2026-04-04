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


def test_rate_limiter_prunes_idle_keys_after_window():
    r = RateLimiter(max_requests=5, window_sec=0.08)
    assert r.is_allowed("idle_client") is True
    time.sleep(0.12)
    assert r.is_allowed("other_client") is True
    assert r.is_allowed("idle_client") is True
    assert r.is_allowed("idle_client") is True


def test_rate_limiter_max_keys_evicts_oldest():
    r = RateLimiter(max_requests=1, window_sec=600.0, max_keys=2)
    assert r.is_allowed("a") is True
    assert r.is_allowed("b") is True
    assert r.is_allowed("c") is True
    # Oldest client key was dropped; "a" counts as a new key again.
    assert r.is_allowed("a") is True
