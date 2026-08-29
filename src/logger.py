"""
Logging configuration for TOI Daily Downloader.
Generates clean daily log files under logs/YYYY-MM-DD.log.
"""

import datetime
import logging
import sys
from pathlib import Path


def setup_logger(logs_dir: Path, log_level: str = "INFO", target_date: datetime.date = None) -> logging.Logger:
    """
    Initializes and returns a configured logger with console and file handlers.
    """
    if target_date is None:
        target_date = datetime.date.today()

    logs_dir.mkdir(parents=True, exist_ok=True)
    log_filename = f"{target_date.strftime('%Y-%m-%d')}.log"
    log_file_path = logs_dir / log_filename

    logger = logging.getLogger("TOI_Daily")
    level = getattr(logging, log_level.upper(), logging.INFO)
    logger.setLevel(level)

    # Avoid duplicate handlers if setup_logger is called repeatedly
    if logger.hasHandlers():
        logger.handlers.clear()

    formatter = logging.Formatter(
        fmt="[%(asctime)s] [%(levelname)-7s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # File Handler
    try:
        file_handler = logging.FileHandler(log_file_path, encoding="utf-8")
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except Exception as e:
        sys.stderr.write(f"Warning: Could not initialize log file handler at {log_file_path}: {e}\n")

    # Console Handler (UTF-8 safe on Windows)
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    return logger
