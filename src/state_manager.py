"""
Persistent State Manager for TOI Daily Downloader.
Ensures exactly-once-per-day behavior through atomic state storage.
"""

import datetime
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional


class StateManager:
    def __init__(self, state_file_path: Path):
        self.state_file_path = state_file_path

    def load_state(self) -> Dict[str, Any]:
        """Loads state from disk, returning default structure if missing or corrupted."""
        if not self.state_file_path.exists():
            return {
                "last_successful_date": None,
                "last_successful_file": None,
                "last_attempt_timestamp": None,
                "status": "initial",
                "history": {}
            }

        try:
            with open(self.state_file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if not isinstance(data, dict):
                    raise ValueError("Invalid state file content")
                return data
        except Exception:
            return {
                "last_successful_date": None,
                "last_successful_file": None,
                "last_attempt_timestamp": None,
                "status": "error_loading",
                "history": {}
            }

    def _save_state_atomic(self, state_data: Dict[str, Any]) -> None:
        """Atomically saves state data to avoid corruption during unexpected shutdowns."""
        self.state_file_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.state_file_path.with_suffix(".json.tmp")

        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(state_data, f, indent=2, ensure_ascii=False)

        # Atomic replace on Windows
        os.replace(tmp_path, self.state_file_path)

    def is_date_completed(self, date_str: str) -> bool:
        """Returns True if the specified date (YYYY-MM-DD) was previously marked completed."""
        state = self.load_state()
        if state.get("last_successful_date") == date_str:
            return True
        history = state.get("history", {})
        if date_str in history and history[date_str].get("status") == "success":
            return True
        return False

    def record_success(
        self,
        date_str: str,
        file_path: Path,
        page_count: int,
        size_bytes: int
    ) -> None:
        """Records a successful download for the specified date."""
        state = self.load_state()
        now_iso = datetime.datetime.now().isoformat()

        state["last_successful_date"] = date_str
        state["last_successful_file"] = str(file_path.resolve())
        state["last_attempt_timestamp"] = now_iso
        state["status"] = "success"

        if "history" not in state or not isinstance(state["history"], dict):
            state["history"] = {}

        state["history"][date_str] = {
            "status": "success",
            "file": str(file_path.resolve()),
            "pages": page_count,
            "size_bytes": size_bytes,
            "timestamp": now_iso
        }

        self._save_state_atomic(state)

    def record_failure(self, date_str: str, error_message: str) -> None:
        """Records a failed attempt without marking the date as complete."""
        state = self.load_state()
        now_iso = datetime.datetime.now().isoformat()

        state["last_attempt_timestamp"] = now_iso
        state["status"] = "failed"

        if "history" not in state or not isinstance(state["history"], dict):
            state["history"] = {}

        state["history"][date_str] = {
            "status": "failed",
            "error": error_message,
            "timestamp": now_iso
        }

        self._save_state_atomic(state)
