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
import logging
import re
import sys
from pathlib import Path
from typing import Optional

from .config import load_config, Config
from .downloader import Downloader
from .browser import BrowserAutomator
from .cleanup import organize_legacy_desktop_files
from .history_manager import HistoryManager
from .lock import SingleInstanceLock
from .logger import setup_logger
from .notification import NotificationManager
from .state_manager import StateManager
from .update_checker import check_for_updates
from .validator import validate_pdf_file


def parse_args():
    parser = argparse.ArgumentParser(
        description="Reliable Daily Times of India (Ahmedabad Edition) Downloader for Windows (v3.0)."
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
        "--no-backfill",
        action="store_true",
        help="Disable automatic recent-day backfill check."
    )
    parser.add_argument(
        "--backfill-days",
        type=int,
        default=None,
        help="Maximum historical days to sweep for recovery (defaults to config max_backfill_days, 31)."
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable detailed debug logging."
    )
    return parser.parse_args()


def find_last_archived_date_before(
    before_date: datetime.date,
    config: Config,
    state_mgr: StateManager
) -> Optional[datetime.date]:
    """
    Finds the most recent successfully archived Ahmedabad edition date strictly prior to before_date.
    Inspects both StateManager records and existing validated files in the Desktop archive.
    """
    found_dates = set()

    # 1. State manager history
    state = state_mgr.load_state()
    for date_str, rec in state.get("history", {}).items():
        if isinstance(rec, dict) and rec.get("status") == "success":
            try:
                d = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
                if d < before_date:
                    found_dates.add(d)
            except ValueError:
                pass

    last_succ = state.get("last_successful_date")
    if last_succ:
        try:
            d = datetime.datetime.strptime(last_succ, "%Y-%m-%d").date()
            if d < before_date:
                found_dates.add(d)
        except ValueError:
            pass

    # 2. Filesystem check for existing valid PDFs
    base_dir = config.base_archive_dir
    if base_dir.exists():
        pattern = re.compile(r"TOI_Ahmedabad_(\d{4})-(\d{2})-(\d{2})\.pdf$", re.IGNORECASE)
        for pdf_file in base_dir.rglob("TOI_Ahmedabad_*.pdf"):
            match = pattern.search(pdf_file.name)
            if match:
                try:
                    d = datetime.date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
                    if d < before_date and pdf_file.stat().st_size > 100 * 1024:
                        found_dates.add(d)
                except Exception:
                    pass

    return max(found_dates) if found_dates else None


def recover_missed_days(
    target_date: datetime.date,
    config: Config,
    downloader: Downloader,
    state_mgr: StateManager,
    history_mgr: HistoryManager,
    notifier: NotificationManager,
    logger: logging.Logger,
    max_days: Optional[int] = None,
    days: Optional[int] = None
) -> dict:
    """
    Recovers missing Ahmedabad editions between the last successfully archived date and target_date.
    Uses configurable safety cap (default 31 days).

    Guarantees:
    - Never overwrites an existing valid PDF.
    - Strictly Ahmedabad edition only.
    - Preserves smart Year/Month archive structure (Desktop\\TOI Daily\\YYYY\\MonthName\\).
    - Failed downloads remain retryable (never marks unavailable paper as successful).
    - Backfill results do not regress today's top-level state.
    """
    edition = config.REQUIRED_EDITION
    results = {
        "checked": 0,
        "existing_valid": 0,
        "recovered": 0,
        "failed": 0
    }

    safety_cap = days if days is not None else (max_days if max_days is not None else getattr(config, "max_backfill_days", 31))

    if days is not None and max_days is None:
        # Fixed sweep mode (e.g. legacy/unit test passing days=7)
        dates_to_check = [target_date - datetime.timedelta(days=i) for i in range(1, days + 1)]
    else:
        # Dynamic recovery model: inspect missing dates from (last_successful + 1) to (target_date - 1)
        last_archived = find_last_archived_date_before(target_date, config, state_mgr)
        earliest_date = target_date - datetime.timedelta(days=safety_cap)

        if last_archived:
            start_date = max(last_archived + datetime.timedelta(days=1), earliest_date)
        else:
            start_date = earliest_date

        end_date = target_date - datetime.timedelta(days=1)
        dates_to_check = []
        curr = start_date
        while curr <= end_date:
            dates_to_check.append(curr)
            curr += datetime.timedelta(days=1)

    if not dates_to_check:
        logger.info("No missed dates detected between last successful archive and today.")
        return results

    logger.info(
        f"Checking {len(dates_to_check)} historical dates for missing {edition} editions "
        f"({dates_to_check[0]} to {dates_to_check[-1]})..."
    )

    for past_date in dates_to_check:
        past_date_str = past_date.strftime("%Y-%m-%d")
        results["checked"] += 1

        target_pdf_path = config.get_target_pdf_path(past_date)

        # 1. Check if valid PDF already exists in archive
        if target_pdf_path.exists():
            is_valid, pages, msg = validate_pdf_file(target_pdf_path)
            if is_valid:
                logger.info(
                    f"Backfill: {edition} {past_date_str} already archived ({pages} pages) at {target_pdf_path}. Preserving without overwrite."
                )
                results["existing_valid"] += 1
                if not state_mgr.is_date_completed(past_date_str):
                    state_mgr.record_backfill_result(
                        date_str=past_date_str,
                        success=True,
                        file_path=target_pdf_path,
                        page_count=pages,
                        size_bytes=target_pdf_path.stat().st_size
                    )
                continue
            else:
                logger.warning(
                    f"Backfill: Existing file for {past_date_str} is corrupted: {msg}. Removing before recovery..."
                )
                from .validator import safe_cleanup_corrupted_file
                safe_cleanup_corrupted_file(target_pdf_path, logger)

        # 2. PDF missing or invalid: attempt recovery download (strictly Ahmedabad)
        logger.info(f"Backfill: Attempting recovery download for missing paper ({edition}, {past_date_str})...")
        try:
            b_success, b_path, b_pages, b_dur, b_err = downloader.download_daily_paper(
                past_date, edition
            )
            if b_success and b_path and b_path.exists():
                file_size = b_path.stat().st_size
                state_mgr.record_backfill_result(
                    date_str=past_date_str,
                    success=True,
                    file_path=b_path,
                    page_count=b_pages,
                    size_bytes=file_size
                )
                history_mgr.record_entry(
                    date_str=past_date_str,
                    edition=edition,
                    status="success",
                    file_path=b_path,
                    page_count=b_pages,
                    size_bytes=file_size,
                    duration_seconds=b_dur
                )
                results["recovered"] += 1
                logger.info(
                    f"Backfill: Successfully recovered {edition} for {past_date_str}! "
                    f"Location: {b_path} ({b_pages} pages, {file_size:,} bytes)"
                )
            else:
                # Failed downloads must remain retryable. Do not mark as success!
                state_mgr.record_backfill_result(
                    date_str=past_date_str,
                    success=False,
                    error_message=b_err or "Paper unavailable"
                )
                history_mgr.record_entry(
                    date_str=past_date_str,
                    edition=edition,
                    status="failed",
                    duration_seconds=b_dur,
                    error_message=b_err or "Backfill download failed"
                )
                results["failed"] += 1
                logger.info(
                    f"Backfill: {edition} for {past_date_str} unavailable: {b_err}. Remains retryable."
                )
        except Exception as exc:
            logger.warning(f"Backfill: Exception during download for {past_date_str}: {exc}")
            results["failed"] += 1

    logger.info(
        f"Automatic backfill completed: {results['checked']} checked, "
        f"{results['existing_valid']} existing valid preserved, "
        f"{results['recovered']} recovered, "
        f"{results['failed']} remaining retryable/unavailable."
    )
    return results


