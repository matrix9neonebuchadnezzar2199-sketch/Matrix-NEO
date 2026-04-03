"""Build request URL with resolved IP + Host header (mitigate DNS rebinding for httpx)."""

from __future__ import annotations

import ipaddress
from urllib.parse import urlparse, urlunparse


def url_with_pinned_ip(original_url: str, resolved_ips: list[str]) -> tuple[str, str | None]:
    """
    Return (url_for_client, host_header).
    When using a literal IP in the URL, set Host to the original hostname for TLS/SNI.
    """
    if not resolved_ips:
        return original_url, None
    p = urlparse(original_url)
    hostname = p.hostname
    if not hostname:
        return original_url, None

    ip: str | None = None
    for s in resolved_ips:
        try:
            if isinstance(ipaddress.ip_address(s), ipaddress.IPv4Address):
                ip = s
                break
        except ValueError:
            continue
    if ip is None:
        ip = resolved_ips[0]
    try:
        ia = ipaddress.ip_address(ip)
    except ValueError:
        return original_url, None

    port = p.port
    if port is None:
        port = 443 if p.scheme == "https" else 80
    path = p.path or "/"
    if p.query:
        path += "?" + p.query

    if isinstance(ia, ipaddress.IPv6Address):
        netloc = f"[{ip}]:{port}"
    else:
        netloc = f"{ip}:{port}"
    new_u = urlunparse((p.scheme, netloc, path, "", "", ""))
    return new_u, hostname
