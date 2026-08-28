"""
Single Instance Process Lock for Windows.
Prevents duplicate background/startup instances from running simultaneously.
"""

import ctypes
import os
import sys
from pathlib import Path
from typing import Optional


class SingleInstanceLock:
    """
    Guarantees that only one instance of the application runs at any given time.
    Uses Win32 Named Mutex on Windows, with msvcrt file lock fallback.
    """

    def __init__(self, mutex_name: str = "Local\\TOI_Daily_Downloader_Mutex", lock_file_path: Optional[Path] = None):
        self.mutex_name = mutex_name
        self.lock_file_path = lock_file_path
        self._mutex_handle = None
        self._lock_file_handle = None
        self.is_locked = False

    def acquire(self) -> bool:
        """
        Attempts to acquire the single-instance lock.
        Returns True if acquired successfully, False if another instance is running.
        """
        # 1. Try Win32 Named Mutex (preferred on Windows)
        if sys.platform == "win32":
            try:
                ERROR_ALREADY_EXISTS = 183
                kernel32 = ctypes.windll.kernel32
                handle = kernel32.CreateMutexW(None, False, self.mutex_name)
                last_error = kernel32.GetLastError()

                if handle and last_error != ERROR_ALREADY_EXISTS:
                    self._mutex_handle = handle
                    self.is_locked = True
                    return True
                else:
                    if handle:
                        kernel32.CloseHandle(handle)
                    return False
            except Exception:
                pass

        # 2. Fallback: File locking via msvcrt on Windows or fcntl on Unix
        if self.lock_file_path:
            try:
                self.lock_file_path.parent.mkdir(parents=True, exist_ok=True)
                self._lock_file_handle = open(self.lock_file_path, "w")
                if sys.platform == "win32":
                    import msvcrt
                    msvcrt.locking(self._lock_file_handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self._lock_file_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.is_locked = True
                return True
            except (IOError, OSError):
                if self._lock_file_handle:
                    try:
                        self._lock_file_handle.close()
                    except Exception:
                        pass
                    self._lock_file_handle = None
                return False

        return True

    def release(self) -> None:
        """Releases the lock and closes handles cleanly."""
        if not self.is_locked:
            return

        if self._mutex_handle and sys.platform == "win32":
            try:
                ctypes.windll.kernel32.CloseHandle(self._mutex_handle)
            except Exception:
                pass
            self._mutex_handle = None

        if self._lock_file_handle:
            try:
                if sys.platform == "win32":
                    import msvcrt
                    try:
                        msvcrt.locking(self._lock_file_handle.fileno(), msvcrt.LK_UNLCK, 1)
                    except Exception:
                        pass
                self._lock_file_handle.close()
                if self.lock_file_path and self.lock_file_path.exists():
                    self.lock_file_path.unlink(missing_ok=True)
            except Exception:
                pass
            self._lock_file_handle = None

        self.is_locked = False

    def __enter__(self):
        acquired = self.acquire()
        if not acquired:
            raise RuntimeError("Another instance of TOI Daily Downloader is already running.")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()
