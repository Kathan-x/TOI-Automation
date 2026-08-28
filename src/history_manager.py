"""
Daily Download History Manager for TOI Daily.
Maintains a permanent, structured history.json log of all downloads.
"""

import datetime
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional


def format_size(size_bytes: int) -> str:
    """Formats bytes to human-readable string (e.g. '35.8 MB')."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.2f} MB"


class HistoryManager:
    def __init__(self, history_file_path: Path):
        self.history_file_path = history_file_path

    def load_history(self) -> List[Dict[str, Any]]:
        """Loads download history records from disk."""
        if not self.history_file_path.exists():
            return []

        try:
            with open(self.history_file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
                return []
        except Exception:
            return []

    def record_entry(
        self,
        date_str: str,
        edition: str,
        status: str,
        file_path: Optional[Path] = None,
        page_count: int = 0,
        size_bytes: int = 0,
        duration_seconds: float = 0.0,
        error_message: Optional[str] = None
    ) -> None:
        """Appends or updates a permanent history record."""
        history = self.load_history()
        now_iso = datetime.datetime.now().isoformat()

        entry = {
            "download_date": date_str,
            "edition": edition,
            "status": status,
            "timestamp": now_iso,
            "duration_seconds": round(duration_seconds, 2),
            "page_count": page_count,
            "size_bytes": size_bytes,
            "size_formatted": format_size(size_bytes) if size_bytes > 0 else "0 B",
            "file_path": str(file_path.resolve()) if file_path else None,
            "error": error_message
        }

        # Update existing record for date if present, or append new
        updated = False
        for i, existing in enumerate(history):
            if existing.get("download_date") == date_str:
                history[i] = entry
                updated = True
                break

        if not updated:
            history.append(entry)

        # Atomic write
        self._save_history_atomic(history)

    def _save_history_atomic(self, history: List[Dict[str, Any]]) -> None:
        """Saves history list atomically using a temporary file."""
        self.history_file_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.history_file_path.with_suffix(".json.tmp")

        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2, ensure_ascii=False)

        os.replace(tmp_path, self.history_file_path)
