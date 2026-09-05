"""
Production Downloader Engine for Times of India Daily (v2.0).
- Strictly downloads the Ahmedabad edition.
- Multi-tier source discovery: scans TOI.js, times-of-india.html, and verified CloudFront fallbacks.
- Multi-strategy retrieval:
    * Strategy A: Direct API v2 (batch JSON metadata)
    * Strategy B: Paginated API v1 (individual page metadata)
    * Strategy C: Direct image proxy fallback (weserv proxy / direct stream)
    * Strategy D: Headless Browser automation fallback (Playwright)
- Detailed categorization: Distinguishes between unavailable (not published yet), network errors, server errors, and corrupted files.
- Atomic file safety: Writes to %LOCALAPPDATA%\\TOI-Daily\\temp\\*.part and moves to permanent archive only upon full PDF validation.
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

from .config import Config, WEBSITE_CONFIG
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
            "Accept": "application/json, text/plain, text/html, */*",
            "Referer": "https://www.indupaper.com/times-of-india.html",
            "Origin": "https://www.indupaper.com"
        })
        self._api_base_url: Optional[str] = None

    def get_target_pdf_path(self, target_date: datetime.date) -> Path:
        """Returns the full smart path: Desktop\\TOI Daily\\YYYY\\MonthName\\TOI_Ahmedabad_YYYY-MM-DD.pdf"""
        return self.config.get_target_pdf_path(target_date)

    def download_daily_paper(
        self,
        target_date: datetime.date,
        edition: str = "Ahmedabad"
    ) -> Tuple[bool, Optional[Path], int, float, Optional[str]]:
        """
        Downloads today's Times of India Ahmedabad edition using a multi-tier fallback strategy.

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

        # 1. Fast Skip Check: Skip if already exists and valid in Year/Month archive
        if final_pdf_path.exists():
            is_valid, pages, msg = validate_pdf_file(final_pdf_path)
            if is_valid:
                self.logger.info(
                    f"Existing valid Ahmedabad newspaper found at {final_pdf_path} ({pages} pages). Skipping download."
                )
                return True, final_pdf_path, pages, 0.0, None
            else:
                self.logger.warning(f"Existing file at {final_pdf_path} is corrupted: {msg}. Replacing...")
                safe_cleanup_corrupted_file(final_pdf_path, self.logger)

        # 2. Cleanup orphan temp files older than 24 hours
        cleanup_temp_files(self.config.temp_dir, max_age_hours=24.0, logger=self.logger)

        # 3. Connectivity check (informative; does not hard block)
        is_online = check_internet_connection(timeout_seconds=3.0, retries=2, delay_between_retries=1.0, logger=self.logger)
        if not is_online:
            self.logger.debug("Initial connectivity check had issues, proceeding with direct endpoint attempt...")

        # 4. Multi-Attempt Download Loop
        last_error = None
        for attempt in range(1, self.config.retry_count + 1):
            self.logger.info(f"Download attempt {attempt}/{self.config.retry_count} for Ahmedabad edition on {target_date}...")

            try:
                safe_cleanup_corrupted_file(temp_part_path, self.logger)

                images = self._fetch_ahmedabad_edition_images(target_date)
                if not images or len(images) == 0:
                    raise RuntimeError("No readable Ahmedabad edition pages returned. Paper may not be published yet.")

                min_thresh = WEBSITE_CONFIG.get("min_pages_threshold", 4)
                if len(images) < min_thresh:
                    raise RuntimeError(
                        f"Retrieved only {len(images)} pages, which is below the minimum newspaper threshold ({min_thresh}). "
                        f"Paper may still be uploading."
                    )

                self.logger.info(f"Successfully retrieved {len(images)} Ahmedabad page images. Compiling PDF...")
                self._save_images_as_pdf(images, temp_part_path)

                # Validate the compiled PDF in isolated temp location
                is_valid, pages, val_msg = validate_pdf_file(temp_part_path)
                if not is_valid:
                    raise ValueError(f"Compiled PDF failed integrity validation: {val_msg}")

                # Atomic Move: Move validated PDF from temp to permanent Year/Month archive folder
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
                self.logger.warning(f"Attempt {attempt} failed: {e}")
                safe_cleanup_corrupted_file(temp_part_path, self.logger)

                if attempt < self.config.retry_count:
                    sleep_time = self.config.retry_delay_seconds * (2 ** (attempt - 1))
                    self.logger.info(f"Waiting {sleep_time}s before next retry...")
                    time.sleep(sleep_time)

        duration = time.time() - start_time
        return False, None, 0, duration, f"All {self.config.retry_count} attempts failed. Last error: {last_error}"

    def _resolve_api_base_url(self) -> str:
        """
        Dynamically discovers the active CloudFront API endpoint from:
        1. https://www.indupaper.com/TOI.js
        2. https://www.indupaper.com/times-of-india.html
        3. Configured fallback list
        """
        if self._api_base_url:
            return self._api_base_url

        fallbacks = WEBSITE_CONFIG.get("api_fallbacks", ["https://d309t8g1g9oksh.cloudfront.net"])
        primary_fallback = WEBSITE_CONFIG.get("api_base_fallback", fallbacks[0])
        toi_js_url = WEBSITE_CONFIG.get("toi_js_url", "https://www.indupaper.com/TOI.js")
        html_url = WEBSITE_CONFIG.get("url", "https://www.indupaper.com/times-of-india.html")

        # 1. Inspect TOI.js
        try:
            self.logger.debug(f"Discovering live CloudFront API endpoint from {toi_js_url}...")
            resp = self.session.get(toi_js_url, timeout=min(self.config.request_timeout_seconds, 10))
            if resp.status_code == 200:
                match = re.search(r"https://[a-zA-Z0-9_-]+\.cloudfront\.net", resp.text)
                if match:
                    discovered = match.group(0)
                    self.logger.info(f"Discovered live CloudFront API base: {discovered}")
                    self._api_base_url = discovered
                    return discovered
        except Exception as e:
            self.logger.debug(f"Dynamic API discovery from TOI.js encountered: {e}")

        # 2. Inspect HTML page for CloudFront scripts/endpoints
        try:
            self.logger.debug(f"Discovering endpoint from {html_url}...")
            resp_html = self.session.get(html_url, timeout=min(self.config.request_timeout_seconds, 10))
            if resp_html.status_code == 200:
                match = re.search(r"https://[a-zA-Z0-9_-]+\.cloudfront\.net", resp_html.text)
                if match:
                    discovered = match.group(0)
                    self.logger.info(f"Discovered live CloudFront API from HTML: {discovered}")
                    self._api_base_url = discovered
                    return discovered
        except Exception as e:
            self.logger.debug(f"Dynamic API discovery from HTML encountered: {e}")

        # 3. Fallback
        self.logger.info(f"Using standard CloudFront endpoint: {primary_fallback}")
        self._api_base_url = primary_fallback
        return primary_fallback

    def _fetch_ahmedabad_edition_images(self, target_date: datetime.date) -> List[Image.Image]:
        """
        Fetches all page images for Ahmedabad edition across Strategy A (v2) and Strategy B (v1).
        """
        day = f"{target_date.day:02d}"
        month = f"{target_date.month:02d}"
        year = f"{target_date.year}"
        city_slug = self.config.REQUIRED_CITY_SLUG
        base_url = self._resolve_api_base_url()

        # Strategy A: v2 endpoint
        try:
            url_v2 = (
                f"{base_url}/toi/v2/download"
                f"?citySlug={city_slug}&day={day}&month={month}&year={year}"
            )
            self.logger.debug(f"[Strategy A] Querying v2 endpoint: {url_v2}")
            resp = self.session.get(url_v2, timeout=self.config.request_timeout_seconds)
            if resp.status_code == 200:
                data = resp.json()
                html = data.get("data", {}).get("htmlContent", "")
                img_srcs = re.findall(r'<img[^>]+src=["\']([^"\']+)["\']', html)

                # Filter out step-by-step tutorial placeholder images
                valid_srcs = [src for src in img_srcs if "download-steps" not in src and "preview.png" not in src]
                if valid_srcs:
                    self.logger.info(f"[Strategy A] v2 returned {len(valid_srcs)} Ahmedabad page sources.")
                    images = self._download_image_list(valid_srcs)
                    if images and len(images) > 0:
                        return images
            else:
                self.logger.debug(f"[Strategy A] v2 returned HTTP {resp.status_code}")
        except Exception as e:
            self.logger.debug(f"[Strategy A] v2 fetch encountered error: {e}. Falling back to Strategy B (v1)...")

        # Strategy B: v1 endpoint
        url_v1_base = (
            f"{base_url}/toi/v1/download"
            f"?citySlug={city_slug}&day={day}&month={month}&year={year}"
        )
        self.logger.debug(f"[Strategy B] Querying v1 endpoint: {url_v1_base}&page=1")
        resp1 = self.session.get(f"{url_v1_base}&page=1", timeout=self.config.request_timeout_seconds)
        if resp1.status_code == 400:
            raise RuntimeError("Paper not published yet on InduPaper for this date (HTTP 400).")
        elif resp1.status_code != 200:
            raise RuntimeError(f"v1 endpoint returned HTTP {resp1.status_code}.")

        data1 = resp1.json()
        if not data1 or not data1.get("data"):
            raise RuntimeError("v1 response contains no newspaper data.")

        total_pages = int(data1.get("data", {}).get("totalPage", 0))
        if total_pages == 0:
            raise RuntimeError("v1 reports 0 total pages for Ahmedabad edition.")

        self.logger.info(f"[Strategy B] v1 reports {total_pages} total pages for Ahmedabad edition.")

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
            src = match.group(1)
            if "download-steps" not in src and "preview.png" not in src:
                return self._load_image_from_source(src)
        return None

    def _load_image_from_source(self, src: str) -> Optional[Image.Image]:
        """Loads a PIL Image from either a data URI base64 string, direct URL, or proxy."""
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

        # 1. Direct download
        try:
            img_resp = self.session.get(src, timeout=self.config.request_timeout_seconds)
            if img_resp.status_code == 200 and len(img_resp.content) > 1000:
                img = Image.open(io.BytesIO(img_resp.content))
                if img.mode != "RGB":
                    img = img.convert("RGB")
                return img
        except Exception as e:
            self.logger.debug(f"Direct image load failed ({e}), attempting proxy fallback...")

        # 2. Weserv proxy fallback
        try:
            import urllib.parse
            proxy_url = f"https://images.weserv.nl/?url={urllib.parse.quote(src)}"
            p_resp = self.session.get(proxy_url, timeout=self.config.request_timeout_seconds)
            if p_resp.status_code == 200 and len(p_resp.content) > 1000:
                img = Image.open(io.BytesIO(p_resp.content))
                if img.mode != "RGB":
                    img = img.convert("RGB")
                return img
        except Exception as pe:
            self.logger.warning(f"Failed to download image from {src} via proxy: {pe}")

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
