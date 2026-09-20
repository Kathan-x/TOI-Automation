"""
Tests for automatic recent-day backfill in TOI Daily Downloader.
Verifies:
1. Sweeps the previous 7 calendar days.
2. Existing valid PDFs are strictly preserved and NEVER overwritten.
3. Missing or corrupted Ahmedabad PDFs are downloaded and archived in Year/Month structure.
4. Failed downloads remain retryable (never marked as success).
5. Strictly Ahmedabad edition only.
6. Backfill results do not regress today's top-level state.
"""

import datetime
import hashlib
import logging
import os
from pathlib import Path
from unittest.mock import MagicMock, patch
from PIL import Image

from src.config import Config
from src.downloader import Downloader
from src.history_manager import HistoryManager
from src.main import backfill_recent_days
from src.notification import NotificationManager
from src.state_manager import StateManager
from src.validator import validate_pdf_file


def create_dummy_valid_pdf(file_path: Path, pages: int = 2) -> str:
    """Creates a valid multi-page PDF on disk and returns its sha256 hash."""
    file_path.parent.mkdir(parents=True, exist_ok=True)
    noise = os.urandom(200 * 200 * 3)
    img1 = Image.frombytes("RGB", (200, 200), noise).resize((400, 600))
    imgs = [Image.frombytes("RGB", (200, 200), noise).resize((400, 600)) for _ in range(pages - 1)]
    img1.save(str(file_path), format="PDF", save_all=True, append_images=imgs, quality=80)
    return hashlib.sha256(file_path.read_bytes()).hexdigest()


def test_backfill_sweeps_previous_7_calendar_days(tmp_path: Path):
    """Verifies backfill sweeps exactly the previous 7 calendar days."""
    archive_dir = tmp_path / "TOI Daily"
    state_file = tmp_path / "state.json"
    history_file = tmp_path / "history.json"

    with patch.object(Config, "base_archive_dir", new_callable=lambda: property(lambda self: archive_dir)):
        config = Config({}, config_path=tmp_path / "config.json")

        logger = logging.getLogger("test_backfill")
        state_mgr = StateManager(state_file)
        history_mgr = HistoryManager(history_file)
        notifier = NotificationManager(enabled=False)

        downloader = MagicMock(spec=Downloader)
        downloader.download_daily_paper.return_value = (False, None, 0, 0.1, "HTTP 400")

        target_date = datetime.date(2026, 9, 7)
        results = backfill_recent_days(
            target_date=target_date,
            config=config,
            downloader=downloader,
            state_mgr=state_mgr,
            history_mgr=history_mgr,
            notifier=notifier,
            logger=logger,
            days=7
        )

        assert results["checked"] == 7
        # Verify downloader was called for each of the 7 previous dates
        expected_dates = [target_date - datetime.timedelta(days=i) for i in range(1, 8)]
        called_dates = [c[0][0] for c in downloader.download_daily_paper.call_args_list]
        assert called_dates == expected_dates
        # Verify strictly Ahmedabad edition was passed
        for c in downloader.download_daily_paper.call_args_list:
            assert c[0][1] == "Ahmedabad"


def test_backfill_never_overwrites_existing_valid_pdf(tmp_path: Path):
    """Verifies existing valid PDFs are never overwritten or modified."""
    archive_dir = tmp_path / "TOI Daily"
    state_file = tmp_path / "state.json"
    history_file = tmp_path / "history.json"

    with patch.object(Config, "base_archive_dir", new_callable=lambda: property(lambda self: archive_dir)):
        config = Config({}, config_path=tmp_path / "config.json")

        logger = logging.getLogger("test_backfill")
        state_mgr = StateManager(state_file)
        history_mgr = HistoryManager(history_file)
        notifier = NotificationManager(enabled=False)

        target_date = datetime.date(2026, 9, 7)

        # Place existing valid PDFs for Sept 4, Sept 3, Sept 2, Sept 1
        existing_hashes = {}
        for day in [4, 3, 2, 1]:
            d = datetime.date(2026, 9, day)
            p = config.get_target_pdf_path(d)
            h = create_dummy_valid_pdf(p, pages=2)
            existing_hashes[d] = h

        downloader = MagicMock(spec=Downloader)
        downloader.download_daily_paper.return_value = (False, None, 0, 0.1, "HTTP 400")

        results = backfill_recent_days(
            target_date=target_date,
            config=config,
            downloader=downloader,
            state_mgr=state_mgr,
            history_mgr=history_mgr,
            notifier=notifier,
            logger=logger,
            days=7
        )

        # 4 existing valid files should be detected
        assert results["existing_valid"] == 4

        # Verify that downloader was NEVER called for the dates that already existed!
        called_dates = [c[0][0] for c in downloader.download_daily_paper.call_args_list]
        for day in [4, 3, 2, 1]:
            d = datetime.date(2026, 9, day)
            assert d not in called_dates, f"Downloader should NOT be called for existing {d}"

        # Verify existing files are byte-for-byte untouched
        for d, original_hash in existing_hashes.items():
            current_path = config.get_target_pdf_path(d)
            current_hash = hashlib.sha256(current_path.read_bytes()).hexdigest()
            assert current_hash == original_hash, f"File for {d} was unexpectedly modified!"


