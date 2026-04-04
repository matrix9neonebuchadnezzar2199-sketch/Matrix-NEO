"""Optional Bearer-token authentication middleware.

When ``MATRIX_NEO_AUTH_TOKEN`` is configured (non-empty), every request
except ``/health`` must include ``Authorization: Bearer <token>`` or
the query parameter ``?token=<token>``.

When the token is empty the middleware is a no-op (backward compatible).
"""

from __future__ import annotations

import logging
from typing import Callable

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app import config as cfg

logger = logging.getLogger(__name__)

# Paths that never require authentication
_PUBLIC_PATHS = frozenset({"/health", "/docs", "/openapi.json", "/redoc"})


class BearerAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        token = cfg.AUTH_TOKEN
        if not token:
            return await call_next(request)

        path = request.url.path.rstrip("/") or "/"
        if path in _PUBLIC_PATHS:
            return await call_next(request)

        # Check Authorization header
        auth = request.headers.get("authorization", "")
        if auth.startswith("Bearer ") and auth[7:] == token:
            return await call_next(request)

        # Fallback: query parameter (useful for SSE EventSource)
        if request.query_params.get("token") == token:
            return await call_next(request)

        logger.warning("Auth rejected: %s %s from %s", request.method, path,
                        request.client.host if request.client else "?")
        return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
