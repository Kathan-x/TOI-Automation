"""
Automated Temporary File Cleaner and Archive Organizer for TOI Daily.
- Cleans up orphan .part/.tmp files older than 24 hours from AppData temp.
- Moves legacy flat files into Year/Month structure when needed.
- Prunes any empty test/future folders from the archive so only folders containing real PDFs remain.
- NEVER deletes completed newspaper PDFs.
"""

import datetime
import logging
import os
import re
import shutil
import time
from pathlib import Path
from typing import Optional


def cleanup_temp_files(
    temp_dir: Path,
    max_age_hours: float = 24.0,
    logger: Optional[logging.Logger] = None
) -> int:
    """
    Cleans up temporary (.part, .tmp) and orphan files older than max_age_hours
    from the isolated AppData temp directory. Never touches Desktop archive.
    """
    if not temp_dir.exists():
        return 0

    now = time.time()
    cutoff = now - (max_age_hours * 3600)
    cleaned_count = 0

    try:
        for item in temp_dir.iterdir():
            if item.is_file():
                try:
                    file_mtime = item.stat().st_mtime
                    if file_mtime < cutoff or item.suffix in [".part", ".tmp"]:
                        item.unlink(missing_ok=True)
                        cleaned_count += 1
                        if logger:
                            logger.debug(f"Removed temporary file: {item.name}")
                except Exception as e:
                    if logger:
                        logger.warning(f"Could not remove temp file {item}: {e}")
    except Exception as e:
        if logger:
            logger.warning(f"Error while scanning temp directory {temp_dir}: {e}")

    if cleaned_count > 0 and logger:
        logger.info(f"Cleaned up {cleaned_count} temporary files from AppData temp directory.")
    return cleaned_count


def organize_legacy_desktop_files(base_archive_dir: Path, logger: Optional[logging.Logger] = None) -> int:
    """
    Scans the base Desktop\\TOI Daily\\ directory for any flat PDF files
    (e.g. TOI_Ahmedabad_YYYY-MM-DD.pdf) and moves them into their appropriate
    Year\\Month subfolders (e.g. TOI Daily\\2026\\August\\).
    """
    if not base_archive_dir.exists():
        return 0

    moved_count = 0
    date_pattern = re.compile(r"TOI_[A-Za-z]+_(\d{4})-(\d{2})-(\d{2})\.pdf$", re.IGNORECASE)

    try:
        for item in base_archive_dir.iterdir():
            if item.is_file() and item.suffix.lower() == ".pdf":
                match = date_pattern.search(item.name)
                if match:
                    year, month, day = match.groups()
                    try:
                        file_date = datetime.date(int(year), int(month), int(day))
                        month_name = file_date.strftime("%B")
                        target_dir = base_archive_dir / year / month_name
                        target_dir.mkdir(parents=True, exist_ok=True)
                        target_file = target_dir / item.name

                        if not target_file.exists():
                            shutil.move(str(item), str(target_file))
                            moved_count += 1
                            if logger:
                                logger.info(f"Organized existing newspaper {item.name} -> {year}\\{month_name}\\")
                        else:
                            item.unlink()
                    except Exception as e:
                        if logger:
                            logger.warning(f"Failed to organize legacy file {item}: {e}")
    except Exception as e:
        if logger:
            logger.warning(f"Error checking legacy archive files: {e}")

    # Clean up empty folders afterwards
    prune_empty_archive_folders(base_archive_dir, logger=logger)
    return moved_count


def prune_empty_archive_folders(base_archive_dir: Path, logger: Optional[logging.Logger] = None) -> int:
    """
    Recursively removes any empty directories inside Desktop\\TOI Daily\\.
    Guarantees that no empty future year/month folders linger on the Desktop.
    Never removes directories that contain files.
    """
    if not base_archive_dir.exists():
        return 0

    removed_count = 0
    # Walk bottom-up to remove empty leaf month folders then empty parent year folders
    for root, dirs, files in os.walk(str(base_archive_dir), topdown=False):
        for dir_name in dirs:
            dir_path = Path(root) / dir_name
            # Never delete the base TOI Daily folder itself
            if dir_path == base_archive_dir:
                continue
            try:
                # Check if directory is empty
                if not any(dir_path.iterdir()):
                    dir_path.rmdir()
                    removed_count += 1
                    if logger:
                        logger.debug(f"Pruned empty archive folder: {dir_path.relative_to(base_archive_dir)}")
            except Exception as e:
                if logger:
                    logger.debug(f"Could not prune folder {dir_path}: {e}")

    return removed_count
