"""Timezone utility tests."""

from datetime import timezone

from app.utils.timeutil import utcnow, utcnow_iso


def test_utcnow_is_timezone_aware():
    dt = utcnow()
    assert dt.tzinfo is not None
    assert dt.tzinfo == timezone.utc


def test_utcnow_iso_contains_offset():
    iso = utcnow_iso()
    # UTC offset should be present: +00:00 or Z
    assert "+" in iso or "Z" in iso


def test_utcnow_iso_is_valid_isoformat():
    from datetime import datetime
    iso = utcnow_iso()
    parsed = datetime.fromisoformat(iso)
    assert parsed.tzinfo is not None
