from app.models import TaskState, TaskStatus
from app.utils.task_sanitize import sanitize_task


def test_sanitize_strips_secrets():
    d = {"task_id": "x", "cookie": "secret", "referer": "http://z", "progress": 1}
    s = sanitize_task(d)
    assert "cookie" not in s
    assert "referer" not in s
    assert s["task_id"] == "x"


def test_sanitize_accepts_task_state():
    t = TaskState(task_id="t1", url="http://u", filename="a.mp4", status=TaskStatus.QUEUED)
    s = sanitize_task(t)
    assert s["task_id"] == "t1"
    assert s["status"] == "queued"
