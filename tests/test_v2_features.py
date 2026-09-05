"""
Unit tests for Version 2.0 Premium Features:
- Smart Year/Month Folder Organization (Pure path resolution & no premature directory creation)
- Empty Archive Folder Pruning
- HistoryManager (history.json)
- Fast Internet Connectivity Checker
- Automatic Temporary File Cleanup & Legacy Organization
- Self-Healing Configuration
"""

import datetime
import json
import time
from pathlib import Path
from unittest.mock import patch, MagicMock
from PIL import Image

from src.config import Config, load_config
from src.history_manager import HistoryManager, format_size
from src.net_checker import check_internet_connection
from src.cleanup import cleanup_temp_files, organize_legacy_desktop_files, prune_empty_archive_folders
from src.downloader import Downloader
from src.logger import setup_logger
from src.validator import validate_pdf_file


def test_year_month_folder_hierarchy_does_not_create_directories(tmp_path: Path):
    # Isolate using tmp_path
    with patch.object(Config, "base_archive_dir", new_callable=lambda: property(lambda self: tmp_path / "TOI Daily")):
        config = Config({"edition": "Ahmedabad"})
        
        # Test path calculation for future years/months
        future_date_2027 = datetime.date(2027, 1, 1)
        target_path_2027 = config.get_target_pdf_path(future_date_2027)
        
        expected_2027_folder = tmp_path / "TOI Daily" / "2027" / "January"
        assert target_path_2027.parent == expected_2027_folder
        assert target_path_2027.name == "TOI_Ahmedabad_2027-01-01.pdf"
        
        # CRITICAL CHECK: Pure calculation must NOT create the directory on disk!
        assert not expected_2027_folder.exists()
        assert not (tmp_path / "TOI Daily" / "2027").exists()


def test_config_download_dir_alias(tmp_path: Path):
    with patch.object(Config, "base_archive_dir", new_callable=lambda: property(lambda self: tmp_path / "TOI Daily")):
        config = Config({"edition": "Ahmedabad"})
        assert config.download_dir == tmp_path / "TOI Daily"
        assert config.download_dir == config.base_archive_dir


def test_prune_empty_archive_folders(tmp_path: Path):
    archive_dir = tmp_path / "TOI Daily"
    
    # Create empty future folders
    empty_2027 = archive_dir / "2027" / "January"
    empty_2028 = archive_dir / "2028" / "February"
    empty_2027.mkdir(parents=True, exist_ok=True)
    empty_2028.mkdir(parents=True, exist_ok=True)
    
    # Create a real folder with a file
    real_2026 = archive_dir / "2026" / "August"
    real_2026.mkdir(parents=True, exist_ok=True)
    (real_2026 / "TOI_Ahmedabad_2026-08-27.pdf").write_bytes(b"pdf data")
    
    # Run pruner
    pruned = prune_empty_archive_folders(archive_dir)
    assert pruned >= 4
    
    # Empty folders must be gone
    assert not (archive_dir / "2027").exists()
    assert not (archive_dir / "2028").exists()
    
    # Real folder and file must remain
    assert real_2026.exists()
    assert (real_2026 / "TOI_Ahmedabad_2026-08-27.pdf").exists()


def test_legacy_archive_organization(tmp_path: Path):
    base_archive = tmp_path / "TOI Daily"
    base_archive.mkdir(parents=True, exist_ok=True)
    
    # Create flat legacy file
    legacy_file = base_archive / "TOI_Ahmedabad_2026-08-27.pdf"
    legacy_file.write_bytes(b"dummy pdf content")
    
    # Run organizer
    moved = organize_legacy_desktop_files(base_archive)
    assert moved == 1
    assert not legacy_file.exists()
    
    organized_file = base_archive / "2026" / "August" / "TOI_Ahmedabad_2026-08-27.pdf"
    assert organized_file.exists()
    assert organized_file.read_bytes() == b"dummy pdf content"


def test_history_manager_recording(tmp_path: Path):
    history_file = tmp_path / "history.json"
    mgr = HistoryManager(history_file)
    dummy_pdf = tmp_path / "test.pdf"
    dummy_pdf.write_bytes(b"dummy")

    mgr.record_entry(
        date_str="2026-08-27",
        edition="Ahmedabad",
        status="success",
        file_path=dummy_pdf,
        page_count=18,
        size_bytes=35000000,
        duration_seconds=12.5
    )

    records = mgr.load_history()
    assert len(records) == 1
    rec = records[0]
    assert rec["download_date"] == "2026-08-27"
    assert rec["status"] == "success"
    assert rec["page_count"] == 18
    assert rec["duration_seconds"] == 12.5
    assert "MB" in rec["size_formatted"]


def test_format_size_helper():
    assert format_size(500) == "500 B"
    assert "KB" in format_size(10240)
    assert "MB" in format_size(35 * 1024 * 1024)


def test_cleanup_temp_files(tmp_path: Path):
    temp_dir = tmp_path / "temp"
    temp_dir.mkdir(parents=True, exist_ok=True)

    part_file = temp_dir / "download.pdf.part"
    part_file.write_bytes(b"partial data")

    old_file = temp_dir / "old_scrap.bin"
    old_file.write_bytes(b"old")
    past_time = time.time() - (48 * 3600)
    import os
    os.utime(str(old_file), (past_time, past_time))

    cleaned = cleanup_temp_files(temp_dir, max_age_hours=24.0)
    assert cleaned >= 2
    assert not part_file.exists()
    assert not old_file.exists()


def test_self_healing_config_regeneration(tmp_path: Path):
    bad_config = tmp_path / "config.json"
    bad_config.write_text("{invalid_json: true", encoding="utf-8")

    cfg = load_config(bad_config)
    assert cfg.edition == "Ahmedabad"
    assert cfg.retry_count == 3
    with open(bad_config, "r", encoding="utf-8") as f:
        data = json.load(f)
        assert data["edition"] == "Ahmedabad"


@patch("urllib.request.urlopen")
def test_internet_checker_online(mock_urlopen):
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.__enter__.return_value = mock_resp
    mock_urlopen.return_value = mock_resp
    assert check_internet_connection(timeout_seconds=0.5, retries=1)


@patch("socket.socket")
@patch("urllib.request.urlopen", side_effect=Exception("Connection refused"))
def test_internet_checker_offline(mock_urlopen, mock_socket):
    mock_instance = MagicMock()
    import socket
    mock_instance.connect.side_effect = socket.timeout("timed out")
    mock_socket.return_value = mock_instance
    assert not check_internet_connection(timeout_seconds=0.1, retries=1)
