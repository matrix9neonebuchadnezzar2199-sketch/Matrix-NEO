"""In-flight download deduplication."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models import TaskState, TaskStatus
from app.state import tm


@pytest.fixture(autouse=True)
def _reset_tm() -> None:
    tm.reset()
    yield
    tm.reset()


def test_duplicate_download_returns_same_task_id(monkeypatch) -> None:
    started: list[str] = []

    async def fake_run(*_args, **_kwargs) -> None:
        started.append("x")
        await asyncio.Event().wait()

    def fake_validate(url: str, *, block_private_ips: bool):
        return url, ["192.0.2.1"]

    monkeypatch.setattr("app.services.download_service.run_download", fake_run)
    monkeypatch.setattr("app.routes.download.validate_http_url", fake_validate)

    with TestClient(app, raise_server_exceptions=False) as client:
        body = {
            "url": "https://cdn.example.com/master.m3u8",
            "filename": "test.mp4",
        }
        r1 = client.post("/download", json=body)
        r2 = client.post("/download", json=body)
    assert r1.status_code == 200
    assert r2.status_code == 200
    j1, j2 = r1.json(), r2.json()
    assert j1["task_id"] == j2["task_id"]
    assert j2.get("deduplicated") is True
    assert len(started) == 1


@pytest.mark.asyncio
async def test_unique_filenames_for_same_title() -> None:
    from app.utils.filename_allocate import unique_output_filename

    a = unique_output_filename("same.mp4", "aaaaaaaaaaaaaaaa")
    b = unique_output_filename("same.mp4", "bbbbbbbbbbbbbbbb")
    assert a != b
