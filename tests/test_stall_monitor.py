"""M3u8StallMonitor unit tests."""

from unittest.mock import patch

from app.services.download_service import M3u8StallMonitor


def test_no_stall_when_progress_moves():
    m = M3u8StallMonitor(stall_sec=5.0)
    assert m.feed("10/100 10.00% 5.00MB/s") is False
    assert m.feed("11/100 11.00% 5.00MB/s") is False


def test_no_stall_when_speed_nonzero():
    m = M3u8StallMonitor(stall_sec=5.0)
    assert m.feed("10/100 10.00% 1.23MB/s") is False


def test_stall_triggers_after_timeout():
    m = M3u8StallMonitor(stall_sec=2.0)
    with patch("app.services.download_service.time.monotonic", side_effect=[100.0, 102.0]):
        assert m.feed("10/100 10.00% 0.00Bps") is False
        assert m.feed("10/100 10.00% 0.00Bps") is True


def test_stall_resets_when_progress_advances():
    m = M3u8StallMonitor(stall_sec=2.0)
    assert m.feed("10/100 10.00% 0.00Bps") is False
    assert m.feed("11/100 11.00% 0.00Bps") is False
    assert m._zero_since is not None


def test_disabled_when_stall_sec_zero():
    m = M3u8StallMonitor(stall_sec=0)
    assert m.feed("10/100 10.00% 0.00Bps") is False


def test_merge_resets_stall():
    m = M3u8StallMonitor(stall_sec=2.0)
    m.feed("10/100 10.00% 0.00Bps")
    m.feed("Merge completed")
    assert m._zero_since is None


def test_no_match_returns_false():
    m = M3u8StallMonitor(stall_sec=5.0)
    assert m.feed("some random log line") is False
    assert m.feed("") is False
