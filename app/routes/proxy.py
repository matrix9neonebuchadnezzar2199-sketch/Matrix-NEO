"""Image proxy for extension UI."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from app import config as cfg
from app.models import ProxyImageRequest
from app.services.thumbnail_service import fetch_thumbnail_http_bytes
from app.utils.rate_limit import RateLimiter
from app.utils.validation import validate_http_url

router = APIRouter(tags=["proxy"])

_limiter = RateLimiter(
    max_requests=cfg.PROXY_IMAGE_RATE_LIMIT,
    window_sec=cfg.PROXY_IMAGE_RATE_WINDOW_SEC,
)


def _client_rate_limit_key(request: Request) -> str:
    """Prefer X-Forwarded-For client when behind a proxy; else direct socket host."""
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        first = fwd.split(",")[0].strip()
        if first:
            return first
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


@router.post("/proxy-image")
async def proxy_image(request: Request, req: ProxyImageRequest):
    if not _limiter.is_allowed(_client_rate_limit_key(request)):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    _url, _ = validate_http_url(req.url.strip(), block_private_ips=cfg.BLOCK_PRIVATE_IPS)
    body, media_type = await fetch_thumbnail_http_bytes(req.url, req.cookie, req.referer)
    if not body:
        raise HTTPException(status_code=502, detail="Upstream image fetch failed")
    return Response(content=body, media_type=media_type or "image/jpeg")
