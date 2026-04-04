from app.utils.url_connection import url_with_pinned_ip


def test_https_skips_ip_pinning():
    u, host = url_with_pinned_ip("https://example.com/path", ["192.0.2.1"])
    assert u == "https://example.com/path"
    assert host is None


def test_http_uses_resolved_ip_when_present():
    u, host = url_with_pinned_ip("http://example.com/foo", ["192.0.2.1"])
    assert "192.0.2.1" in u
    assert host == "example.com"


def test_http_preserves_query_and_fragment():
    u, host = url_with_pinned_ip(
        "http://example.com/foo/bar?x=1&y=2#frag",
        ["192.0.2.1"],
    )
    assert "192.0.2.1" in u
    assert "x=1" in u and "y=2" in u
    assert u.endswith("#frag")
    assert host == "example.com"
