"""Image proxy for extension UI."""

from __future__ import annotations

from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from app.models import ProxyImageRequest
from app.services.thumbnail_service import fetch_thumbnail_http_bytes

router = APIRouter(tags=["proxy"])


@router.post("/proxy-image")
async def proxy_image(req: ProxyImageRequest):
    parsed = urlparse(req.url.strip())
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(status_code=400, detail="Invalid URL")
    body, media_type = await fetch_thumbnail_http_bytes(req.url, req.cookie, req.referer)
    if not body:
        raise HTTPException(status_code=502, detail="Upstream image fetch failed")
    return Response(content=body, media_type=media_type or "image/jpeg")