def test_backfill_recovers_missing_days_and_preserves_structure(tmp_path: Path):
    """Verifies missing days are downloaded and saved in YYYY\\MonthName archive."""
    archive_dir = tmp_path / "TOI Daily"
    state_file = tmp_path / "state.json"
    history_file = tmp_path / "history.json"

    with patch.object(Config, "base_archive_dir", new_callable=lambda: property(lambda self: archive_dir)):
        config = Config({}, config_path=tmp_path / "config.json")

        logger = logging.getLogger("test_backfill")
        state_mgr = StateManager(state_file)
        history_mgr = HistoryManager(history_file)
        notifier = NotificationManager(enabled=False)

        target_date = datetime.date(2026, 9, 3)

        def mock_download(dt, ed):
            pdf_path = config.get_target_pdf_path(dt)
            create_dummy_valid_pdf(pdf_path, pages=3)
            return True, pdf_path, 3, 0.5, None

        downloader = MagicMock(spec=Downloader)
        downloader.download_daily_paper.side_effect = mock_download

        results = backfill_recent_days(
            target_date=target_date,
            config=config,
            downloader=downloader,
            state_mgr=state_mgr,
            history_mgr=history_mgr,
            notifier=notifier,
            logger=logger,
            days=7
        )

        assert results["recovered"] == 7

        # Verify files created in correct month folders:
        # 2026-09-02 and 2026-09-01 should be in September
        sept_p = archive_dir / "2026" / "September" / "TOI_Ahmedabad_2026-09-02.pdf"
        assert sept_p.exists()
        assert state_mgr.is_date_completed("2026-09-02")

        # 2026-08-31 down to 2026-08-27 should be in August
        aug_p = archive_dir / "2026" / "August" / "TOI_Ahmedabad_2026-08-31.pdf"
        assert aug_p.exists()
        assert state_mgr.is_date_completed("2026-08-31")


def test_backfill_failed_downloads_remain_retryable(tmp_path: Path):
    """Verifies failed backfill attempts are never marked as success and remain retryable."""
    archive_dir = tmp_path / "TOI Daily"
    state_file = tmp_path / "state.json"
    history_file = tmp_path / "history.json"

    with patch.object(Config, "base_archive_dir", new_callable=lambda: property(lambda self: archive_dir)):
        config = Config({}, config_path=tmp_path / "config.json")

        logger = logging.getLogger("test_backfill")
        state_mgr = StateManager(state_file)
        history_mgr = HistoryManager(history_file)
        notifier = NotificationManager(enabled=False)

        target_date = datetime.date(2026, 9, 7)

        downloader = MagicMock(spec=Downloader)
        downloader.download_daily_paper.return_value = (False, None, 0, 0.1, "HTTP 400 paper not published")

        results = backfill_recent_days(
            target_date=target_date,
            config=config,
            downloader=downloader,
            state_mgr=state_mgr,
            history_mgr=history_mgr,
            notifier=notifier,
            logger=logger,
            days=7
        )

        assert results["failed"] == 7
        # None of the failed dates should be marked completed
        for i in range(1, 8):
            dt = (target_date - datetime.timedelta(days=i)).strftime("%Y-%m-%d")
            assert not state_mgr.is_date_completed(dt), f"{dt} must NOT be marked completed"

        state = state_mgr.load_state()
        # History must record the failed attempt
        assert state["history"]["2026-09-06"]["status"] == "failed"
        assert "HTTP 400" in state["history"]["2026-09-06"]["error"]


def test_backfill_replaces_corrupted_file(tmp_path: Path):
    """Verifies that an invalid/corrupted PDF is recognized, cleaned up, and recovered."""
    archive_dir = tmp_path / "TOI Daily"
    state_file = tmp_path / "state.json"
    history_file = tmp_path / "history.json"

    with patch.object(Config, "base_archive_dir", new_callable=lambda: property(lambda self: archive_dir)):
        config = Config({}, config_path=tmp_path / "config.json")

        logger = logging.getLogger("test_backfill")
        state_mgr = StateManager(state_file)
        history_mgr = HistoryManager(history_file)
        notifier = NotificationManager(enabled=False)

        target_date = datetime.date(2026, 9, 7)

        # Place corrupted (truncated) PDF for 2026-09-06
        corrupted_pdf = config.get_target_pdf_path(datetime.date(2026, 9, 6))
        corrupted_pdf.parent.mkdir(parents=True, exist_ok=True)
        corrupted_pdf.write_bytes(b"corrupted truncated header")

        def mock_download(dt, ed):
            pdf_path = config.get_target_pdf_path(dt)
            create_dummy_valid_pdf(pdf_path, pages=4)
            return True, pdf_path, 4, 0.5, None

        downloader = MagicMock(spec=Downloader)
        downloader.download_daily_paper.side_effect = mock_download

        results = backfill_recent_days(
            target_date=target_date,
            config=config,
            downloader=downloader,
            state_mgr=state_mgr,
            history_mgr=history_mgr,
            notifier=notifier,
            logger=logger,
            days=1
        )

        assert results["recovered"] == 1
        is_valid, pages, _ = validate_pdf_file(corrupted_pdf)
        assert is_valid is True
        assert pages == 4


