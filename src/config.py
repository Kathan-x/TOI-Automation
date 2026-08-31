"""
Configuration loader and path manager for TOI Daily Downloader (v2.0).
Provides smart Year/Month folder resolution (pure path calculation without creating empty folders),
self-healing config defaults, centralized website selectors, and isolated AppData storage.
"""

import ctypes
import json
import logging
import os
import sys
import winreg
import datetime
from ctypes import wintypes
from pathlib import Path
from typing import Any, Dict, Optional


# Root directory of the project
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "config.json"

# Centralized Website Selectors for Future-Proof Maintenance
WEBSITE_CONFIG = {
    "url": "https://www.indupaper.com/times-of-india.html",
    "toi_js_url": "https://www.indupaper.com/TOI.js",
    "api_base_fallback": "https://d309t8g1g9oksh.cloudfront.net",
    "api_fallbacks": [
        "https://d309t8g1g9oksh.cloudfront.net"
    ],
    "selectors": {
        "date_input": "#TOIDate",
        "city_select": "#TOICity",
        "pdf_button": "#TOIForm .btn-download, .btn-download, button.btn-download",
        "view_button": "#TOIForm .btn-view, .btn-view, button.btn-view",
        "form_container": "#TOIForm",
        "preview_container": "#preview",
        "download_group": "#TOIForm .btn-group"
    },
    "expected_city_value": "ahmedabad",
    "expected_city_text": "Ahmedabad",
    "publication_code": "toiac",
    "min_pages_threshold": 4
}


def get_desktop_dir() -> Path:
    """
    Dynamically resolves the actual Windows Desktop directory without hardcoding.
    Works across OneDrive redirection, customized user shell folders, spaces,
    and localized Windows setups.
    """
    if sys.platform == "win32":
        # 1. Check Windows Registry User Shell Folders
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
            ) as key:
                val, _ = winreg.QueryValueEx(key, "Desktop")
                resolved = Path(os.path.expandvars(val))
                if resolved.exists() and resolved.is_dir():
                    return resolved
        except Exception:
            pass

        # 2. Use Win32 SHGetFolderPath API
        try:
            CSIDL_DESKTOPDIRECTORY = 0x0010
            SHGFP_TYPE_CURRENT = 0
            buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
            ctypes.windll.shell32.SHGetFolderPathW(None, CSIDL_DESKTOPDIRECTORY, None, SHGFP_TYPE_CURRENT, buf)
            resolved = Path(buf.value)
            if resolved.exists() and resolved.is_dir():
                return resolved
        except Exception:
            pass

    # 3. Standard user profile fallback
    user_profile = Path(os.environ.get("USERPROFILE", os.path.expanduser("~")))
    onedrive_desktop = user_profile / "OneDrive" / "Desktop"
    if onedrive_desktop.exists() and onedrive_desktop.is_dir():
        return onedrive_desktop

    standard_desktop = user_profile / "Desktop"
    if standard_desktop.exists() and standard_desktop.is_dir():
        return standard_desktop

    return standard_desktop


def get_app_data_dir() -> Path:
    """
    Returns the dedicated %LOCALAPPDATA%\\TOI-Daily\\ directory for internal state,
    history, logs, temporary downloads, and debug snapshots.
    """
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        if base:
            app_dir = Path(base) / "TOI-Daily"
            app_dir.mkdir(parents=True, exist_ok=True)
            return app_dir

    app_dir = Path(os.path.expanduser("~")) / ".toi-daily"
    app_dir.mkdir(parents=True, exist_ok=True)
    return app_dir


