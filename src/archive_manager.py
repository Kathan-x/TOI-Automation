"""
Automatic Monthly ZIP Archiving & Storage Optimization for TOI Daily.
Provides safe, atomic, verified monthly ZIP archiving for completed months,
with crash recovery, zero data loss guarantees, and seamless historical backfill integration.
"""

import argparse
import calendar
import datetime
import hashlib
import logging
import os
import re
import shutil
import sys
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .config import Config, load_config
from .history_manager import format_size
from .lock import SingleInstanceLock
from .logger import setup_logger
from .notification import NotificationManager
from .validator import validate_pdf_file, validate_pdf_bytes, MIN_VALID_PDF_SIZE_BYTES


# Case-insensitive mapping of month names to month numbers (1..12)
MONTH_NAME_TO_INT: Dict[str, int] = {
    calendar.month_name[m].lower(): m for m in range(1, 13)
}
MONTH_NAME_TO_INT.update({
    calendar.month_abbr[m].lower(): m for m in range(1, 13)
})

INT_TO_MONTH_NAME: Dict[int, str] = {
    m: calendar.month_name[m] for m in range(1, 13)
}


def get_completed_months(
    base_archive_dir: Path,
    current_date: Optional[datetime.date] = None
) -> List[Tuple[int, int, str, Path]]:
    """
    Identifies all completed month folders in Desktop\\TOI Daily that are strictly
    older than the current calendar month. Never includes the current active month.

    Returns a list of tuples sorted chronologically:
        [(year: int, month: int, month_name: str, folder_path: Path), ...]
    """
    if current_date is None:
        current_date = datetime.date.today()

    completed: List[Tuple[int, int, str, Path]] = []
    if not base_archive_dir.exists():
        return completed

    # Scan for 4-digit Year subdirectories
    for year_dir in base_archive_dir.iterdir():
        if not year_dir.is_dir() or not re.match(r"^\d{4}$", year_dir.name):
            continue

        year_int = int(year_dir.name)

        # Scan for Month subdirectories inside Year
        for month_dir in year_dir.iterdir():
            if not month_dir.is_dir():
                continue

            folder_name_lower = month_dir.name.strip().lower()
            month_int = MONTH_NAME_TO_INT.get(folder_name_lower)
            if not month_int:
                continue

            # Strict calendar boundary: must be strictly older than current (year, month)
            if (year_int, month_int) < (current_date.year, current_date.month):
                canonical_month_name = INT_TO_MONTH_NAME[month_int]
                completed.append((year_int, month_int, canonical_month_name, month_dir))

    completed.sort(key=lambda item: (item[0], item[1]))
    return completed


def get_monthly_archive_path(
    base_archive_dir: Path,
    year: int,
    month: int,
    month_name: str
) -> Path:
    """
    Returns the canonical path for a monthly ZIP archive:
    Desktop\\TOI Daily\\YYYY\\YYYY-MM_MonthName.zip
    Beside the monthly folder, inside the corresponding year directory.
    """
    return base_archive_dir / str(year) / f"{year}-{month:02d}_{month_name}.zip"


def get_temporary_archive_path(final_zip_path: Path) -> Path:
    """Returns the temporary file path used during atomic creation: *.zip.tmp"""
    return final_zip_path.with_suffix(".zip.tmp")


def cleanup_temporary_archives(
    base_archive_dir: Path,
    logger: Optional[logging.Logger] = None
) -> int:
    """
    Safely removes abandoned temporary archive files (*.zip.tmp) from previous interrupted runs.
    Never mistakes a temporary archive for a finalized archive.
    """
    if not base_archive_dir.exists():
        return 0

    cleaned = 0
    for tmp_file in base_archive_dir.rglob("*.zip.tmp"):
        try:
            if tmp_file.is_file():
                tmp_file.unlink()
                cleaned += 1
                if logger:
                    logger.info(f"Cleaned up abandoned temporary archive: {tmp_file.name}")
        except Exception as e:
            if logger:
                logger.warning(f"Could not remove temporary archive {tmp_file}: {e}")

    return cleaned


