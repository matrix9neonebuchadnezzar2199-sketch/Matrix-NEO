"""Thumbnail embed edge cases."""

from __future__ import annotations

import pytest

from app.services.thumbnail_service import embed_thumbnail


@pytest.mark.asyncio
async def test_embed_thumbnail_missing_files(tmp_path) -> None:
    v = tmp_path / "v.mp4"
    t = tmp_path / "t.jpg"
    assert await embed_thumbnail(v, t, "tid") is False
