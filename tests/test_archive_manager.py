"""
Comprehensive unit test suite for TOI Daily Monthly ZIP Archiving & Storage Optimization.
Verifies all 21 core requirements from Section 11:
1. Older completed month is archived.
2. Current month is never archived.
3. ZIP contains every expected file.
4. ZIP integrity validation succeeds.
5. SHA-256 hashes match original files.
6. Original folder deleted only after successful verification.
7. Failed ZIP creation leaves source files untouched.
8. Corrupted ZIP does not cause source deletion.
9. Existing matching ZIP handled idempotently.
10. Existing conflicting ZIP not silently overwritten.
11. Empty folders handled safely.
12. Interrupted cleanup can resume without data loss.
13. Different years and month boundaries work correctly.
14. January archives previous December under previous year.
15. Missing historical PDFs recovered into already archived month.
16. Updating archive does not lose, alter, or duplicate entries.
17. Valid existing newspaper never overwritten.
18. Downloader and archiver cannot corrupt each other (locking).
19. Installer registers archive task.
20. Uninstaller removes tasks without deleting archives.
21. No network request or browser launch required to archive.
"""

import datetime
import hashlib
import os
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch
from PIL import Image

from src.config import Config
from src.archive_manager import (
    get_completed_months,
    get_monthly_archive_path,
    inventory_month_folder,
    verify_archive_integrity,
    archive_month_folder,
    is_date_in_monthly_archive,
    add_pdf_to_monthly_archive,
    process_monthly_archives,
    cleanup_temporary_archives
)
from src.lock import SingleInstanceLock
from src.state_manager import StateManager
from src.history_manager import HistoryManager
from src.notification import NotificationManager
from src.downloader import Downloader
from src.main import recover_missed_days, find_last_archived_date_before


def make_dummy_pdf(file_path: Path, pages: int = 2) -> str:
    """Helper to create a realistic valid PDF (> 100KB) and return its sha256."""
    file_path.parent.mkdir(parents=True, exist_ok=True)
    noise = os.urandom(200 * 200 * 3)
    img1 = Image.frombytes("RGB", (200, 200), noise).resize((600, 800))
    imgs = [Image.frombytes("RGB", (200, 200), noise).resize((600, 800)) for _ in range(pages - 1)]
    img1.save(str(file_path), format="PDF", save_all=True, append_images=imgs, quality=85)

    data = file_path.read_bytes()
    return hashlib.sha256(data).hexdigest()


# 1. An older completed month is archived
def test_older_completed_month_is_archived(tmp_path: Path):
    base_dir = tmp_path / "TOI Daily"
    august_dir = base_dir / "2026" / "August"
    pdf1 = august_dir / "TOI_Ahmedabad_2026-08-01.pdf"
    make_dummy_pdf(pdf1, pages=2)

    current_date = datetime.date(2026, 10, 14)
    completed = get_completed_months(base_dir, current_date=current_date)
    assert len(completed) == 1
    year, month, month_name, m_path = completed[0]
    assert year == 2026 and month == 8 and month_name == "August"

    res = archive_month_folder(
        month_dir=m_path,
        year=year,
        month=month,
        month_name=month_name,
        base_archive_dir=base_dir,
        delete_source=False,
        current_date=current_date
    )
    assert res["status"] == "success"
    zip_path = res["zip_path"]
    assert zip_path.exists()
    assert zip_path.name == "2026-08_August.zip"


# 2. The current month is never archived
def test_current_month_is_never_archived(tmp_path: Path):
    base_dir = tmp_path / "TOI Daily"
    oct_dir = base_dir / "2026" / "October"
    pdf1 = oct_dir / "TOI_Ahmedabad_2026-10-01.pdf"
    make_dummy_pdf(pdf1, pages=2)

    current_date = datetime.date(2026, 10, 14)
    completed = get_completed_months(base_dir, current_date=current_date)
    # October 2026 is current month, so it must not be eligible!
    assert len(completed) == 0

    res = archive_month_folder(
        month_dir=oct_dir,
        year=2026,
        month=10,
        month_name="October",
        base_archive_dir=base_dir,
        delete_source=True,
        current_date=current_date
    )
    assert res["status"] == "skipped"
    assert oct_dir.exists()
    assert pdf1.exists()


