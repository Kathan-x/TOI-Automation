"""
Tests for PDF validator.
"""

import os
from pathlib import Path
from PIL import Image
from src.validator import validate_pdf_file, safe_cleanup_corrupted_file


def test_validator_nonexistent_file(tmp_path: Path):
    non_existent = tmp_path / "does_not_exist.pdf"
    is_valid, pages, msg = validate_pdf_file(non_existent)
    assert not is_valid
    assert pages == 0
    assert "does not exist" in msg


def test_validator_empty_file(tmp_path: Path):
    empty_file = tmp_path / "empty.pdf"
    empty_file.write_bytes(b"")
    is_valid, pages, msg = validate_pdf_file(empty_file)
    assert not is_valid
    assert pages == 0
    assert "suspiciously small" in msg


def test_validator_invalid_header(tmp_path: Path):
    corrupt_file = tmp_path / "corrupt.pdf"
    # Over 100KB (e.g. 150KB) but not a valid PDF header
    corrupt_file.write_bytes(b"NOT_A_PDF_HEADER" + b"0" * 150000)
    is_valid, pages, msg = validate_pdf_file(corrupt_file)
    assert not is_valid
    assert pages == 0
    assert "Invalid PDF header" in msg


def test_validator_valid_pdf(tmp_path: Path):
    valid_pdf_path = tmp_path / "valid.pdf"
    # Generate realistic uncompressed/noisy images so file size exceeds 100KB threshold
    noise_bytes = os.urandom(100 * 100 * 3)
    img1 = Image.frombytes("RGB", (100, 100), noise_bytes).resize((800, 1000))
    img2 = Image.frombytes("RGB", (100, 100), noise_bytes).resize((800, 1000))
    
    # Save as multi-page PDF
    img1.save(
        str(valid_pdf_path),
        format="PDF",
        save_all=True,
        append_images=[img2],
        quality=95
    )

    # Pad if needed so size >= 100KB
    if valid_pdf_path.stat().st_size < 100 * 1024:
        # Create larger noise images
        big_noise = os.urandom(300 * 300 * 3)
        img1 = Image.frombytes("RGB", (300, 300), big_noise).resize((1000, 1400))
        img2 = Image.frombytes("RGB", (300, 300), big_noise).resize((1000, 1400))
        img1.save(
            str(valid_pdf_path),
            format="PDF",
            save_all=True,
            append_images=[img2],
            quality=95
        )

    is_valid, pages, msg = validate_pdf_file(valid_pdf_path)
    assert is_valid
    assert pages >= 2
    assert "Valid PDF" in msg


def test_safe_cleanup_corrupted_file(tmp_path: Path):
    junk = tmp_path / "junk.pdf"
    junk.write_bytes(b"bad content")
    assert junk.exists()

    safe_cleanup_corrupted_file(junk)
    assert not junk.exists()
