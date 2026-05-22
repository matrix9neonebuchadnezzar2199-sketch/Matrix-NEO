"""Unique output filename allocation."""

from app.utils.filename_allocate import unique_output_filename


def test_appends_task_suffix() -> None:
    tid = "abcdef1234567890"
    name = unique_output_filename("My Video.mp4", tid)
    assert name.endswith("_abcdef12.mp4")
    assert "My Video" in name


def test_mp3_extension() -> None:
    tid = "1234567890abcdef"
    name = unique_output_filename("song", tid, ext=".mp3")
    assert name.lower().endswith(".mp3")
    assert "_12345678" in name