# 3. The ZIP contains every expected file
def test_zip_contains_every_expected_file(tmp_path: Path):
    base_dir = tmp_path / "TOI Daily"
    sep_dir = base_dir / "2026" / "September"
    files = ["TOI_Ahmedabad_2026-09-01.pdf", "TOI_Ahmedabad_2026-09-02.pdf", "TOI_Ahmedabad_2026-09-03.pdf"]
    for f in files:
        make_dummy_pdf(sep_dir / f, pages=2)

    res = archive_month_folder(
        month_dir=sep_dir,
        year=2026,
        month=9,
        month_name="September",
        base_archive_dir=base_dir,
        delete_source=False,
        current_date=datetime.date(2026, 10, 1)
    )
    assert res["status"] == "success"
    zip_path = res["zip_path"]

    with zipfile.ZipFile(zip_path, "r") as zf:
        namelist = zf.namelist()
        for f in files:
            expected_arc = f"September/{f}"
            assert expected_arc in namelist


# 4. ZIP integrity validation succeeds
def test_zip_integrity_validation_succeeds(tmp_path: Path):
    base_dir = tmp_path / "TOI Daily"
    sep_dir = base_dir / "2026" / "September"
    p = sep_dir / "TOI_Ahmedabad_2026-09-01.pdf"
    make_dummy_pdf(p, pages=2)

    res = archive_month_folder(
        month_dir=sep_dir,
        year=2026,
        month=9,
        month_name="September",
        base_archive_dir=base_dir,
        delete_source=False,
        current_date=datetime.date(2026, 10, 1)
    )
    inv, _ = inventory_month_folder(sep_dir)
    is_valid, msg, stats = verify_archive_integrity(res["zip_path"], expected_inventory=inv, month_name="September")
    assert is_valid is True
    assert stats["entry_count"] == 1


# 5. SHA-256 hashes of archived files match the original files
def test_sha256_hashes_match_original_files(tmp_path: Path):
    base_dir = tmp_path / "TOI Daily"
    sep_dir = base_dir / "2026" / "September"
    hashes = {}
    for day in range(1, 4):
        p = sep_dir / f"TOI_Ahmedabad_2026-09-0{day}.pdf"
        h = make_dummy_pdf(p, pages=2)
        hashes[f"September/{p.name}"] = h

    res = archive_month_folder(
        month_dir=sep_dir,
        year=2026,
        month=9,
        month_name="September",
        base_archive_dir=base_dir,
        delete_source=False,
        current_date=datetime.date(2026, 10, 1)
    )
    with zipfile.ZipFile(res["zip_path"], "r") as zf:
        for arcname, expected_hash in hashes.items():
            b = zf.read(arcname)
            assert hashlib.sha256(b).hexdigest() == expected_hash


# 6. The original folder is deleted only after successful verification
def test_original_folder_deleted_only_after_successful_verification(tmp_path: Path):
    base_dir = tmp_path / "TOI Daily"
    sep_dir = base_dir / "2026" / "September"
    make_dummy_pdf(sep_dir / "TOI_Ahmedabad_2026-09-01.pdf", pages=2)

    res = archive_month_folder(
        month_dir=sep_dir,
        year=2026,
        month=9,
        month_name="September",
        base_archive_dir=base_dir,
        delete_source=True,
        min_savings_pct=0.0,  # Explicitly allow deletion in test
        current_date=datetime.date(2026, 10, 1)
    )
    assert res["status"] == "success"
    assert res["source_deleted"] is True
    assert not sep_dir.exists()
    assert res["zip_path"].exists()


# 7. A failed ZIP creation leaves source files untouched
def test_failed_zip_creation_leaves_source_files_untouched(tmp_path: Path):
    base_dir = tmp_path / "TOI Daily"
    sep_dir = base_dir / "2026" / "September"
    pdf = sep_dir / "TOI_Ahmedabad_2026-09-01.pdf"
    orig_hash = make_dummy_pdf(pdf, pages=2)

    with patch("zipfile.ZipFile.write", side_effect=IOError("Simulated disk error")):
        res = archive_month_folder(
            month_dir=sep_dir,
            year=2026,
            month=9,
            month_name="September",
            base_archive_dir=base_dir,
            delete_source=True,
            min_savings_pct=0.0,
            current_date=datetime.date(2026, 10, 1)
        )
        assert res["status"] == "failed"

    # Source files must be 100% untouched
    assert sep_dir.exists()
    assert pdf.exists()
    assert hashlib.sha256(pdf.read_bytes()).hexdigest() == orig_hash
    # Temporary zip must not remain
    tmp_zip = base_dir / "2026" / "2026-09_September.zip.tmp"
    assert not tmp_zip.exists()


