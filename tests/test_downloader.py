"""
Tests for Downloader engine, Ahmedabad-only enforcement, and temp isolation.
"""

import datetime
import os
from pathlib import Path
from unittest.mock import patch, MagicMock
from PIL import Image
from src.config import Config
from src.downloader import Downloader
from src.logger import setup_logger


def test_filename_convention(tmp_path: Path):
    with patch.object(Config, "base_archive_dir", new_callable=lambda: property(lambda self: tmp_path / "TOI Daily")):
        config = Config({"edition": "Ahmedabad"})
        logger = setup_logger(tmp_path / "logs", "DEBUG")
        downloader = Downloader(config, logger)

        target_date = datetime.date(2026, 8, 27)
        pdf_path = downloader.get_target_pdf_path(target_date)
        assert pdf_path.name == "TOI_Ahmedabad_2026-08-27.pdf"
        assert "August" in str(pdf_path)


def test_strict_ahmedabad_enforcement(tmp_path: Path):
    with patch.object(Config, "base_archive_dir", new_callable=lambda: property(lambda self: tmp_path / "TOI Daily")):
        config = Config({"edition": "Ahmedabad"})
        logger = setup_logger(tmp_path / "logs", "DEBUG")
        downloader = Downloader(config, logger)

        target_date = datetime.date(2026, 8, 27)
        success, path, pages, dur, err = downloader.download_daily_paper(target_date, edition="Delhi")
        assert not success
        assert path is None
        assert "STRICT POLICY VIOLATION" in err


def test_existing_valid_file_skips_download(tmp_path: Path):
    with patch.object(Config, "base_archive_dir", new_callable=lambda: property(lambda self: tmp_path / "TOI Daily")):
        config = Config({"edition": "Ahmedabad", "retry_count": 1})
        logger = setup_logger(tmp_path / "logs", "DEBUG")
        downloader = Downloader(config, logger)

        target_date = datetime.date(2026, 8, 27)
        target_file = downloader.get_target_pdf_path(target_date)
        target_file.parent.mkdir(parents=True, exist_ok=True)

        # Create realistic multi-page PDF (> 100KB)
        noise = os.urandom(300 * 300 * 3)
        img1 = Image.frombytes("RGB", (300, 300), noise).resize((1000, 1400))
        img2 = Image.frombytes("RGB", (300, 300), noise).resize((1000, 1400))
        img1.save(str(target_file), format="PDF", save_all=True, append_images=[img2], quality=95)

        # Should detect existing valid file without making network calls
        success, path, pages, dur, err = downloader.download_daily_paper(target_date, "Ahmedabad")
        assert success
        assert path == target_file
        assert pages >= 2
        assert err is None


@patch("src.downloader.Downloader._fetch_ahmedabad_edition_images")
@patch("src.downloader.check_internet_connection", return_value=True)
def test_download_failure_and_temp_cleanup(mock_net, mock_fetch, tmp_path: Path):
    with patch.object(Config, "base_archive_dir", new_callable=lambda: property(lambda self: tmp_path / "TOI Daily")), \
         patch.object(Config, "temp_dir", new_callable=lambda: property(lambda self: tmp_path / "temp")):
        
        config = Config({
            "edition": "Ahmedabad",
            "retry_count": 2,
            "retry_delay_seconds": 0
        })
        logger = setup_logger(tmp_path / "logs", "DEBUG")
        downloader = Downloader(config, logger)

        mock_fetch.side_effect = RuntimeError("Server connection refused")

        target_date = datetime.date(2026, 8, 27)
        temp_part_file = tmp_path / "temp" / "TOI_Ahmedabad_2026-08-27.pdf.part"

        success, path, pages, dur, err = downloader.download_daily_paper(target_date, "Ahmedabad")
        assert not success
        assert path is None
        assert "Server connection refused" in err
        assert not temp_part_file.exists()


def test_resolve_api_base_url_from_js(tmp_path: Path):
    config = Config({"edition": "Ahmedabad"})
    logger = setup_logger(tmp_path / "logs", "DEBUG")
    downloader = Downloader(config, logger)

    fake_js_content = 'const apiUrl = "https://d309t8g1g9oksh.cloudfront.net/toi/v1/download";'
    with patch.object(downloader.session, "get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = fake_js_content
        mock_get.return_value = mock_resp

        discovered = downloader._resolve_api_base_url()
        assert discovered == "https://d309t8g1g9oksh.cloudfront.net"


def test_resolve_api_base_url_fallback_on_error(tmp_path: Path):
    config = Config({"edition": "Ahmedabad"})
    logger = setup_logger(tmp_path / "logs", "DEBUG")
    downloader = Downloader(config, logger)

    with patch.object(downloader.session, "get", side_effect=Exception("Network error")):
        resolved = downloader._resolve_api_base_url()
        assert "cloudfront.net" in resolved


def test_downloader_session_headers(tmp_path: Path):
    config = Config({"edition": "Ahmedabad"})
    logger = setup_logger(tmp_path / "logs", "DEBUG")
    downloader = Downloader(config, logger)

    assert "indupaper.com" in downloader.session.headers.get("Referer", "")
    assert "indupaper.com" in downloader.session.headers.get("Origin", "")

