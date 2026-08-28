"""
Tests for single-instance process lock.
"""

from pathlib import Path
from src.lock import SingleInstanceLock


def test_lock_acquire_and_release(tmp_path: Path):
    lock_file = tmp_path / "test.lock"
    lock = SingleInstanceLock(mutex_name="Local\\TOI_Test_Mutex_1", lock_file_path=lock_file)

    assert lock.acquire()
    assert lock.is_locked

    # Release
    lock.release()
    assert not lock.is_locked


def test_lock_prevents_duplicate_acquisition(tmp_path: Path):
    lock_file = tmp_path / "test_duplicate.lock"
    lock1 = SingleInstanceLock(mutex_name="Local\\TOI_Test_Mutex_2", lock_file_path=lock_file)
    lock2 = SingleInstanceLock(mutex_name="Local\\TOI_Test_Mutex_2", lock_file_path=lock_file)

    assert lock1.acquire()

    # Second instance must fail to acquire
    assert not lock2.acquire()

    # Clean release of lock1
    lock1.release()

    # Now lock2 can acquire
    assert lock2.acquire()
    lock2.release()


def test_lock_context_manager(tmp_path: Path):
    lock_file = tmp_path / "test_ctx.lock"
    lock1 = SingleInstanceLock(mutex_name="Local\\TOI_Test_Mutex_3", lock_file_path=lock_file)

    with lock1:
        assert lock1.is_locked
        lock2 = SingleInstanceLock(mutex_name="Local\\TOI_Test_Mutex_3", lock_file_path=lock_file)
        assert not lock2.acquire()

    assert not lock1.is_locked