# 8. A corrupted ZIP does not cause source deletion
def test_corrupted_zip_does_not_cause_source_deletion(tmp_path: Path):
    base_dir = tmp_path / "TOI Daily"
    sep_dir = base_dir / "2026" / "September"
    pdf = sep_dir / "TOI_Ahmedabad_2026-09-01.pdf"
    make_dummy_pdf(pdf, pages=2)

    with patch("src.archive_manager.verify_archive_integrity", return_value=(False, "Simulated CRC failure", {})):
        res = archive_month_folder(
            month_dir=sep_dir,
            year=2026,
            month=9,
            month_name="September",
            base_archive_dir=base_dir,
            delete_source=True,
            min_savings_pct=0.0,
            current_date=datetime.date(2026, 10, 1)
        )
        assert res["status"] == "failed"

    assert sep_dir.exists()
    assert pdf.exists()


# 9. An existing matching ZIP is handled idempotently
def test_existing_matching_zip_handled_idempotently(tmp_path: Path):
    base_dir = tmp_path / "TOI Daily"
    sep_dir = base_dir / "2026" / "September"
    make_dummy_pdf(sep_dir / "TOI_Ahmedabad_2026-09-01.pdf", pages=2)

    # First run creates archive
    res1 = archive_month_folder(
        month_dir=sep_dir,
        year=2026,
        month=9,
        month_name="September",
        base_archive_dir=base_dir,
        delete_source=False,
        current_date=datetime.date(2026, 10, 1)
    )
    assert res1["status"] == "success"

    # Second run checks existing archive
    res2 = archive_month_folder(
        month_dir=sep_dir,
        year=2026,
        month=9,
        month_name="September",
        base_archive_dir=base_dir,
        delete_source=False,
        current_date=datetime.date(2026, 10, 1)
    )
    assert res2["status"] == "success"
    assert res2["zip_path"].exists()


# 10. An existing conflicting ZIP is not silently overwritten
def test_existing_conflicting_zip_not_silently_overwritten(tmp_path: Path):
    base_dir = tmp_path / "TOI Daily"
    sep_dir = base_dir / "2026" / "September"
    pdf = sep_dir / "TOI_Ahmedabad_2026-09-01.pdf"
    make_dummy_pdf(pdf, pages=2)

    # Pre-create a conflicting ZIP with completely different contents
    zip_path = base_dir / "2026" / "2026-09_September.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("September/different_file.pdf", b"%PDF-1.4 " + b"0" * 110000)

    orig_zip_bytes = zip_path.read_bytes()

    res = archive_month_folder(
        month_dir=sep_dir,
        year=2026,
        month=9,
        month_name="September",
        base_archive_dir=base_dir,
        delete_source=True,
        min_savings_pct=0.0,
        current_date=datetime.date(2026, 10, 1)
    )
    assert res["status"] == "conflict"
    # Both copies must be preserved intact!
    assert zip_path.read_bytes() == orig_zip_bytes
    assert sep_dir.exists()
    assert pdf.exists()


# 11. Empty folders are handled safely
def test_empty_folders_handled_safely(tmp_path: Path):
    base_dir = tmp_path / "TOI Daily"
    empty_month = base_dir / "2026" / "July"
    empty_month.mkdir(parents=True, exist_ok=True)

    res = archive_month_folder(
        month_dir=empty_month,
        year=2026,
        month=7,
        month_name="July",
        base_archive_dir=base_dir,
        delete_source=True,
        current_date=datetime.date(2026, 10, 1)
    )
    assert res["status"] == "empty"
    assert not empty_month.exists()
    zip_path = base_dir / "2026" / "2026-07_July.zip"
    assert not zip_path.exists()


