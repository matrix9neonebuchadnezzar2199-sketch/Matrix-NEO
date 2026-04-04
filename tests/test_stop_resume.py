from app.models import TaskState, TaskStatus
from app.main import app
from app.state import tm
from fastapi.testclient import TestClient


def test_no_stopped_tasks_dict():
    assert not hasattr(tm, "stopped_tasks")


def test_resume_hls_revalidates_url_and_passes_resolved_ips(monkeypatch):
    captured: dict = {}

    async def fake_run(*args, **kwargs):
        captured["kwargs"] = kwargs

    def fake_validate(url, *, block_private_ips):
        return url, ["192.0.2.1"]

    monkeypatch.setattr("app.routes.stop_resume.run_download", fake_run)
    monkeypatch.setattr("app.routes.stop_resume.validate_http_url", fake_validate)

    tid = "t_resume_hls"
    tm.tasks[tid] = TaskState(
        task_id=tid,
        url="http://example.com/a.m3u8",
        filename="x.mp4",
        status=TaskStatus.STOPPED,
        type="hls",
    )

    with TestClient(app, raise_server_exceptions=False) as client:
        r = client.post(f"/task/{tid}/resume")
    assert r.status_code == 200
    assert captured.get("kwargs", {}).get("resolved_ips") == ["192.0.2.1"]
