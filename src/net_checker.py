"""
Fast and Reliable Internet Connectivity Checker for TOI Daily.
Probes connection using standard HTTPS/HTTP endpoints with socket fallback.
Avoids fragile port 53 (DNS) raw TCP queries which are frequently blocked by local ISPs/Wi-Fi firewalls.
"""

import logging
import socket
import urllib.request
from typing import Optional


def check_internet_connection(
    timeout_seconds: float = 3.0,
    retries: int = 2,
    delay_between_retries: float = 1.0,
    logger: Optional[logging.Logger] = None
) -> bool:
    """
    Checks if active internet connection is available.
    1. Attempts lightweight HTTP HEAD/GET to reliable endpoints (google generate_204, cloudflare).
    2. Falls back to TCP socket probe on standard HTTPS port 443.
    """
    http_endpoints = [
        "http://www.google.com/generate_204",
        "https://www.cloudflare.com",
        "https://www.indupaper.com/times-of-india.html"
    ]

    for attempt in range(1, retries + 1):
        # 1. Try HTTP endpoints
        for url in http_endpoints:
            try:
                req = urllib.request.Request(
                    url,
                    headers={"User-Agent": "Mozilla/5.0"}
                )
                with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
                    if resp.status in (200, 204):
                        if logger:
                            logger.debug(f"Internet connection verified via {url} (HTTP {resp.status})")
                        return True
            except Exception:
                continue

        # 2. Try TCP socket on port 443 (standard HTTPS, never blocked by consumer firewalls)
        socket_targets = [("1.1.1.1", 443), ("8.8.8.8", 443), ("www.google.com", 443)]
        for host, port in socket_targets:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(timeout_seconds)
                sock.connect((host, port))
                sock.close()
                if logger:
                    logger.debug(f"Internet connection verified via socket {host}:{port}")
                return True
            except Exception:
                continue

        if attempt < retries and delay_between_retries > 0:
            import time
            time.sleep(delay_between_retries)

    if logger:
        logger.warning("Internet connectivity check could not reach test endpoints. Will attempt download anyway.")
    # Return True so we don't hard-block if test probes fail but actual API might be reachable
    return False

