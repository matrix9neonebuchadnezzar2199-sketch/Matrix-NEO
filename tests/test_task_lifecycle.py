"""Task lifecycle: active_downloads cleanup, stop safety, atomic dedup."""

from __future__ import annotations

import asyncio
import threading
import time

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


def test_active_downloads_cleared_after_runner_finishes(monkeypatch) -> None:
    gate = threading.Event()

    async def fake_run(*_args, **_kwargs) -> None:
        while not gate.is_set():
            await asyncio.sleep(0.02)

    def fake_validate(url: str, *, block_private_ips: bool):
        return url, ["192.0.2.1"]

    monkeypatch.setattr("app.services.download_service.run_download", fake_run)
    monkeypatch.setattr("app.routes.download.validate_http_url", fake_validate)

    with TestClient(app, raise_server_exceptions=False) as client:
        r = client.post(
            "/download",
            json={"url": "https://cdn.example.com/v.mp4", "filename": "a.mp4"},
        )
        assert r.status_code == 200
        task_id = r.json()["task_id"]
        assert task_id in tm.active_downloads
        gate.set()
        for _ in range(100):
            if task_id not in tm.active_downloads:
                break
            time.sleep(0.05)
        assert task_id not in tm.active_downloads


def test_stop_does_not_delete_completed_output(tmp_path, monkeypatch) -> None:
    out_dir = tmp_path / "output"
    out_dir.mkdir()
    monkeypatch.setattr("app.config.OUTPUT_DIR", out_dir)

    tid = "done_task"
    fname = "finished.mp4"
    out_file = out_dir / fname
    out_file.write_bytes(b"x" * 2000)

    tm.tasks[tid] = TaskState(
        task_id=tid,
        url="http://example.com/a.m3u8",
        filename=fname,
        status=TaskStatus.COMPLETED,
    )

    with TestClient(app, raise_server_exceptions=False) as client:
        r = client.post(f"/task/{tid}/stop")
    assert r.status_code == 200
    assert r.json()["status"] == "already_finished"
    assert out_file.is_file()


@pytest.mark.asyncio
async def test_register_and_bind_in_flight_dedup() -> None:
    key = "http://example.com/x|"
    t1 = TaskState(
        task_id="aaa",
        url="http://example.com/x",
        filename="a.mp4",
        status=TaskStatus.DOWNLOADING,
    )
    await tm.register_and_bind_in_flight(key, t1)
    t2 = TaskState(
        task_id="bbb",
        url="http://example.com/x",
        filename="b.mp4",
        status=TaskStatus.QUEUED,
    )
    existing = await tm.register_and_bind_in_flight(key, t2)
    assert existing is t1
    assert tm.get("bbb") is None