# Maintain backward-compatible alias
backfill_recent_days = recover_missed_days


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

    # 3. Setup Daily Rotating Logger in %LOCALAPPDATA%\\TOI-Daily\\logs\\
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
        today_already_valid = False

        if not args.force and is_already_completed and final_pdf_path.exists():
            is_valid, pages, msg = validate_pdf_file(final_pdf_path)
            if is_valid:
                today_already_valid = True
                logger.info(
                    f"Today's newspaper ({edition}, {date_str}) is already present in archive ({pages} pages)."
                )

        # 8. Check for application updates weekly (non-intrusive)
        if config.check_updates_weekly:
            has_update, new_ver = check_for_updates(config.state_file, interval_days=7, logger=logger)
            if has_update and new_ver:
                notifier.notify_update_available(new_ver, logger=logger)

        # 9. Perform Download for Today's Paper (strictly Ahmedabad) if needed
        success = False
        saved_path = None
        pages_count = 0
        duration_sec = 0.0
        error_msg = None

        if not today_already_valid:
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
                # Automatic fallback to Browser Automation if direct endpoint failed
                if not success:
                    try:
                        import playwright
                        logger.info("Direct API retrieval failed. Attempting browser automation fallback...")
                        browser_automator = BrowserAutomator(config, logger)
                        b_start = datetime.datetime.now()
                        b_success, b_saved_path, b_pages, b_err = browser_automator.download_via_browser(
                            target_date, edition, final_pdf_path
                        )
                        if b_success and b_saved_path:
                            success, saved_path, pages_count, error_msg = b_success, b_saved_path, b_pages, None
                            duration_sec += (datetime.datetime.now() - b_start).total_seconds()
                        else:
                            logger.warning(f"Browser fallback was unable to retrieve paper: {b_err}")
                    except ImportError:
                        logger.debug("Playwright not installed; skipping browser fallback.")
                    except Exception as b_exc:
                        logger.debug(f"Browser fallback encountered exception: {b_exc}")

            # 10. Handle Result, State & History Recording for Today
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
                logger.warning(
                    f"Ahmedabad newspaper unavailable for {date_str}: {error_msg}. Will retry on next scheduled interval."
                )
                if args.force:
                    notifier.notify_failure(error_summary=error_msg or "Download failed", edition=edition, logger=logger)

        # 11. Automatic Missed-Day Recovery
        if not args.no_backfill and getattr(config, "backfill_enabled", True):
            recover_missed_days(
                target_date=target_date,
                config=config,
                downloader=downloader,
                state_mgr=state_mgr,
                history_mgr=history_mgr,
                notifier=notifier,
                logger=logger,
                max_days=args.backfill_days or getattr(config, "max_backfill_days", 31)
            )

        # 12. Exit Code determination
        if not today_already_valid and not success:
            sys.exit(1)

    except Exception as e:
        logger.exception(f"Unexpected fatal exception during execution: {e}")
        sys.exit(1)
    finally:
        lock.release()
        logger.info("=== TOI Daily Downloader finished ===")


if __name__ == "__main__":
    run()
