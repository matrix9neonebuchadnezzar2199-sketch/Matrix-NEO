from app.utils.task_sanitize import sanitize_task


def test_sanitize_strips_secrets():
    d = {"task_id": "x", "cookie": "secret", "referer": "http://z", "progress": 1}
    s = sanitize_task(d)
    assert "cookie" not in s
    assert "referer" not in s
    assert s["task_id"] == "x"
