"""
Weekly Update Checker for TOI Daily.
Checks for newer application releases on GitHub once every 7 days without auto-installing.
"""

import datetime
import json
import logging
from pathlib import Path
from typing import Optional, Tuple
import requests

CURRENT_VERSION = "2.0.0"
GITHUB_REPO = "toi-daily-downloader"  # Configurable or default


def check_for_updates(
    state_file: Path,
    interval_days: int = 7,
    logger: Optional[logging.Logger] = None
) -> Tuple[bool, Optional[str]]:
    """
    Checks GitHub releases for newer version if >= interval_days have elapsed.

    Returns:
        (update_available: bool, latest_version: Optional[str])
    """
    now = datetime.datetime.now()
    state_data = {}

    if state_file.exists():
        try:
            with open(state_file, "r", encoding="utf-8") as f:
                state_data = json.load(f)
        except Exception:
            state_data = {}

    last_check_str = state_data.get("last_update_check")
    if last_check_str:
        try:
            last_check = datetime.datetime.fromisoformat(last_check_str)
            if (now - last_check).days < interval_days:
                # Not yet time for weekly check
                return False, None
        except Exception:
            pass

    # Record update check timestamp
    state_data["last_update_check"] = now.isoformat()
    try:
        tmp_path = state_file.with_suffix(".json.tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(state_data, f, indent=2)
        tmp_path.replace(state_file)
    except Exception:
        pass

    # Perform lightweight check
    try:
        # Example release query (fails gracefully if offline or private repo)
        url = "https://api.github.com/repos/kathan/toi-automation/releases/latest"
        resp = requests.get(url, timeout=5, headers={"User-Agent": "TOI-Daily-Downloader"})
        if resp.status_code == 200:
            release_info = resp.json()
            latest_tag = release_info.get("tag_name", "").lstrip("v")
            if latest_tag and latest_tag > CURRENT_VERSION:
                if logger:
                    logger.info(f"A new version of TOI Daily Downloader (v{latest_tag}) is available!")
                return True, latest_tag
    except Exception as e:
        if logger:
            logger.debug(f"Update check skipped ({e})")

    return False, None
