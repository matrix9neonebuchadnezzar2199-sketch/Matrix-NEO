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


def test_task_state_to_api_dict_includes_null_optional_fields():
    """Match legacy dict responses: optional keys present with null."""
    t = TaskState(
        task_id="n1",
        url="http://u",
        filename="a.mp4",
        file_size=None,
        completed_at=None,
    )
    d = t.to_api_dict()
    assert "file_size" in d and d["file_size"] is None
    assert "completed_at" in d and d["completed_at"] is None
