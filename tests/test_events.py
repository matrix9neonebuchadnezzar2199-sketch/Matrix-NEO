"""SSE /tasks/events route registration."""

from __future__ import annotations


def test_tasks_events_route_registered() -> None:
    from app.main import app

    paths = [getattr(r, "path", "") for r in app.routes]
    assert "/tasks/events" in paths
