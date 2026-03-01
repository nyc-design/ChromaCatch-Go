"""Tests for single-instance runtime lock."""

from pathlib import Path

from airplay_client.config import client_settings
from airplay_client.runtime_lock import SingleInstanceLock


def test_runtime_lock_prevents_second_instance(tmp_path: Path):
    original = client_settings.single_instance_lock_path
    client_settings.single_instance_lock_path = str(tmp_path)
    try:
        lock1 = SingleInstanceLock("test-client")
        lock2 = SingleInstanceLock("test-client")
        assert lock1.acquire() is True
        assert lock2.acquire() is False
        lock1.release()
        assert lock2.acquire() is True
        lock2.release()
    finally:
        client_settings.single_instance_lock_path = original