# 12. Interrupted cleanup can resume without data loss
def test_interrupted_cleanup_resumes_safely(tmp_path: Path):
    base_dir = tmp_path / "TOI Daily"
    sep_dir = base_dir / "2026" / "September"
    pdf1 = sep_dir / "TOI_Ahmedabad_2026-09-01.pdf"
    pdf2 = sep_dir / "TOI_Ahmedabad_2026-09-02.pdf"
    make_dummy_pdf(pdf1, pages=2)
    make_dummy_pdf(pdf2, pages=2)

    # First archive without deleting
    res1 = archive_month_folder(
        month_dir=sep_dir,
        year=2026,
        month=9,
        month_name="September",
        base_archive_dir=base_dir,
        delete_source=False,
        current_date=datetime.date(2026, 10, 1)
    )
    assert res1["status"] == "success"

    # Simulate crash midway through deleting: pdf1 was deleted, but pdf2 remains
    pdf1.unlink()
    assert sep_dir.exists()
    assert pdf2.exists()

    # Next run with deletion enabled should safely recognize verified archive and resume cleanup
    res2 = archive_month_folder(
        month_dir=sep_dir,
        year=2026,
        month=9,
        month_name="September",
        base_archive_dir=base_dir,
        delete_source=True,
        min_savings_pct=0.0,
        current_date=datetime.date(2026, 10, 1)
    )
    assert res2["status"] == "success"
    assert not sep_dir.exists()
    # Verified archive remains intact with both files
    with zipfile.ZipFile(res1["zip_path"], "r") as zf:
        assert "September/TOI_Ahmedabad_2026-09-01.pdf" in zf.namelist()
        assert "September/TOI_Ahmedabad_2026-09-02.pdf" in zf.namelist()


# 13. Different years and month boundaries work correctly
def test_different_years_and_month_boundaries_work_correctly(tmp_path: Path):
    base_dir = tmp_path / "TOI Daily"
    # Leap year Feb 2024
    feb_2024 = base_dir / "2024" / "February"
    make_dummy_pdf(feb_2024 / "TOI_Ahmedabad_2024-02-29.pdf", pages=2)

    # 30-day month April 2025
    apr_2025 = base_dir / "2025" / "April"
    make_dummy_pdf(apr_2025 / "TOI_Ahmedabad_2025-04-30.pdf", pages=2)

    # Active month
    may_2025 = base_dir / "2025" / "May"
    make_dummy_pdf(may_2025 / "TOI_Ahmedabad_2025-05-15.pdf", pages=2)

    completed = get_completed_months(base_dir, current_date=datetime.date(2025, 5, 20))
    # Must find 2024-02 and 2025-04, but NOT 2025-05
    months_found = [(y, m) for y, m, _, _ in completed]
    assert (2024, 2) in months_found
    assert (2025, 4) in months_found
    assert (2025, 5) not in months_found


# 14. January archives the previous December under the previous year
def test_january_archives_previous_december_under_previous_year(tmp_path: Path):
    base_dir = tmp_path / "TOI Daily"
    dec_dir = base_dir / "2026" / "December"
    make_dummy_pdf(dec_dir / "TOI_Ahmedabad_2026-12-31.pdf", pages=2)

    current_date = datetime.date(2027, 1, 5)
    completed = get_completed_months(base_dir, current_date=current_date)
    assert len(completed) == 1
    year, month, month_name, m_path = completed[0]
    assert year == 2026 and month == 12 and month_name == "December"

    res = archive_month_folder(
        month_dir=m_path,
        year=year,
        month=month,
        month_name=month_name,
        base_archive_dir=base_dir,
        delete_source=False,
        current_date=current_date
    )
    assert res["status"] == "success"
    # Saved beside monthly folder, inside the 2026 directory: Desktop\TOI Daily\2026\2026-12_December.zip
    expected_path = base_dir / "2026" / "2026-12_December.zip"
    assert res["zip_path"] == expected_path
    assert expected_path.exists()


