"""Single-instance runtime lock for the client process."""

from __future__ import annotations

import fcntl
import logging
from pathlib import Path

from airplay_client.config import client_settings

logger = logging.getLogger(__name__)


class SingleInstanceLock:
    """Prevents multiple concurrent client processes with same client_id."""

    def __init__(self, client_id: str) -> None:
        lock_dir = Path(client_settings.single_instance_lock_path)
        lock_dir.mkdir(parents=True, exist_ok=True)
        sanitized = "".join(ch if ch.isalnum() or ch in "-._" else "_" for ch in client_id)
        self._path = lock_dir / f"chromacatch-client-{sanitized}.lock"
        self._handle = None

    def acquire(self) -> bool:
        self._handle = open(self._path, "w")
        try:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        self._handle.write(str(self._path))
        self._handle.flush()
        logger.debug("Acquired runtime lock: %s", self._path)
        return True

    def release(self) -> None:
        if self._handle is None:
            return
        try:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        self._handle.close()
        self._handle = None