def test_dynamic_recovery_model_from_last_archived_date(tmp_path: Path):
    """
    Verifies dynamic recovery model:
    If last success = 2026-09-04 and today = 2026-09-20,
    it inspects all missing dates: 2026-09-05 to 2026-09-19.
    2026-09-05 remains failed/retryable (HTTP 400),
    2026-09-06 is recovered.
    """
    archive_dir = tmp_path / "TOI Daily"
    state_file = tmp_path / "state.json"
    history_file = tmp_path / "history.json"

    with patch.object(Config, "base_archive_dir", new_callable=lambda: property(lambda self: archive_dir)):
        config = Config({"max_backfill_days": 31}, config_path=tmp_path / "config.json")

        logger = logging.getLogger("test_dynamic_backfill")
        state_mgr = StateManager(state_file)
        history_mgr = HistoryManager(history_file)
        notifier = NotificationManager(enabled=False)

        # 1. Simulate last success on 2026-09-04
        p_sep4 = config.get_target_pdf_path(datetime.date(2026, 9, 4))
        create_dummy_valid_pdf(p_sep4, pages=18)
        state_mgr.record_success("2026-09-04", p_sep4, page_count=18, size_bytes=1000000)

        # Target date is 2026-09-20
        target_date = datetime.date(2026, 9, 20)

        # Downloader mock: 2026-09-05 fails (HTTP 400), others succeed
        def mock_download(dt, ed):
            assert ed == "Ahmedabad"
            if dt == datetime.date(2026, 9, 5):
                return False, None, 0, 0.2, "Paper not published yet (HTTP 400)"
            pdf_path = config.get_target_pdf_path(dt)
            create_dummy_valid_pdf(pdf_path, pages=14)
            return True, pdf_path, 14, 0.4, None

        downloader = MagicMock(spec=Downloader)
        downloader.download_daily_paper.side_effect = mock_download

        from src.main import recover_missed_days
        results = recover_missed_days(
            target_date=target_date,
            config=config,
            downloader=downloader,
            state_mgr=state_mgr,
            history_mgr=history_mgr,
            notifier=notifier,
            logger=logger
        )

        # Should check 15 days (2026-09-05 to 2026-09-19)
        assert results["checked"] == 15
        assert results["failed"] == 1  # 2026-09-05
        assert results["recovered"] == 14  # 2026-09-06 to 2026-09-19

        # 2026-09-05 must NOT be marked complete and must have NO fabricated PDF
        sep5_pdf = config.get_target_pdf_path(datetime.date(2026, 9, 5))
        assert not sep5_pdf.exists(), "Must never fabricate missing edition"
        assert not state_mgr.is_date_completed("2026-09-05")

        # 2026-09-06 must be marked complete and exist
        sep6_pdf = config.get_target_pdf_path(datetime.date(2026, 9, 6))
        assert sep6_pdf.exists()
        assert state_mgr.is_date_completed("2026-09-06")


def test_dynamic_recovery_safety_cap(tmp_path: Path):
    """Verifies that the recovery window respects safety cap max_backfill_days."""
    archive_dir = tmp_path / "TOI Daily"
    state_file = tmp_path / "state.json"
    history_file = tmp_path / "history.json"

    with patch.object(Config, "base_archive_dir", new_callable=lambda: property(lambda self: archive_dir)):
        config = Config({"max_backfill_days": 10}, config_path=tmp_path / "config.json")

        logger = logging.getLogger("test_cap")
        state_mgr = StateManager(state_file)
        history_mgr = HistoryManager(history_file)
        notifier = NotificationManager(enabled=False)

        # Last success was 50 days ago
        target_date = datetime.date(2026, 9, 20)
        old_date = target_date - datetime.timedelta(days=50)
        p_old = config.get_target_pdf_path(old_date)
        create_dummy_valid_pdf(p_old, pages=10)
        state_mgr.record_success(old_date.strftime("%Y-%m-%d"), p_old, 10, 500000)

        downloader = MagicMock(spec=Downloader)
        downloader.download_daily_paper.return_value = (False, None, 0, 0.1, "Unavailable")

        from src.main import recover_missed_days
        results = recover_missed_days(
            target_date=target_date,
            config=config,
            downloader=downloader,
            state_mgr=state_mgr,
            history_mgr=history_mgr,
            notifier=notifier,
            logger=logger
        )

        # Safety cap is 10 days, so at most 10 days are checked
        assert results["checked"] == 10

