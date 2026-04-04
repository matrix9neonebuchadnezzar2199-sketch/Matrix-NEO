"""Constants sanity tests."""

from app.constants import MAX_ERROR_MSG_LEN, MIN_FREE_DISK_BYTES


def test_max_error_msg_len_is_positive():
    assert MAX_ERROR_MSG_LEN > 0
    assert MAX_ERROR_MSG_LEN >= 50  # usable minimum


def test_min_free_disk_bytes_reasonable():
    assert MIN_FREE_DISK_BYTES >= 100 * 1024 * 1024  # at least 100 MB
