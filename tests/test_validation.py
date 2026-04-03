"""URL validation (SSRF layers)."""

from unittest.mock import patch

import pytest
from fastapi import HTTPException

from app.utils.validation import validate_http_url


def test_rejects_ftp():
    with pytest.raises(HTTPException) as e:
        validate_http_url("ftp://x/y", block_private_ips=False)
    assert e.value.status_code == 400


@patch("app.utils.validation.socket.getaddrinfo")
def test_always_blocks_metadata_ip(mock_gai):
    mock_gai.return_value = [(0, 0, 0, "", ("169.254.169.254", 0))]
    with pytest.raises(HTTPException) as e:
        validate_http_url("http://169.254.169.254/latest/meta-data", block_private_ips=False)
    assert e.value.status_code == 400
    assert "Blocked" in e.value.detail


@patch("app.utils.validation.socket.getaddrinfo")
def test_private_allowed_when_flag_off(mock_gai):
    mock_gai.return_value = [(0, 0, 0, "", ("192.168.1.1", 0))]
    u, ips = validate_http_url("http://example.com/x", block_private_ips=False)
    assert "http" in u
    assert "192.168.1.1" in ips


@patch("app.utils.validation.socket.getaddrinfo")
def test_private_blocked_when_flag_on(mock_gai):
    mock_gai.return_value = [(0, 0, 0, "", ("192.168.1.1", 0))]
    with pytest.raises(HTTPException) as e:
        validate_http_url("http://example.com/x", block_private_ips=True)
    assert e.value.status_code == 400
