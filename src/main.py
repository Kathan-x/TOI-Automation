"""
Main entry point for TOI Daily Downloader (v2.0).
Orchestrates:
- Fast skip check if today's paper already exists.
- Self-healing configuration and directory initialization.
- Smart Year/Month archive organization.
- Permanent history tracking in history.json.
- Rich Windows notifications with click-to-open action.
- Weekly background update checking.
"""

import argparse
import datetime
import os
import sys
from pathlib import Path

from .config import load_config, Config
from .downloader import Downloader
from .browser import BrowserAutomator
from .cleanup import organize_legacy_desktop_files, cleanup_temp_files
from .history_manager import HistoryManager
from .lock import SingleInstanceLock
from .logger import setup_logger
from .notification import NotificationManager
from .state_manager import StateManager
from .update_checker import check_for_updates
from .validator import validate_pdf_file


def parse_args():
    parser = argparse.ArgumentParser(
        description="Reliable Daily Times of India (Ahmedabad Edition) Downloader for Windows (v2.0)."
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force download even if today's edition is already recorded as completed."
    )
    parser.add_argument(
        "--date",
        type=str,
        default=None,
        help="Target date in YYYY-MM-DD format (defaults to local system date)."
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to custom config.json file."
    )
    parser.add_argument(
        "--browser",
        action="store_true",
        help="Use Playwright browser automation instead of direct endpoint."
    )
    parser.add_argument(
        "--no-notify",
        action="store_true",
        help="Disable desktop toast notifications."
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable detailed debug logging."
    )
    return parser.parse_args()


def run():
    args = parse_args()

    # 1. Self-Healing: Load or auto-repair configuration
    custom_config_path = Path(args.config) if args.config else None
    config = load_config(custom_config_path)

    # 2. Determine target date (always local system date unless explicitly specified)
    if args.date:
        try:
            target_date = datetime.datetime.strptime(args.date, "%Y-%m-%d").date()
        except ValueError:
            print(f"Error: Invalid date format '{args.date}'. Expected YYYY-MM-DD.", file=sys.stderr)
            sys.exit(1)
    else:
        target_date = datetime.date.today()

    date_str = target_date.strftime("%Y-%m-%d")
    edition = config.REQUIRED_EDITION

    # 3. Setup Daily Rotating Logger in %LOCALAPPDATA%\TOI-Daily\logs\
    log_level = "DEBUG" if args.verbose else config.log_level
    logger = setup_logger(config.logs_dir, log_level=log_level, target_date=target_date)

    # 4. Acquire Single-Instance Lock
    lock = SingleInstanceLock(lock_file_path=config.lock_file)
    if not lock.acquire():
        logger.info("Another instance of TOI Daily Downloader is already executing. Exiting quietly.")
        sys.exit(0)

    try:
        logger.info(f"=== TOI Daily Downloader v2.0 started for {edition} on {date_str} ===")

        # 5. Initialize Managers
        state_mgr = StateManager(config.state_file)
        history_mgr = HistoryManager(config.history_file)
        notify_enabled = not args.no_notify and config.notifications_enabled
        notifier = NotificationManager(enabled=notify_enabled)
        downloader = Downloader(config, logger)

        # 6. Organize any flat legacy files from previous versions into Year/Month structure
        organize_legacy_desktop_files(config.base_archive_dir, logger=logger)

        # 7. Fast Skip Check: If today's paper is already downloaded & verified in Year/Month folder
        final_pdf_path = config.get_target_pdf_path(target_date)
        is_already_completed = state_mgr.is_date_completed(date_str)

        if not args.force and is_already_completed and final_pdf_path.exists():
            is_valid, pages, msg = validate_pdf_file(final_pdf_path)
            if is_valid:
                logger.info(
                    f"Today's newspaper ({edition}, {date_str}) is already present in archive ({pages} pages). "
                    f"Exiting instantly without network overhead."
                )
                return

        # 8. Check for application updates weekly (non-intrusive)
        if config.check_updates_weekly:
            has_update, new_ver = check_for_updates(config.state_file, interval_days=7, logger=logger)
            if has_update and new_ver:
                notifier.notify_update_available(new_ver, logger=logger)

        # 9. Perform Download (strictly Ahmedabad)
        success = False
        saved_path = None
        pages_count = 0
        duration_sec = 0.0
        error_msg = None

        if args.browser or config.browser_fallback_enabled:
            logger.info("Running via browser automation fallback...")
            browser_automator = BrowserAutomator(config, logger)
            start_t = datetime.datetime.now()
            success, saved_path, pages_count, error_msg = browser_automator.download_via_browser(
                target_date, edition, final_pdf_path
            )
            duration_sec = (datetime.datetime.now() - start_t).total_seconds()
        else:
            success, saved_path, pages_count, duration_sec, error_msg = downloader.download_daily_paper(
                target_date, edition
            )

        # 10. Handle Result, State & History Recording
        if success and saved_path and saved_path.exists():
            file_size = saved_path.stat().st_size

            # Update state.json
            state_mgr.record_success(
                date_str=date_str,
                file_path=saved_path,
                page_count=pages_count,
                size_bytes=file_size
            )

            # Update permanent history.json
            history_mgr.record_entry(
                date_str=date_str,
                edition=edition,
                status="success",
                file_path=saved_path,
                page_count=pages_count,
                size_bytes=file_size,
                duration_seconds=duration_sec
            )

            logger.info(
                f"Successfully archived {edition} edition for {date_str}! "
                f"Location: {saved_path} ({pages_count} pages, {file_size:,} bytes)"
            )
            notifier.notify_success(file_path=saved_path, target_date=target_date, edition=edition, logger=logger)
        else:
            state_mgr.record_failure(date_str=date_str, error_message=error_msg or "Unknown error")
            history_mgr.record_entry(
                date_str=date_str,
                edition=edition,
                status="failed",
                duration_seconds=duration_sec,
                error_message=error_msg or "Download failed"
            )
            logger.error(f"Failed to download Ahmedabad newspaper for {date_str}: {error_msg}")
            notifier.notify_failure(error_summary=error_msg or "Download failed", edition=edition, logger=logger)
            sys.exit(1)

    except Exception as e:
        logger.exception(f"Unexpected fatal exception during execution: {e}")
        sys.exit(1)
    finally:
        lock.release()
        logger.info("=== TOI Daily Downloader finished ===")


if __name__ == "__main__":
    run()
