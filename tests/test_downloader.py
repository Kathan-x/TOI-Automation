"""
Tests for Downloader engine, Ahmedabad-only enforcement, source discovery, and multi-strategy fallbacks.
"""

import datetime
import io
import os
from pathlib import Path
from unittest.mock import patch, MagicMock
from PIL import Image

from src.config import Config
from src.downloader import Downloader
from src.logger import setup_logger


def _create_sample_image() -> bytes:
    img = Image.new("RGB", (200, 200), color="blue")
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


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


def test_resolve_api_base_url_from_html_fallback(tmp_path: Path):
    config = Config({"edition": "Ahmedabad"})
    logger = setup_logger(tmp_path / "logs", "DEBUG")
    downloader = Downloader(config, logger)

    fake_html = '<html><script src="https://d309t8g1g9oksh.cloudfront.net/main.js"></script></html>'
    def mock_get(url, timeout=None):
        resp = MagicMock()
        if "TOI.js" in url:
            resp.status_code = 404
            resp.text = ""
        else:
            resp.status_code = 200
            resp.text = fake_html
        return resp

    with patch.object(downloader.session, "get", side_effect=mock_get):
        discovered = downloader._resolve_api_base_url()
        assert "cloudfront.net" in discovered


def test_strategy_v3_success(tmp_path: Path):
    config = Config({"edition": "Ahmedabad"})
    logger = setup_logger(tmp_path / "logs", "DEBUG")
    downloader = Downloader(config, logger)
    downloader._api_base_url = "https://d309t8g1g9oksh.cloudfront.net"

    fake_img_bytes = _create_sample_image()
    v3_p1_response = {
        "status": "success",
        "data": {
            "totalPage": 3,
            "page": 1,
            "htmlContent": '<div style="width:100%"><img src="https://img.server/page1.jpg"></div>'
        }
    }
    v3_p2_response = {
        "status": "success",
        "data": {
            "totalPage": 3,
            "page": 2,
            "htmlContent": '<div style="width:100%"><img src="https://img.server/page2.jpg"></div>'
        }
    }
    v3_p3_response = {
        "status": "success",
        "data": {
            "totalPage": 3,
            "page": 3,
            "htmlContent": '<div style="width:100%"><img src="https://img.server/page3.jpg"></div>'
        }
    }

    def mock_get(url, timeout=None):
        resp = MagicMock()
        if "page=1" in url:
            resp.status_code = 200
            resp.json.return_value = v3_p1_response
        elif "page=2" in url:
            resp.status_code = 200
            resp.json.return_value = v3_p2_response
        elif "page=3" in url:
            resp.status_code = 200
            resp.json.return_value = v3_p3_response
        else:
            resp.status_code = 200
            resp.content = fake_img_bytes
        return resp

    with patch.object(downloader.session, "get", side_effect=mock_get):
        images = downloader._fetch_ahmedabad_edition_images(datetime.date(2026, 9, 20))
        assert len(images) == 3


def test_strategy_v3_base64_data_uri(tmp_path: Path):
    import base64
    config = Config({"edition": "Ahmedabad"})
    logger = setup_logger(tmp_path / "logs", "DEBUG")
    downloader = Downloader(config, logger)
    downloader._api_base_url = "https://d309t8g1g9oksh.cloudfront.net"

    fake_img_bytes = _create_sample_image()
    b64_str = base64.b64encode(fake_img_bytes).decode("ascii")
    v3_b64_response = {
        "status": "success",
        "data": {
            "totalPage": 1,
            "page": 1,
            "htmlContent": f'<div style="width:100%"><img src="data:image/jpeg;base64,{b64_str}"></div>'
        }
    }

    def mock_get(url, timeout=None):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = v3_b64_response
        return resp

    with patch.object(downloader.session, "get", side_effect=mock_get):
        images = downloader._fetch_ahmedabad_edition_images(datetime.date(2026, 9, 20))
        assert len(images) == 1
        assert images[0].size == (200, 200)


def test_strategy_v3_zero_pages_error(tmp_path: Path):
    config = Config({"edition": "Ahmedabad"})
    logger = setup_logger(tmp_path / "logs", "DEBUG")
    downloader = Downloader(config, logger)
    downloader._api_base_url = "https://d309t8g1g9oksh.cloudfront.net"

    v3_empty = {
        "status": "success",
        "data": {
            "totalPage": 0,
            "htmlContent": ""
        }
    }

    def mock_get(url, timeout=None):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = v3_empty
        return resp

    with patch.object(downloader.session, "get", side_effect=mock_get):
        try:
            downloader._fetch_ahmedabad_edition_images(datetime.date(2026, 9, 20))
            assert False, "Should raise exception for 0 total pages"
        except RuntimeError as e:
            assert "0 total pages" in str(e)


def test_downloader_session_headers(tmp_path: Path):
    config = Config({"edition": "Ahmedabad"})
    logger = setup_logger(tmp_path / "logs", "DEBUG")
    downloader = Downloader(config, logger)

    assert "indupaper.com" in downloader.session.headers.get("Referer", "")
    assert "indupaper.com" in downloader.session.headers.get("Origin", "")


def test_unpublished_paper_http_400_handling(tmp_path: Path):
    config = Config({"edition": "Ahmedabad"})
    logger = setup_logger(tmp_path / "logs", "DEBUG")
    downloader = Downloader(config, logger)
    downloader._api_base_url = "https://d309t8g1g9oksh.cloudfront.net"

    def mock_get(url, timeout=None):
        resp = MagicMock()
        resp.status_code = 400
        resp.text = '{"status":"error","data":null,"message":"BAD REQUEST"}'
        return resp

    with patch.object(downloader.session, "get", side_effect=mock_get):
        try:
            downloader._fetch_ahmedabad_edition_images(datetime.date(2026, 9, 5))
            assert False, "Should raise RuntimeError for HTTP 400"
        except RuntimeError as e:
            assert "not published yet" in str(e).lower()