# 15. Missing historical PDFs can still be recovered into an already archived month
def test_missing_historical_pdf_recovered_into_archived_month(tmp_path: Path):
    base_dir = tmp_path / "TOI Daily"
    sep_dir = base_dir / "2026" / "September"
    p1 = sep_dir / "TOI_Ahmedabad_2026-09-01.pdf"
    make_dummy_pdf(p1, pages=2)

    # Archive September
    res = archive_month_folder(
        month_dir=sep_dir,
        year=2026,
        month=9,
        month_name="September",
        base_archive_dir=base_dir,
        delete_source=True,
        min_savings_pct=0.0,
        current_date=datetime.date(2026, 10, 1)
    )
    assert res["status"] == "success"
    zip_path = res["zip_path"]
    assert zip_path.exists()
    assert not sep_dir.exists()

    # Recover newly available September 5 edition
    recovered_temp = tmp_path / "temp_download" / "TOI_Ahmedabad_2026-09-05.pdf"
    make_dummy_pdf(recovered_temp, pages=3)

    added = add_pdf_to_monthly_archive(
        pdf_path=recovered_temp,
        target_date=datetime.date(2026, 9, 5),
        base_archive_dir=base_dir
    )
    assert added is True
    # Temp file unlinked
    assert not recovered_temp.exists()
    # Permanent September folder was NOT recreated
    assert not sep_dir.exists()

    # Verify ZIP contains both 09-01 and 09-05
    with zipfile.ZipFile(zip_path, "r") as zf:
        namelist = zf.namelist()
        assert "September/TOI_Ahmedabad_2026-09-01.pdf" in namelist
        assert "September/TOI_Ahmedabad_2026-09-05.pdf" in namelist


# 16. Updating an archive does not lose, alter, or duplicate existing entries
def test_updating_archive_does_not_lose_or_duplicate_entries(tmp_path: Path):
    base_dir = tmp_path / "TOI Daily"
    sep_dir = base_dir / "2026" / "September"
    h1 = make_dummy_pdf(sep_dir / "TOI_Ahmedabad_2026-09-01.pdf", pages=2)
    h2 = make_dummy_pdf(sep_dir / "TOI_Ahmedabad_2026-09-02.pdf", pages=2)

    res = archive_month_folder(
        month_dir=sep_dir,
        year=2026,
        month=9,
        month_name="September",
        base_archive_dir=base_dir,
        delete_source=True,
        min_savings_pct=0.0,
        current_date=datetime.date(2026, 10, 1)
    )
    zip_path = res["zip_path"]

    # Add 09-03
    temp3 = tmp_path / "TOI_Ahmedabad_2026-09-03.pdf"
    h3 = make_dummy_pdf(temp3, pages=2)
    assert add_pdf_to_monthly_archive(temp3, datetime.date(2026, 9, 3), base_dir)

    with zipfile.ZipFile(zip_path, "r") as zf:
        names = zf.namelist()
        assert len(names) == 3
        # Check no duplicates
        assert len(names) == len(set(names))
        # Check original hashes retained
        assert hashlib.sha256(zf.read("September/TOI_Ahmedabad_2026-09-01.pdf")).hexdigest() == h1
        assert hashlib.sha256(zf.read("September/TOI_Ahmedabad_2026-09-02.pdf")).hexdigest() == h2
        assert hashlib.sha256(zf.read("September/TOI_Ahmedabad_2026-09-03.pdf")).hexdigest() == h3


# 17. A valid existing newspaper is never overwritten
def test_valid_existing_newspaper_never_overwritten(tmp_path: Path):
    base_dir = tmp_path / "TOI Daily"
    sep_dir = base_dir / "2026" / "September"
    pdf = sep_dir / "TOI_Ahmedabad_2026-09-01.pdf"
    orig_hash = make_dummy_pdf(pdf, pages=2)

    res = archive_month_folder(
        month_dir=sep_dir,
        year=2026,
        month=9,
        month_name="September",
        base_archive_dir=base_dir,
        delete_source=True,
        min_savings_pct=0.0,
        current_date=datetime.date(2026, 10, 1)
    )

    # Check is_date_in_monthly_archive detects it
    in_arch, z_path, pages = is_date_in_monthly_archive(datetime.date(2026, 9, 1), base_dir)
    assert in_arch is True
    assert z_path == res["zip_path"]
    assert pages >= 1

    # Verify backfill skips downloading when paper is already in monthly ZIP
    state_file = tmp_path / "state.json"
    history_file = tmp_path / "history.json"
    state_mgr = StateManager(state_file)
    history_mgr = HistoryManager(history_file)
    notifier = NotificationManager(enabled=False)
    logger = MagicMock()

    with patch.object(Config, "base_archive_dir", new_callable=lambda: property(lambda self: base_dir)):
        cfg = Config({}, config_path=tmp_path / "config.json")
        downloader = MagicMock(spec=Downloader)

        results = recover_missed_days(
            target_date=datetime.date(2026, 9, 2),
            config=cfg,
            downloader=downloader,
            state_mgr=state_mgr,
            history_mgr=history_mgr,
            notifier=notifier,
            logger=logger,
            days=1
        )
        assert results["existing_valid"] == 1
        # Downloader must NEVER be called!
        assert downloader.download_daily_paper.call_count == 0


