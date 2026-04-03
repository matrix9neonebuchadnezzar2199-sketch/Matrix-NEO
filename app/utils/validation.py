"""URL validation (scheme + optional SSRF mitigation)."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

from fastapi import HTTPException


def validate_http_url(url: str, *, block_private_ips: bool) -> str:
    """
    Require http(s) URL. Optionally block hostnames that resolve to private/reserved IPs.
    """
    u = (url or "").strip()
    parsed = urlparse(u)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(status_code=400, detail="URL must use http or https")
    host = parsed.hostname
    if not host:
        raise HTTPException(status_code=400, detail="Invalid URL: missing host")

    if not block_private_ips:
        return u

    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        raise HTTPException(status_code=400, detail=f"DNS resolution failed: {e}") from e

    for info in infos:
        addr_s = info[4][0]
        try:
            addr = ipaddress.ip_address(addr_s)
        except ValueError:
            continue
        if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved:
            raise HTTPException(
                status_code=400,
                detail="Private or local addresses are blocked (MATRIX_NEO_BLOCK_PRIVATE_IPS=1)",
            )
    return u
