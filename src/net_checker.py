"""
Fast Internet Connectivity Checker for TOI Daily.
Probes connection before launching browser or large network requests.
"""

import logging
import socket
import time
from typing import Optional


def check_internet_connection(
    timeout_seconds: float = 3.0,
    retries: int = 3,
    delay_between_retries: float = 3.0,
    logger: Optional[logging.Logger] = None
) -> bool:
    """
    Checks if active internet connection is available using fast socket probes.
    Tries Cloudflare DNS (1.1.1.1:53) and Google DNS (8.8.8.8:53).
    """
    hosts = [("1.1.1.1", 53), ("8.8.8.8", 53)]

    for attempt in range(1, retries + 1):
        for host, port in hosts:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(timeout_seconds)
                sock.connect((host, port))
                sock.close()
                if logger:
                    logger.debug(f"Internet connection verified via {host}:{port}")
                return True
            except (socket.timeout, OSError):
                continue

        if attempt < retries:
            if logger:
                logger.info(f"Internet not detected (attempt {attempt}/{retries}). Waiting {delay_between_retries}s...")
            time.sleep(delay_between_retries)

    if logger:
        logger.warning("No internet connection detected after multiple attempts.")
    return False