def inventory_month_folder(month_dir: Path) -> Tuple[Dict[str, Dict[str, Any]], List[str]]:
    """
    Recursively inventories a monthly folder.
    Records relative paths, file sizes, and SHA-256 hashes.
    Detects any active downloads or unfinished .part/.tmp files.

    Returns:
        (inventory_dict, unfinished_files_list)
        where inventory_dict maps relative_path_str -> {"size": int, "sha256": str, "abs_path": Path}
    """
    inventory: Dict[str, Dict[str, Any]] = {}
    unfinished_files: List[str] = []

    if not month_dir.exists():
        return inventory, unfinished_files

    for root, _, files in os.walk(str(month_dir)):
        for f in sorted(files):
            abs_p = Path(root) / f
            rel_p = str(abs_p.relative_to(month_dir)).replace("\\", "/")

            # Check for unfinished or partial files
            if f.endswith((".part", ".tmp", ".download", ".crdownload")):
                unfinished_files.append(rel_p)
                continue

            try:
                sz = abs_p.stat().st_size
                with open(abs_p, "rb") as fp:
                    hasher = hashlib.sha256()
                    while chunk := fp.read(65536):
                        hasher.update(chunk)
                    digest = hasher.hexdigest()

                inventory[rel_p] = {
                    "size": sz,
                    "sha256": digest,
                    "abs_path": abs_p
                }
            except Exception as e:
                unfinished_files.append(f"{rel_p} (read error: {e})")

    return inventory, unfinished_files


def verify_archive_integrity(
    zip_path: Path,
    expected_inventory: Optional[Dict[str, Dict[str, Any]]] = None,
    month_name: Optional[str] = None,
    allow_subset: bool = False
) -> Tuple[bool, str, Dict[str, Any]]:
    """
    Thoroughly verifies the integrity of a monthly ZIP archive:
    1. Verifies the file exists and is a valid ZIP.
    2. Runs ZipFile.testzip() to check for CRC/deflate stream corruption.
    3. If expected_inventory is provided:
       - Confirms every source file appears in the ZIP under MonthName/rel_path.
       - Compares archived file sizes and SHA-256 hashes byte-for-byte.
       - Confirms no expected files were omitted, altered, or duplicated.
       - If allow_subset is True, allows the inventory to be a subset of the archive
         (e.g. for safely resuming interrupted folder cleanup).

    Returns:
        (is_valid: bool, description: str, stats_dict: dict)
    """
    if not zip_path.exists():
        return False, f"Archive does not exist: {zip_path}", {}

    if not zipfile.is_zipfile(zip_path):
        return False, f"File is not a valid ZIP archive: {zip_path}", {}

    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            # 1. Low-level CRC / Deflate integrity test
            corrupt_file = zf.testzip()
            if corrupt_file is not None:
                return False, f"ZIP testzip() failed: corrupted entry '{corrupt_file}'", {}

            namelist = zf.namelist()
            # Filter out directory entries
            file_entries = [name for name in namelist if not name.endswith("/")]

            # Check for duplicates in namelist
            if len(file_entries) != len(set(file_entries)):
                return False, f"ZIP contains duplicate entry names: {len(file_entries)} vs {len(set(file_entries))}", {}

            # 2. Match against expected source inventory if provided
            if expected_inventory is not None:
                for rel_p, meta in expected_inventory.items():
                    # Expected entry formats: "MonthName/filename.pdf" or "filename.pdf"
                    expected_arcnames = []
                    if month_name:
                        expected_arcnames.append(f"{month_name}/{rel_p}")
                    expected_arcnames.append(rel_p)

                    matching_entry = None
                    for candidate in expected_arcnames:
                        if candidate in namelist:
                            matching_entry = candidate
                            break

                    if not matching_entry:
                        return False, f"Archive missing expected file: {rel_p}", {}

                    # Read entry bytes and verify size and SHA-256 hash
                    entry_bytes = zf.read(matching_entry)
                    if len(entry_bytes) != meta["size"]:
                        return False, (
                            f"Size mismatch for '{matching_entry}': "
                            f"expected {meta['size']} bytes, found {len(entry_bytes)} bytes in ZIP"
                        ), {}

                    entry_sha256 = hashlib.sha256(entry_bytes).hexdigest()
                    if entry_sha256 != meta["sha256"]:
                        return False, (
                            f"SHA-256 hash mismatch for '{matching_entry}': "
                            f"expected {meta['sha256']}, got {entry_sha256}"
                        ), {}

                if not allow_subset and len(file_entries) != len(expected_inventory):
                    return False, (
                        f"Entry count mismatch: ZIP has {len(file_entries)} entries, "
                        f"expected {len(expected_inventory)}"
                    ), {}

            return True, "Archive verified successfully (all entries and checksums match).", {
                "entry_count": len(file_entries),
                "zip_size": zip_path.stat().st_size
            }
    except Exception as e:
        return False, f"Exception while verifying ZIP integrity: {e}", {}


