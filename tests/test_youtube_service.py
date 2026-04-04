"""YouTube service helpers (cache)."""

from __future__ import annotations

import time

import pytest

from app.services.youtube_service import _LRUCache


def test_lru_ttl_expiry() -> None:
    c = _LRUCache(maxsize=10, ttl=0.01)
    c.put("a", {"x": 1})
    assert c.get("a") == {"x": 1}
    time.sleep(0.05)
    assert c.get("a") is None


def test_lru_evicts_oldest() -> None:
    c = _LRUCache(maxsize=2, ttl=300.0)
    c.put("a", {"n": 1})
    c.put("b", {"n": 2})
    c.put("c", {"n": 3})
    assert c.get("a") is None
    assert c.get("b") == {"n": 2}
    assert c.get("c") == {"n": 3}


def test_lru_get_moves_to_end() -> None:
    c = _LRUCache(maxsize=2, ttl=300.0)
    c.put("a", {"n": 1})
    c.put("b", {"n": 2})
    assert c.get("a") == {"n": 1}
    c.put("c", {"n": 3})
    assert c.get("b") is None
    assert c.get("a") == {"n": 1}
