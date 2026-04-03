from app.utils.filename import sanitize_filename_for_windows


def test_sanitize_control_chars():
    s = "a\x01b\x1ec"
    out = sanitize_filename_for_windows(s)
    assert "\x01" not in out
    assert "\x1e" not in out


def test_sanitize_brackets():
    out = sanitize_filename_for_windows("foo[bar].mp4")
    assert "[" not in out
    assert out.endswith(".mp4")
