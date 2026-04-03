import app.state as st


def test_no_stopped_tasks_dict():
    assert not hasattr(st, "stopped_tasks")
