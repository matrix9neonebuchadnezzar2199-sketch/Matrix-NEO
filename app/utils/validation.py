"""URL validation (scheme + SSRF mitigation + DNS-resolved IP checks)."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

from fastapi import HTTPException

# Always block (cloud metadata, link-local IPv4, IPv6 link-local, ULA), regardless of MATRIX_NEO_BLOCK_PRIVATE_IPS
_ALWAYS_BLOCKED_NETS = (
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("fd00::/8"),
)


def _addr_always_blocked(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return any(addr in net for net in _ALWAYS_BLOCKED_NETS)


def validate_http_url(url: str, *, block_private_ips: bool) -> tuple[str, list[str]]:
    """
    Require http(s). Resolve hostname and validate all returned addresses.
    Returns (original_url, resolved_ip_strings) for optional connection pinning (httpx).
    N_m3u8DL-RE / yt-dlp など外部プロセスは別途 DNS 解決するため完全なリバインディング対策は不可（README 参照）。
    """
    u = (url or "").strip()
    parsed = urlparse(u)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(status_code=400, detail="URL must use http or https")
    host = parsed.hostname
    if not host:
        raise HTTPException(status_code=400, detail="Invalid URL: missing host")

    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        raise HTTPException(status_code=400, detail=f"DNS resolution failed: {e}") from e

    seen: set[str] = set()
    ordered: list[str] = []
    for info in infos:
        addr_s = info[4][0]
        if addr_s in seen:
            continue
        seen.add(addr_s)
        ordered.append(addr_s)
        try:
            addr = ipaddress.ip_address(addr_s)
        except ValueError:
            continue
        if _addr_always_blocked(addr):
            raise HTTPException(
                status_code=400,
                detail="Blocked address (link-local, ULA, or metadata range)",
            )
        if block_private_ips and (
            addr.is_private or addr.is_loopback or addr.is_reserved
        ):
            raise HTTPException(
                status_code=400,
                detail="Private or local addresses are blocked (MATRIX_NEO_BLOCK_PRIVATE_IPS=1)",
            )

    if not ordered:
        raise HTTPException(status_code=400, detail="DNS resolution produced no addresses")

    return u, ordered