class Config:
    REQUIRED_EDITION = "Ahmedabad"
    REQUIRED_CITY_SLUG = "ahmedabad"
    REQUIRED_PUB_CODE = "toiac"

    def __init__(self, config_data: Dict[str, Any], config_path: Path = DEFAULT_CONFIG_PATH):
        self.config_path = config_path
        self.edition: str = self.REQUIRED_EDITION
        self.retry_count: int = int(config_data.get("retry_count", 3))
        self.retry_delay_seconds: int = int(config_data.get("retry_delay_seconds", 5))
        self.request_timeout_seconds: int = int(config_data.get("request_timeout_seconds", 30))
        self.notifications_enabled: bool = bool(config_data.get("notifications_enabled", True))
        self.log_level: str = config_data.get("log_level", "INFO").upper()
        self.check_updates_weekly: bool = bool(config_data.get("check_updates_weekly", True))

        browser_fb = config_data.get("browser_fallback", {})
        self.browser_fallback_enabled: bool = bool(browser_fb.get("enabled", False))
        self.browser_headless: bool = bool(browser_fb.get("headless", True))
        self.browser_timeout_ms: int = int(browser_fb.get("timeout_ms", 45000))

    @property
    def city_slug(self) -> str:
        return self.REQUIRED_CITY_SLUG

    @property
    def publication_code(self) -> str:
        return self.REQUIRED_PUB_CODE

    @property
    def base_archive_dir(self) -> Path:
        """
        The base Desktop\\TOI Daily\\ directory path.
        NOTE: Pure path resolution. Does NOT create the directory prematurely.
        """
        desktop = get_desktop_dir()
        return desktop / "TOI Daily"

    def get_month_archive_dir(self, target_date: datetime.date) -> Path:
        """
        Returns the smart Year\\Month directory path:
        Desktop\\TOI Daily\\YYYY\\MonthName\\ (e.g. Desktop\\TOI Daily\\2026\\August)
        NOTE: Pure path calculation. NEVER creates directories prematurely on disk!
        """
        year_str = target_date.strftime("%Y")
        month_str = target_date.strftime("%B")  # Full month name (e.g. "August")
        return self.base_archive_dir / year_str / month_str

    def get_target_pdf_path(self, target_date: datetime.date) -> Path:
        """
        Returns the full canonical file path:
        Desktop\\TOI Daily\\YYYY\\MonthName\\TOI_Ahmedabad_YYYY-MM-DD.pdf
        NOTE: Pure path calculation. NEVER creates directories prematurely on disk!
        """
        filename = f"TOI_Ahmedabad_{target_date.strftime('%Y-%m-%d')}.pdf"
        return self.get_month_archive_dir(target_date) / filename

    @property
    def app_data_dir(self) -> Path:
        return get_app_data_dir()

    @property
    def temp_dir(self) -> Path:
        p = self.app_data_dir / "temp"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def logs_dir(self) -> Path:
        p = self.app_data_dir / "logs"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def debug_dir(self) -> Path:
        p = self.app_data_dir / "debug"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def state_file(self) -> Path:
        return self.app_data_dir / "state.json"

    @property
    def history_file(self) -> Path:
        return self.app_data_dir / "history.json"

    @property
    def lock_file(self) -> Path:
        return self.app_data_dir / "toi.lock"


def load_config(custom_path: Path = None, logger: Optional[logging.Logger] = None) -> Config:
    """
    Loads configuration with automatic self-healing.
    If config.json is missing or invalid, it regenerates valid defaults automatically.
    """
    target_path = custom_path or DEFAULT_CONFIG_PATH
    defaults = {
        "edition": "Ahmedabad",
        "download_dir": "Desktop/TOI Daily",
        "retry_count": 3,
        "retry_delay_seconds": 5,
        "request_timeout_seconds": 30,
        "notifications_enabled": True,
        "log_level": "INFO",
        "check_updates_weekly": True,
        "browser_fallback": {
            "enabled": False,
            "headless": True,
            "timeout_ms": 45000
        }
    }

    if target_path.exists():
        try:
            with open(target_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    defaults.update(data)
                else:
                    raise ValueError("Config file content is not a valid JSON dictionary.")
        except Exception as e:
            if logger:
                logger.warning(f"Config at {target_path} corrupted ({e}). Auto-repairing with defaults...")
            _write_default_config(target_path, defaults)
    else:
        _write_default_config(target_path, defaults)

    return Config(defaults, target_path)


def _write_default_config(target_path: Path, defaults: Dict[str, Any]) -> None:
    """Writes default configuration file."""
    try:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        with open(target_path, "w", encoding="utf-8") as f:
            json.dump(defaults, f, indent=2)
    except Exception:
        pass
