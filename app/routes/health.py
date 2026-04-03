"""Health and VPN status."""

from __future__ import annotations

import logging
from datetime import datetime

import httpx
from fastapi import APIRouter

from app import __version__
from app import config as cfg

router = APIRouter(tags=["health"])
logger = logging.getLogger(__name__)


@router.get("/health")
async def health():
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "version": __version__,
        "product": "MATRIX-NEO",
        "threads": cfg.MAX_THREADS,
        "youtube": True,
    }


@router.get("/vpn-status")
async def vpn_status():
    from app.services import http_client

    try:
        client = http_client.get_client()
        services = [
            "https://ipapi.co/json/",
            "https://ipinfo.io/json",
            "http://ip-api.com/json/",
        ]
        for service_url in services:
            try:
                response = await client.get(service_url, timeout=httpx.Timeout(10.0))
                if response.status_code != 200:
                    continue
                data = response.json()
                ip = data.get("ip") or data.get("query") or "Unknown"
                country = data.get("country_name") or data.get("country") or "Unknown"
                country_code = data.get("country_code") or data.get("countryCode") or ""
                city = data.get("city") or ""
                org = data.get("org") or data.get("isp") or ""
                vpn_keywords = [
                    "vpn",
                    "nord",
                    "express",
                    "surfshark",
                    "private",
                    "proxy",
                    "hosting",
                    "server",
                    "data center",
                    "datacenter",
                    "packethub",
                    "m247",
                    "datacamp",
                    "ovh",
                    "leaseweb",
                    "zscaler",
                    "cloudflare warp",
                    "mullvad",
                    "cyberghost",
                    "pia",
                    "proton",
                ]
                is_likely_vpn = any(kw in org.lower() for kw in vpn_keywords)
                is_japan = country_code.upper() == "JP"
                return {
                    "success": True,
                    "ip": ip,
                    "country": country,
                    "country_code": country_code,
                    "city": city,
                    "org": org,
                    "is_vpn": is_likely_vpn,
                    "is_home_country": is_japan,
                    "warning": is_japan and not is_likely_vpn,
                }
            except Exception as e:
                logger.debug("VPN service %s: %s", service_url, e)
                continue
        return {"success": False, "error": "All IP services failed"}
    except Exception as e:
        logger.exception("vpn-status: %s", e)
        return {"success": False, "error": str(e)}