def archive_month_folder(
    month_dir: Path,
    year: int,
    month: int,
    month_name: str,
    base_archive_dir: Path,
    delete_source: bool = False,
    min_savings_pct: float = 0.0,
    current_date: Optional[datetime.date] = None,
    logger: Optional[logging.Logger] = None
) -> Dict[str, Any]:
    """
    Archives a completed month folder into a verified ZIP archive following the 16-step
    safe creation procedure.

    Guarantees:
    - Never archives the current calendar month.
    - Postpones if unfinished .part/.tmp files are present.
    - Creates temporary ZIP (*.zip.tmp) and tests with ZipFile.testzip().
    - Verifies SHA-256 hashes of every file before any destructive action.
    - Atomically renames temporary ZIP to final archive.
    - Deletes source folder ONLY AFTER full verification, and ONLY IF delete_source is True
      and measured savings meet min_savings_pct (or explicit override).
    - Preserves existing matching archives idempotently without duplicating or corrupting.
    - Preserves both copies and errors out if an existing archive conflicts.

    Returns result dict with detailed storage accounting.
    """
    if current_date is None:
        current_date = datetime.date.today()

    result: Dict[str, Any] = {
        "status": "pending",
        "year": year,
        "month": month,
        "month_name": month_name,
        "zip_path": None,
        "file_count": 0,
        "original_size": 0,
        "zip_size": 0,
        "saved_bytes": 0,
        "saved_pct": 0.0,
        "source_deleted": False,
        "error": None
    }

    # Step 1: Calendar boundary verification
    if (year, month) >= (current_date.year, current_date.month):
        msg = f"Cannot archive active month: {month_name} {year} is not older than current month."
        if logger:
            logger.warning(msg)
        result["status"] = "skipped"
        result["error"] = msg
        return result

    final_zip_path = get_monthly_archive_path(base_archive_dir, year, month, month_name)
    result["zip_path"] = final_zip_path

    # Step 2 & 3: Inventory source folder
    inventory, unfinished = inventory_month_folder(month_dir)

    # Step 5: Check for unfinished files or active downloads
    if unfinished:
        msg = f"Postponing archiving for {month_name} {year}: active/unfinished files detected ({len(unfinished)}): {unfinished}"
        if logger:
            logger.warning(msg)
        result["status"] = "postponed"
        result["error"] = msg
        return result

    # Handle empty source folder
    if not inventory:
        # Check if final archive already exists from a prior successful run
        if final_zip_path.exists():
            is_valid, msg, _ = verify_archive_integrity(final_zip_path)
            if is_valid:
                if logger:
                    logger.info(f"Month folder {month_dir} is empty, but verified archive exists: {final_zip_path.name}.")
                if delete_source and month_dir.exists():
                    try:
                        month_dir.rmdir()
                        result["source_deleted"] = True
                    except Exception:
                        pass
                result["status"] = "success"
                result["file_count"] = 0
                return result

        if logger:
            logger.info(f"Month folder {month_name} {year} contains no files. Pruning empty directory.")
        if delete_source and month_dir.exists():
            try:
                month_dir.rmdir()
                result["source_deleted"] = True
            except Exception:
                pass
        result["status"] = "empty"
        return result

    total_original_bytes = sum(m["size"] for m in inventory.values())
    result["file_count"] = len(inventory)
    result["original_size"] = total_original_bytes

    # Step 5b: Clean up any stale temporary archive file
    tmp_zip_path = get_temporary_archive_path(final_zip_path)
    tmp_zip_path.unlink(missing_ok=True)

    # Existing archive handling (Crash Recovery & Idempotence)
    if final_zip_path.exists():
        is_match, desc, _ = verify_archive_integrity(final_zip_path, expected_inventory=inventory, month_name=month_name, allow_subset=True)
        if is_match:
            # The archive already completely and accurately reflects the source folder!
            zip_sz = final_zip_path.stat().st_size
            saved_b = total_original_bytes - zip_sz
            saved_p = (saved_b / total_original_bytes * 100.0) if total_original_bytes > 0 else 0.0
            result["zip_size"] = zip_sz
            result["saved_bytes"] = saved_b
            result["saved_pct"] = saved_p

            if logger:
                logger.info(
                    f"Existing archive for {month_name} {year} ({final_zip_path.name}) is fully verified and matches source folder. "
                    f"Reusing archive idempotently."
                )

            # Resume interrupted source folder deletion if enabled
            if delete_source and (saved_p >= min_savings_pct or min_savings_pct <= 0):
                try:
                    shutil.rmtree(month_dir)
                    result["source_deleted"] = not month_dir.exists()
                    if logger:
                        logger.info(f"Resumed and completed source folder cleanup for {month_dir.name}.")
                except Exception as e:
                    if logger:
                        logger.error(f"Failed to remove source folder {month_dir}: {e}")
            result["status"] = "success"
            return result
        else:
            # Conflicting archive: do NOT blindly overwrite! Preserve both copies!
            err = (
                f"CONFLICT: Archive '{final_zip_path.name}' already exists but differs from source folder contents ({desc}). "
                f"Preserving existing archive and source files. Manual inspection required."
            )
            if logger:
                logger.error(err)
            result["status"] = "conflict"
            result["error"] = err
            return result

    # Step 6 & 7: Create temporary ZIP with standard ZIP_DEFLATED
    final_zip_path.parent.mkdir(parents=True, exist_ok=True)
    if logger:
        logger.info(
            f"Archiving {len(inventory)} newspapers from {month_name} {year} "
            f"({format_size(total_original_bytes)}) to temporary ZIP..."
        )

    t0 = time.perf_counter()
    try:
        with zipfile.ZipFile(tmp_zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
            for rel_p, meta in sorted(inventory.items()):
                arcname = f"{month_name}/{rel_p}"
                zf.write(meta["abs_path"], arcname=arcname)
    except Exception as e:
        tmp_zip_path.unlink(missing_ok=True)
        err = f"Failed to write temporary archive {tmp_zip_path.name}: {e}"
        if logger:
            logger.error(err)
        result["status"] = "failed"
        result["error"] = err
        return result

    duration = time.perf_counter() - t0

    # Steps 9-13: Comprehensive Verification
    is_valid, msg, _ = verify_archive_integrity(tmp_zip_path, expected_inventory=inventory, month_name=month_name)
    if not is_valid:
        tmp_zip_path.unlink(missing_ok=True)
        err = f"Archive integrity verification failed: {msg}. Source folder untouched."
        if logger:
            logger.error(err)
        result["status"] = "failed"
        result["error"] = err
        return result

    # Step 14: Atomic rename to final filename
    try:
        os.replace(tmp_zip_path, final_zip_path)
    except Exception as e:
        tmp_zip_path.unlink(missing_ok=True)
        err = f"Failed to atomically finalize archive {final_zip_path.name}: {e}"
        if logger:
            logger.error(err)
        result["status"] = "failed"
        result["error"] = err
        return result

    zip_size = final_zip_path.stat().st_size
    saved_bytes = total_original_bytes - zip_size
    saved_pct = (saved_bytes / total_original_bytes * 100.0) if total_original_bytes > 0 else 0.0

    result["zip_size"] = zip_size
    result["saved_bytes"] = saved_bytes
    result["saved_pct"] = saved_pct

    # Step 15 & 16: Source folder cleanup subject to Decision Rule
    if delete_source and saved_pct >= min_savings_pct:
        try:
            shutil.rmtree(month_dir)
            result["source_deleted"] = not month_dir.exists()
            if logger:
                logger.info(f"Verified archive finalized. Deleted original monthly folder: {month_dir.name}.")
        except Exception as e:
            if logger:
                logger.error(f"Failed to delete original folder {month_dir}: {e}")
    elif delete_source and saved_pct < min_savings_pct:
        if logger:
            logger.info(
                f"Source folder {month_dir.name} PRESERVED: compression savings ({saved_pct:.2f}%) "
                f"is below the required safety threshold ({min_savings_pct:.1f}%)."
            )
    else:
        if logger:
            logger.info(f"Source folder {month_dir.name} PRESERVED: automatic folder deletion is currently disabled.")

    # Storage accounting log
    compression_ratio = (zip_size / total_original_bytes * 100.0) if total_original_bytes > 0 else 100.0
    if logger:
        logger.info(
            f"=== STORAGE ACCOUNTING FOR {month_name} {year} ===\n"
            f"  - Target Archive:    {final_zip_path.name}\n"
            f"  - Files Archived:    {len(inventory)}\n"
            f"  - Original Size:     {total_original_bytes:,} bytes ({format_size(total_original_bytes)})\n"
            f"  - Resulting ZIP:     {zip_size:,} bytes ({format_size(zip_size)})\n"
            f"  - Compression Ratio: {compression_ratio:.2f}%\n"
            f"  - Space Saved:       {saved_bytes:,} bytes ({format_size(saved_bytes)}) [{saved_pct:.2f}%]\n"
            f"  - Integrity Check:   PASSED (CRC & SHA-256 matched 100%)\n"
            f"  - Time Taken:        {duration:.2f}s\n"
            f"  - Source Deleted:    {result['source_deleted']}\n"
            f"=================================================="
        )

    result["status"] = "success"
    return result


def is_date_in_monthly_archive(
    target_date: datetime.date,
    base_archive_dir: Path
) -> Tuple[bool, Optional[Path], int]:
    """
    Checks if an Ahmedabad edition for target_date is already present and valid
    inside a finalized monthly ZIP archive.

    Returns:
        (exists_and_valid: bool, zip_path: Optional[Path], estimated_pages: int)
    """
    year = target_date.year
    month = target_date.month
    month_name = INT_TO_MONTH_NAME.get(month, target_date.strftime("%B"))
    zip_path = get_monthly_archive_path(base_archive_dir, year, month, month_name)

    if not zip_path.exists() or not zipfile.is_zipfile(zip_path):
        return False, None, 0

    date_str = target_date.strftime("%Y-%m-%d")
    expected_filenames = [
        f"{month_name}/TOI_Ahmedabad_{date_str}.pdf",
        f"TOI_Ahmedabad_{date_str}.pdf"
    ]

    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            namelist = zf.namelist()
            matched_entry = None
            for candidate in expected_filenames:
                if candidate in namelist:
                    matched_entry = candidate
                    break

            if not matched_entry:
                return False, zip_path, 0

            info = zf.getinfo(matched_entry)
            if info.file_size < MIN_VALID_PDF_SIZE_BYTES:
                return False, zip_path, 0

            entry_bytes = zf.read(matched_entry)
            is_valid, pages, _ = validate_pdf_bytes(entry_bytes)
            if is_valid:
                return True, zip_path, pages
            return False, zip_path, 0
    except Exception:
        return False, None, 0


def add_pdf_to_monthly_archive(
    pdf_path: Path,
    target_date: datetime.date,
    base_archive_dir: Path,
    logger: Optional[logging.Logger] = None
) -> bool:
    """
    Safely recovers a newly downloaded historical Ahmedabad newspaper directly into an
    existing monthly ZIP archive. Does NOT recreate a permanent month folder.

    Order of operations:
    1. Validates the new PDF on disk.
    2. Builds a temporary ZIP (*.zip.tmp) alongside the existing archive.
    3. Copies all existing verified entries from the old ZIP.
    4. Inserts the newly recovered edition under MonthName/filename.pdf.
    5. Verifies integrity of all old entries (checksum equality) and the new entry.
    6. Atomically replaces the old ZIP.
    7. Only after full verification, removes the temporary recovered PDF.

    Returns True if successfully added and verified, False on failure (old ZIP untouched).
    """
    year = target_date.year
    month = target_date.month
    month_name = INT_TO_MONTH_NAME.get(month, target_date.strftime("%B"))
    zip_path = get_monthly_archive_path(base_archive_dir, year, month, month_name)

    if not zip_path.exists():
        if logger:
            logger.warning(f"Cannot add PDF to non-existent monthly archive: {zip_path.name}")
        return False

    # 1. Validate the recovered PDF file
    is_valid, pages, v_msg = validate_pdf_file(pdf_path)
    if not is_valid:
        if logger:
            logger.error(f"Cannot add invalid PDF to archive: {v_msg}")
        return False

    target_arcname = f"{month_name}/{pdf_path.name}"
    tmp_zip_path = get_temporary_archive_path(zip_path)
    tmp_zip_path.unlink(missing_ok=True)

    try:
        # 2. Read existing entries and their hashes
        existing_hashes: Dict[str, str] = {}
        with zipfile.ZipFile(zip_path, "r") as old_zf:
            for name in old_zf.namelist():
                if not name.endswith("/") and name != target_arcname:
                    b = old_zf.read(name)
                    existing_hashes[name] = hashlib.sha256(b).hexdigest()

        # 3. Create temporary ZIP
        with zipfile.ZipFile(tmp_zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as new_zf:
            # Copy all existing entries
            with zipfile.ZipFile(zip_path, "r") as old_zf:
                for name in old_zf.namelist():
                    if name != target_arcname:
                        info = old_zf.getinfo(name)
                        data = old_zf.read(name)
                        new_zf.writestr(info, data)

            # Write the new recovered PDF
            new_zf.write(pdf_path, arcname=target_arcname)

        # 4. Verify integrity of temporary ZIP
        with zipfile.ZipFile(tmp_zip_path, "r") as check_zf:
            if check_zf.testzip() is not None:
                raise ValueError("testzip() detected corruption in newly updated archive.")

            # Verify all previous entries retain identical SHA-256 hashes
            for name, expected_hash in existing_hashes.items():
                b = check_zf.read(name)
                if hashlib.sha256(b).hexdigest() != expected_hash:
                    raise ValueError(f"Previous entry '{name}' was altered during archive update.")

            # Verify new entry matches the recovered PDF on disk
            new_bytes = check_zf.read(target_arcname)
            expected_new_hash = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
            if hashlib.sha256(new_bytes).hexdigest() != expected_new_hash:
                raise ValueError("New entry in ZIP does not match source PDF checksum.")

        # 5. Atomically replace old ZIP
        os.replace(tmp_zip_path, zip_path)

        # 6. Safely remove the recovered PDF from disk now that it is permanently archived
        pdf_path.unlink(missing_ok=True)

        # Prune month folder if left empty
        month_dir = base_archive_dir / str(year) / month_name
        if month_dir.exists() and not any(month_dir.iterdir()):
            try:
                month_dir.rmdir()
            except Exception:
                pass

        if logger:
            logger.info(
                f"Successfully added recovered edition {pdf_path.name} ({pages} pages) "
                f"into verified archive {zip_path.name}!"
            )
        return True

    except Exception as e:
        tmp_zip_path.unlink(missing_ok=True)
        if logger:
            logger.error(f"Failed to update archive {zip_path.name} with recovered edition: {e}. Archive preserved.")
        return False


def process_monthly_archives(
    config: Config,
    logger: Optional[logging.Logger] = None,
    current_date: Optional[datetime.date] = None,
    force_delete: bool = False
) -> List[Dict[str, Any]]:
    """
    Main orchestrator for automatic monthly ZIP archiving.
    Acquires single-instance lock to prevent collision with daily downloader,
    identifies completed older months, verifies and creates ZIP archives,
    and updates logging and notifications.
    """
    if current_date is None:
        current_date = datetime.date.today()

    if not getattr(config, "monthly_archive_enabled", True):
        if logger:
            logger.info("Monthly archiving is disabled in config. Skipping.")
        return []

    # Acquire lock so archiver and downloader cannot collide
    lock = SingleInstanceLock(lock_file_path=config.lock_file)
    if not lock.acquire():
        if logger:
            logger.info("Another TOI process is running. Postponing monthly archiving check.")
        return []

    results: List[Dict[str, Any]] = []
    try:
        base_dir = config.base_archive_dir
        cleanup_temporary_archives(base_dir, logger=logger)

        completed_months = get_completed_months(base_dir, current_date=current_date)
        if not completed_months:
            if logger:
                logger.info(f"No completed months require archiving for {current_date.strftime('%B %Y')}.")
            return results

        if logger:
            logger.info(f"Identified {len(completed_months)} completed month(s) eligible for archiving.")

        delete_source = force_delete or getattr(config, "delete_source_after_archiving", True)
        min_savings = getattr(config, "min_compression_savings_pct", 0.0)

        notifier = NotificationManager(enabled=config.notifications_enabled)

        for year, month, month_name, month_dir in completed_months:
            res = archive_month_folder(
                month_dir=month_dir,
                year=year,
                month=month,
                month_name=month_name,
                base_archive_dir=base_dir,
                delete_source=delete_source,
                min_savings_pct=min_savings,
                current_date=current_date,
                logger=logger
            )
            results.append(res)

            # Notify user only when a new archive is successfully created
            if res.get("status") == "success" and res.get("saved_bytes", 0) > 0 and res.get("zip_path"):
                notifier.notify_archive_success(
                    zip_path=res["zip_path"],
                    month_name=month_name,
                    year=year,
                    files_count=res["file_count"],
                    saved_bytes=res["saved_bytes"],
                    logger=logger
                )

        return results
    finally:
        lock.release()


def run_archive_cli(argv: Optional[List[str]] = None) -> int:
    """CLI entrypoint for manual or Task Scheduler invocation."""
    parser = argparse.ArgumentParser(
        description="TOI Daily Downloader - Monthly ZIP Archiving & Storage Optimizer"
    )
    parser.add_argument("--config", type=str, default=None, help="Path to custom config.json")
    parser.add_argument("--check-only", action="store_true", help="List eligible months without archiving")
    parser.add_argument("--delete-source", action="store_true", help="Enable deletion of source folders after verification")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose debug logging")
    args = parser.parse_args(argv)

    cfg = load_config(Path(args.config) if args.config else None)
    log_level = "DEBUG" if args.verbose else cfg.log_level
    logger = setup_logger(cfg.logs_dir, log_level=log_level)

    logger.info("=== TOI Monthly Archiving Maintenance Run Started ===")

    if args.check_only:
        months = get_completed_months(cfg.base_archive_dir)
        print(f"Eligible completed months ({len(months)}):")
        for y, m, m_name, path in months:
            print(f"  - {y} {m_name}: {path}")
        return 0

    results = process_monthly_archives(
        config=cfg,
        logger=logger,
        force_delete=args.delete_source
    )

    success_count = sum(1 for r in results if r.get("status") == "success")
    logger.info(f"=== TOI Monthly Archiving Maintenance Finished: {success_count}/{len(results)} completed successfully ===")
    return 0


if __name__ == "__main__":
    sys.exit(run_archive_cli())
