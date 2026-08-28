"""
Core Downloader Engine for TOI Daily (v2.0).
- Strictly downloads the Ahmedabad edition.
- Organizes PDFs into smart Year/Month hierarchy: Desktop\\TOI Daily\\YYYY\\MonthName\\
- Isolates temporary files in %LOCALAPPDATA%\\TOI-Daily\\temp.
- Checks internet connectivity before attempting downloads.
- Measures download duration for permanent history records.
"""

import base64
import datetime
import io
import logging
import os
import re
import shutil
import time
from pathlib import Path
from typing import List, Optional, Tuple
import requests
from PIL import Image

from .config import Config
from .net_checker import check_internet_connection
from .cleanup import cleanup_temp_files
from .validator import validate_pdf_file, safe_cleanup_corrupted_file


class Downloader:
    def __init__(self, config: Config, logger: logging.Logger):
        self.config = config
        self.logger = logger
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/html, */*",
            "Referer": "https://www.indupaper.com/times-of-india.html"
        })

    def get_target_pdf_path(self, target_date: datetime.date) -> Path:
        """Returns the full smart path: Desktop\\TOI Daily\\YYYY\\MonthName\\TOI_Ahmedabad_YYYY-MM-DD.pdf"""
        return self.config.get_target_pdf_path(target_date)

    def download_daily_paper(
        self,
        target_date: datetime.date,
        edition: str = "Ahmedabad"
    ) -> Tuple[bool, Optional[Path], int, float, Optional[str]]:
        """
        Downloads today's Times of India Ahmedabad edition with retry backoff.

        Returns:
            (success: bool, final_path: Optional[Path], page_count: int, duration_sec: float, error_msg: Optional[str])
        """
        start_time = time.time()

        # Strict Ahmedabad enforcement
        if edition.strip().lower() != self.config.REQUIRED_CITY_SLUG:
            err = (
                f"STRICT POLICY VIOLATION: Requested edition '{edition}' is prohibited. "
                f"This system is strictly configured for '{self.config.REQUIRED_EDITION}' only."
            )
            self.logger.error(err)
            return False, None, 0, 0.0, err

        final_pdf_path = self.get_target_pdf_path(target_date)
        filename = final_pdf_path.name
        temp_part_path = self.config.temp_dir / f"{filename}.part"

        # 1. Fast Check: Skip if already exists and valid in Year/Month archive
        if final_pdf_path.exists():
            is_valid, pages, msg = validate_pdf_file(final_pdf_path)
            if is_valid:
                self.logger.info(f"Existing valid Ahmedabad newspaper found at {final_pdf_path} ({pages} pages). Skipping download.")
                return True, final_pdf_path, pages, 0.0, None
            else:
                self.logger.warning(f"Existing file at {final_pdf_path} is corrupted: {msg}. Replacing...")
                safe_cleanup_corrupted_file(final_pdf_path, self.logger)

        # 2. Cleanup orphan temp files
        cleanup_temp_files(self.config.temp_dir, max_age_hours=24.0, logger=self.logger)

        # 3. Fast Internet Connectivity Check
        self.logger.info("Checking internet connection before starting download...")
        if not check_internet_connection(timeout_seconds=3.0, retries=3, delay_between_retries=3.0, logger=self.logger):
            err = "No internet connection available. Download will retry automatically when connection is restored."
            self.logger.warning(err)
            return False, None, 0, time.time() - start_time, err

        # 4. Retry loop
        last_error = None
        for attempt in range(1, self.config.retry_count + 1):
            self.logger.info(f"Download attempt {attempt}/{self.config.retry_count} for Ahmedabad edition on {target_date}...")

            try:
                safe_cleanup_corrupted_file(temp_part_path, self.logger)

                images = self._fetch_ahmedabad_edition_images(target_date)
                if not images:
                    raise RuntimeError("No readable Ahmedabad edition pages were returned by the server.")

                self.logger.info(f"Successfully retrieved {len(images)} Ahmedabad page images. Compiling PDF...")
                self._save_images_as_pdf(images, temp_part_path)

                # Validate the compiled PDF in temp location
                is_valid, pages, val_msg = validate_pdf_file(temp_part_path)
                if not is_valid:
                    raise ValueError(f"Compiled PDF failed integrity validation: {val_msg}")

                # Move validated PDF from temp to smart Year/Month archive folder
                final_pdf_path.parent.mkdir(parents=True, exist_ok=True)
                if final_pdf_path.exists():
                    final_pdf_path.unlink()

                shutil.move(str(temp_part_path), str(final_pdf_path))
                duration = time.time() - start_time

                self.logger.info(
                    f"Download and validation successful ({duration:.1f}s)! "
                    f"Saved to permanent archive: {final_pdf_path} ({pages} pages)"
                )
                return True, final_pdf_path, pages, duration, None

            except Exception as e:
                last_error = str(e)
                self.logger.error(f"Attempt {attempt} failed: {e}")
                safe_cleanup_corrupted_file(temp_part_path, self.logger)

                if attempt < self.config.retry_count:
                    sleep_time = self.config.retry_delay_seconds * (2 ** (attempt - 1))
                    self.logger.info(f"Waiting {sleep_time}s before next retry...")
                    time.sleep(sleep_time)

        duration = time.time() - start_time
        return False, None, 0, duration, f"All {self.config.retry_count} attempts failed. Last error: {last_error}"

    def _fetch_ahmedabad_edition_images(self, target_date: datetime.date) -> List[Image.Image]:
        """
        Fetches all page images for the Ahmedabad edition from CloudFront API.
        """
        day = f"{target_date.day:02d}"
        month = f"{target_date.month:02d}"
        year = f"{target_date.year}"
        city_slug = self.config.REQUIRED_CITY_SLUG

        # 1. Try v2 endpoint
        try:
            url_v2 = (
                f"https://d1h47qec6ptx2j.cloudfront.net/toi/v2/download"
                f"?citySlug={city_slug}&day={day}&month={month}&year={year}"
            )
            self.logger.debug(f"Querying v2 metadata for Ahmedabad: {url_v2}")
            resp = self.session.get(url_v2, timeout=self.config.request_timeout_seconds)
            if resp.status_code == 200:
                data = resp.json()
                html = data.get("data", {}).get("htmlContent", "")
                img_srcs = re.findall(r'<img[^>]+src=["\']([^"\']+)["\']', html)

                is_placeholder = any("download-steps" in src for src in img_srcs)
                if img_srcs and not is_placeholder:
                    self.logger.info(f"v2 metadata returned {len(img_srcs)} Ahmedabad page sources.")
                    images = self._download_image_list(img_srcs)
                    if images:
                        return images
        except Exception as e:
            self.logger.debug(f"v2 fetch encountered error: {e}. Falling back to v1...")

        # 2. Fallback to v1 endpoint
        url_v1_base = (
            f"https://d1h47qec6ptx2j.cloudfront.net/toi/v1/download"
            f"?citySlug={city_slug}&day={day}&month={month}&year={year}"
        )
        self.logger.debug(f"Querying v1 endpoint for Ahmedabad: {url_v1_base}&page=1")
        resp1 = self.session.get(f"{url_v1_base}&page=1", timeout=self.config.request_timeout_seconds)
        resp1.raise_for_status()
        data1 = resp1.json()

        total_pages = int(data1.get("data", {}).get("totalPage", 1))
        self.logger.info(f"v1 reports {total_pages} total pages for Ahmedabad edition.")

        images = []
        page1_html = data1.get("data", {}).get("htmlContent", "")
        img1 = self._parse_image_from_html(page1_html)
        if img1:
            images.append(img1)

        for page_num in range(2, total_pages + 1):
            page_url = f"{url_v1_base}&page={page_num}"
            self.logger.debug(f"Fetching v1 page {page_num}/{total_pages}...")
            p_resp = self.session.get(page_url, timeout=self.config.request_timeout_seconds)
            if p_resp.status_code == 200:
                p_data = p_resp.json()
                p_html = p_data.get("data", {}).get("htmlContent", "")
                p_img = self._parse_image_from_html(p_html)
                if p_img:
                    images.append(p_img)

        return images

    def _download_image_list(self, img_urls: List[str]) -> List[Image.Image]:
        """Downloads a list of image URLs (or decodes base64) into PIL Images."""
        images = []
        for idx, src in enumerate(img_urls, start=1):
            self.logger.debug(f"Retrieving Ahmedabad page {idx}/{len(img_urls)}...")
            img = self._load_image_from_source(src)
            if img:
                images.append(img)
            else:
                self.logger.warning(f"Could not load image for page {idx}")
        return images

    def _parse_image_from_html(self, html_snippet: str) -> Optional[Image.Image]:
        """Extracts the <img> tag from an HTML snippet and loads it."""
        match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', html_snippet)
        if match:
            return self._load_image_from_source(match.group(1))
        return None

    def _load_image_from_source(self, src: str) -> Optional[Image.Image]:
        """Loads a PIL Image from either a data URI base64 string or an HTTP URL."""
        if src.startswith("data:image"):
            try:
                base64_data = src.split(",", 1)[1]
                img_bytes = base64.b64decode(base64_data)
                img = Image.open(io.BytesIO(img_bytes))
                if img.mode != "RGB":
                    img = img.convert("RGB")
                return img
            except Exception as e:
                self.logger.warning(f"Failed to decode base64 image: {e}")
                return None

        try:
            img_resp = self.session.get(src, timeout=self.config.request_timeout_seconds)
            img_resp.raise_for_status()
            img = Image.open(io.BytesIO(img_resp.content))
            if img.mode != "RGB":
                img = img.convert("RGB")
            return img
        except Exception as e:
            # Fallback to weserv proxy
            try:
                import urllib.parse
                proxy_url = f"https://images.weserv.nl/?url={urllib.parse.quote(src)}"
                p_resp = self.session.get(proxy_url, timeout=self.config.request_timeout_seconds)
                p_resp.raise_for_status()
                img = Image.open(io.BytesIO(p_resp.content))
                if img.mode != "RGB":
                    img = img.convert("RGB")
                return img
            except Exception as pe:
                self.logger.warning(f"Failed to download image from {src}: {e} (Proxy fallback error: {pe})")
                return None

    def _save_images_as_pdf(self, images: List[Image.Image], output_path: Path) -> None:
        """Saves a sequence of PIL images into a clean multi-page PDF."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if not images:
            raise ValueError("Cannot create PDF from empty image list")

        first = images[0]
        rest = images[1:] if len(images) > 1 else []
        first.save(
            str(output_path),
            format="PDF",
            save_all=True,
            append_images=rest,
            resolution=150.0
        )
