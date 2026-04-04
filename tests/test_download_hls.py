"""HLS / m3u8 helper coverage."""

from __future__ import annotations

from unittest.mock import patch

from app import config as cfg
from app.services.download_service import m3u8_static_header_args


def test_m3u8_static_headers_disabled_when_cfg_off() -> None:
    with patch.object(cfg, "M3U8_BROWSER_HEADERS", False):
        assert m3u8_static_header_args() == []


def test_m3u8_static_headers_when_enabled() -> None:
    with patch.object(cfg, "M3U8_BROWSER_HEADERS", True):
        args = m3u8_static_header_args()
        assert "--header" in args
        assert any("User-Agent" in a for a in args)