# 18. A downloader and archiver cannot corrupt each other's operations (locking)
def test_downloader_and_archiver_cannot_corrupt_each_other(tmp_path: Path):
    lock_file = tmp_path / "toi.lock"
    base_dir = tmp_path / "TOI Daily"

    # Simulate downloader currently running and holding the lock
    downloader_lock = SingleInstanceLock(mutex_name="Local\\TOI_Test_Mutex_Arch", lock_file_path=lock_file)
    assert downloader_lock.acquire() is True

    try:
        with patch.object(Config, "base_archive_dir", new_callable=lambda: property(lambda self: base_dir)), \
             patch.object(Config, "lock_file", new_callable=lambda: property(lambda self: lock_file)):
            cfg = Config({}, config_path=tmp_path / "config.json")

            # Archiver attempts to run while downloader holds lock
            arch_lock = SingleInstanceLock(mutex_name="Local\\TOI_Test_Mutex_Arch", lock_file_path=lock_file)
            assert arch_lock.acquire() is False

            # process_monthly_archives must cleanly exit without throwing
            results = process_monthly_archives(cfg)
            assert results == []
    finally:
        downloader_lock.release()


# 19. The installer registers the archive task
def test_installer_registers_archive_task():
    ps1_path = Path(__file__).resolve().parent.parent / "register_task.ps1"
    assert ps1_path.exists()
    content = ps1_path.read_text(encoding="utf-8")

    assert "TOI_Monthly_Archive" in content
    assert "run_archive.vbs" in content
    assert "-StartWhenAvailable" in content
    assert "06:30AM" in content


# 20. The uninstaller removes tasks without deleting archives
def test_uninstaller_removes_tasks_without_deleting_archives():
    bat_path = Path(__file__).resolve().parent.parent / "uninstall.bat"
    assert bat_path.exists()
    content = bat_path.read_text(encoding="utf-8")

    assert "TOI_Monthly_Archive" in content
    assert "TOI_Daily_Downloader" in content
    # Crucial safety: uninstaller must NOT delete the TOI Daily archive folder
    assert "rmdir" not in content.lower()
    for line in content.splitlines():
        line_clean = line.strip().lower()
        if line_clean.startswith("del") or line_clean.startswith("rmdir") or "remove-item" in line_clean:
            assert "toi daily" not in line_clean and "base_archive" not in line_clean


# 21. No network request or browser launch is required to archive files
def test_no_network_or_browser_required_to_archive(tmp_path: Path):
    base_dir = tmp_path / "TOI Daily"
    sep_dir = base_dir / "2026" / "September"
    make_dummy_pdf(sep_dir / "TOI_Ahmedabad_2026-09-01.pdf", pages=2)

    # Mock socket and urllib/requests to ensure any network call raises an exception
    with patch("socket.socket", side_effect=RuntimeError("Network access forbidden")), \
         patch("urllib.request.urlopen", side_effect=RuntimeError("Network access forbidden")):

        res = archive_month_folder(
            month_dir=sep_dir,
            year=2026,
            month=9,
            month_name="September",
            base_archive_dir=base_dir,
            delete_source=False,
            current_date=datetime.date(2026, 10, 1)
        )
        assert res["status"] == "success"
        assert res["zip_path"].exists()


# Additional test: find_last_archived_date_before detects dates inside monthly ZIP
def test_find_last_archived_date_before_inspects_monthly_zips(tmp_path: Path):
    base_dir = tmp_path / "TOI Daily"
    zip_path = base_dir / "2026" / "2026-08_August.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("August/TOI_Ahmedabad_2026-08-25.pdf", b"%PDF-1.4 " + b"0" * 110000)

    state_file = tmp_path / "state.json"
    state_mgr = StateManager(state_file)

    with patch.object(Config, "base_archive_dir", new_callable=lambda: property(lambda self: base_dir)):
        cfg = Config({}, config_path=tmp_path / "config.json")
        last_date = find_last_archived_date_before(datetime.date(2026, 9, 1), cfg, state_mgr)
        assert last_date == datetime.date(2026, 8, 25)
