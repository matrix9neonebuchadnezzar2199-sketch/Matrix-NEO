"""Disk space check utility tests."""

from unittest.mock import patch

from app.utils.disk import check_disk_space


def test_disk_check_returns_tuple():
    ok, free = check_disk_space()
    assert isinstance(ok, bool)
    assert isinstance(free, int)


@patch("app.utils.disk.shutil.disk_usage")
def test_disk_check_low_space(mock_usage):
    mock_usage.return_value = type("Usage", (), {"free": 50 * 1024 * 1024})()
    ok, free = check_disk_space()
    assert not ok
    assert free == 50 * 1024 * 1024


@patch("app.utils.disk.shutil.disk_usage")
def test_disk_check_enough_space(mock_usage):
    mock_usage.return_value = type("Usage", (), {"free": 10 * 1024 * 1024 * 1024})()
    ok, free = check_disk_space()
    assert ok


@patch("app.utils.disk.shutil.disk_usage", side_effect=OSError("no device"))
def test_disk_check_os_error_is_optimistic(mock_usage):
    ok, free = check_disk_space()
    assert ok  # Optimistic when check fails
