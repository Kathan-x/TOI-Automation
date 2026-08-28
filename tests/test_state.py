"""
Tests for persistent state manager.
"""

import json
from pathlib import Path
from src.state_manager import StateManager


def test_missing_state_file_initialization(tmp_path: Path):
    state_file = tmp_path / "state.json"
    mgr = StateManager(state_file)

    assert not mgr.is_date_completed("2026-08-27")
    data = mgr.load_state()
    assert data["last_successful_date"] is None
    assert data["status"] == "initial"


def test_record_success_updates_state(tmp_path: Path):
    state_file = tmp_path / "state.json"
    mgr = StateManager(state_file)
    dummy_pdf = tmp_path / "TOI_Ahmedabad_2026-08-27.pdf"
    dummy_pdf.write_bytes(b"dummy")

    mgr.record_success("2026-08-27", dummy_pdf, page_count=18, size_bytes=1024000)

    assert mgr.is_date_completed("2026-08-27")
    assert not mgr.is_date_completed("2026-08-28")

    state = mgr.load_state()
    assert state["last_successful_date"] == "2026-08-27"
    assert state["status"] == "success"
    assert "2026-08-27" in state["history"]
    assert state["history"]["2026-08-27"]["pages"] == 18


def test_record_failure_does_not_mark_completed(tmp_path: Path):
    state_file = tmp_path / "state.json"
    mgr = StateManager(state_file)

    mgr.record_failure("2026-08-27", "Network timeout error")

    assert not mgr.is_date_completed("2026-08-27")
    state = mgr.load_state()
    assert state["last_successful_date"] is None
    assert state["status"] == "failed"
    assert state["history"]["2026-08-27"]["error"] == "Network timeout error"


def test_date_transitions(tmp_path: Path):
    state_file = tmp_path / "state.json"
    mgr = StateManager(state_file)

    # Yesterday completed
    yesterday_pdf = tmp_path / "TOI_Ahmedabad_2026-08-26.pdf"
    yesterday_pdf.write_bytes(b"yesterday")
    mgr.record_success("2026-08-26", yesterday_pdf, page_count=16, size_bytes=900000)

    assert mgr.is_date_completed("2026-08-26")
    assert not mgr.is_date_completed("2026-08-27")

    # Today completed
    today_pdf = tmp_path / "TOI_Ahmedabad_2026-08-27.pdf"
    today_pdf.write_bytes(b"today")
    mgr.record_success("2026-08-27", today_pdf, page_count=18, size_bytes=1000000)

    assert mgr.is_date_completed("2026-08-26")
    assert mgr.is_date_completed("2026-08-27")
