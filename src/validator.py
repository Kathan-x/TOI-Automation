"""
PDF File Validator for TOI Daily (v2.0).
Ensures downloaded files are uncorrupted, valid, and contain actual newspaper pages.
"""

import logging
import os
from pathlib import Path
from typing import Tuple
from pypdf import PdfReader


MIN_VALID_PDF_SIZE_BYTES = 100 * 1024  # At least 100 KB for a realistic multi-page paper


def validate_pdf_file(file_path: Path) -> Tuple[bool, int, str]:
    """
    Thoroughly checks if a file is a valid, readable, non-empty PDF.

    Returns:
        (is_valid: bool, page_count: int, description: str)
    """
    if not file_path.exists():
        return False, 0, f"File does not exist: {file_path}"

    if not file_path.is_file():
        return False, 0, f"Target path is not a file: {file_path}"

    file_size = file_path.stat().st_size
    if file_size < MIN_VALID_PDF_SIZE_BYTES:
        return False, 0, f"File size ({file_size} bytes) is suspiciously small (minimum: {MIN_VALID_PDF_SIZE_BYTES} bytes)"

    # 1. Check PDF magic bytes (%PDF-)
    try:
        with open(file_path, "rb") as f:
            header = f.read(5)
            if header != b"%PDF-":
                return False, 0, f"Invalid PDF header: expected '%PDF-', got '{header!r}'"
    except Exception as e:
        return False, 0, f"Could not read file header: {e}"

    # 2. Structural inspection with pypdf
    try:
        reader = PdfReader(str(file_path))
        num_pages = len(reader.pages)
        if num_pages < 1:
            return False, 0, "PDF contains 0 pages"

        # Verify first and last page can be extracted without syntax/stream errors
        _ = reader.pages[0]
        if num_pages > 1:
            _ = reader.pages[-1]

        return True, num_pages, f"Valid PDF with {num_pages} pages ({file_size:,} bytes)"
    except Exception as e:
        return False, 0, f"PDF structural validation failed: {e}"


def safe_cleanup_corrupted_file(file_path: Path, logger: logging.Logger = None) -> None:
    """Safely removes an invalid or partial file."""
    try:
        if file_path.exists():
            file_path.unlink()
            if logger:
                logger.warning(f"Cleaned up corrupted/invalid file: {file_path}")
    except Exception as e:
        if logger:
            logger.error(f"Failed to remove invalid file {file_path}: {e}")
