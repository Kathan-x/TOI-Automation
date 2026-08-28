"""
Browser Automation Fallback using Playwright for TOI Daily (v2.0).
Provides future-proof website handling:
- Centralized selectors.
- Verifies Ahmedabad city selection.
- Generates debug HTML snapshots and screenshots if website structure shifts.
- Self-heals browser installations if required.
"""

import datetime
import logging
from pathlib import Path
from typing import Optional, Tuple

from .config import Config, WEBSITE_CONFIG


class BrowserAutomator:
    def __init__(self, config: Config, logger: logging.Logger):
        self.config = config
        self.logger = logger
        self.selectors = WEBSITE_CONFIG["selectors"]

    def download_via_browser(
        self,
        target_date: datetime.date,
        edition: str,
        output_file: Path
    ) -> Tuple[bool, Optional[Path], int, Optional[str]]:
        """
        Drives the website via Playwright headless browser to download the paper.
        Strictly verifies that Ahmedabad edition is selected.
        """
        if edition.strip().lower() != self.config.REQUIRED_CITY_SLUG:
            err = f"Strict Edition Policy: Prohibited from downloading non-Ahmedabad edition '{edition}'."
            self.logger.error(err)
            return False, None, 0, err

        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            msg = (
                "Playwright is not installed. To enable browser automation fallback, "
                "run: pip install playwright && playwright install chromium"
            )
            self.logger.warning(msg)
            return False, None, 0, msg

        self.logger.info(f"Starting browser automation for Ahmedabad edition (headless={self.config.browser_headless})...")
        date_str = target_date.strftime("%Y-%m-%d")
        city_slug = self.config.REQUIRED_CITY_SLUG

        temp_download_path = self.config.temp_dir / f"TOI_Ahmedabad_{date_str}.pdf.part"

        try:
            with sync_playwright() as p:
                # Self-healing launch
                try:
                    browser = p.chromium.launch(
                        headless=self.config.browser_headless,
                        args=["--no-sandbox", "--disable-dev-shm-usage"]
                    )
                except Exception as launch_err:
                    self.logger.warning(f"Chromium launch error ({launch_err}). Attempting self-healing install...")
                    import subprocess
                    subprocess.run(["playwright", "install", "chromium"], capture_output=True)
                    browser = p.chromium.launch(
                        headless=self.config.browser_headless,
                        args=["--no-sandbox", "--disable-dev-shm-usage"]
                    )

                context = browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/125.0.0.0 Safari/537.36"
                    ),
                    accept_downloads=True
                )
                page = context.new_page()
                page.set_default_timeout(self.config.browser_timeout_ms)

                url = WEBSITE_CONFIG["url"]
                self.logger.debug(f"Navigating to {url}...")
                page.goto(url, wait_until="domcontentloaded")

                # Diagnostic check for date input
                date_sel = self.selectors["date_input"]
                if page.locator(date_sel).count() > 0:
                    page.fill(date_sel, date_str)
                    self.logger.debug(f"Set date input ({date_sel}) to: {date_str}")
                else:
                    self._save_diagnostic_snapshot(page, date_str, "missing_date_input")
                    browser.close()
                    return False, None, 0, f"Website changed: Date input selector '{date_sel}' not found."

                # Diagnostic check for city select
                city_sel = self.selectors["city_select"]
                if page.locator(city_sel).count() > 0:
                    page.select_option(city_sel, value=city_slug)
                    self.logger.debug(f"Selected '{city_slug}' in city dropdown ({city_sel}).")
                else:
                    self._save_diagnostic_snapshot(page, date_str, "missing_city_select")
                    browser.close()
                    return False, None, 0, f"Website changed: City dropdown selector '{city_sel}' not found."

                # Verify selection
                selected_val = page.eval_on_selector(city_sel, "el => el.value")
                if selected_val != city_slug:
                    self._save_diagnostic_snapshot(page, date_str, "city_mismatch")
                    browser.close()
                    err = f"Verification failed: Selected city is '{selected_val}', expected '{city_slug}'."
                    self.logger.error(err)
                    return False, None, 0, err

                self.logger.info("Successfully verified Ahmedabad edition is active on page.")

                # Click PDF Download button
                pdf_btn_sel = self.selectors["pdf_button"]
                if page.locator(pdf_btn_sel).count() > 0:
                    self.logger.info("Triggering PDF download button on website...")
                    with page.expect_download(timeout=self.config.browser_timeout_ms) as download_info:
                        page.locator(pdf_btn_sel).click()
                    download = download_info.value
                    download.save_as(str(temp_download_path))
                    browser.close()

                    # Move to output destination
                    output_file.parent.mkdir(parents=True, exist_ok=True)
                    if output_file.exists():
                        output_file.unlink()
                    temp_download_path.rename(output_file)

                    self.logger.info(f"Browser download successfully saved to: {output_file}")
                    return True, output_file, 1, None
                else:
                    self._save_diagnostic_snapshot(page, date_str, "missing_pdf_button")
                    browser.close()
                    return False, None, 0, f"Website changed: PDF download button '{pdf_btn_sel}' not found."

        except Exception as e:
            self.logger.error(f"Browser automation error: {e}")
            if temp_download_path.exists():
                temp_download_path.unlink()
            return False, None, 0, str(e)

    def _save_diagnostic_snapshot(self, page, date_str: str, reason: str) -> None:
        """Saves a debug HTML DOM snapshot and screenshot when site structure changes."""
        try:
            debug_dir = self.config.debug_dir
            html_path = debug_dir / f"debug_{date_str}_{reason}.html"
            png_path = debug_dir / f"debug_{date_str}_{reason}.png"

            html_content = page.content()
            html_path.write_text(html_content, encoding="utf-8")
            page.screenshot(path=str(png_path), full_page=True)

            self.logger.warning(
                f"Website structure change detected ({reason}). "
                f"Saved diagnostic snapshot to: {html_path} and {png_path}"
            )
        except Exception as e:
            self.logger.error(f"Could not save diagnostic snapshot: {e}")
