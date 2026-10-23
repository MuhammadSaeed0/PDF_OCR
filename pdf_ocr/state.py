"""Persistent processing state (a JSON file) and the single-instance lock."""

from __future__ import annotations

import copy
import json
import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _replace_with_retry(src: Path, dst: Path, attempts: int = 10) -> None:
    """os.replace that tolerates short-lived locks (antivirus, backup or indexing software)."""
    for attempt in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(0.3)


class StateStore:
    """Remembers, per source file, what was processed and what the result was.

    Record layout (keyed by the case-normalised path relative to the source folder):
      status       "completed" or "failed"
      source       {"size", "mtime_ns", "sha256"} of the source version that was processed
      pages        page count (completed only)
      output       {"size", "mtime_ns"} of the output written (completed only)
      error, permanent, attempts, next_retry_at   (failed only)

    The state file is only a cache of what was done: the destination PDF is checked
    on every scan, so a deleted or damaged output is always regenerated.
    """

    VERSION = 1

    def __init__(self, path: Path, source_folder: Path, destination_folder: Path) -> None:
        self.path = path
        self._meta = {"source_folder": str(source_folder), "destination_folder": str(destination_folder)}
        self._files: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()

    def load(self) -> None:
        with self._lock:
            self._files = {}
            if not self.path.exists():
                return
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                files = data.get("files", {}) if isinstance(data, dict) else None
                if not isinstance(files, dict):
                    raise ValueError("unexpected file structure")
                self._files = {str(k): v for k, v in files.items() if isinstance(v, dict)}
            except (OSError, ValueError) as exc:
                backup = self.path.with_name(f"{self.path.stem}.corrupt-{time.strftime('%Y%m%d-%H%M%S')}.json")
                try:
                    os.replace(self.path, backup)
                except OSError:
                    backup = None
                logger.warning(
                    "State file %s could not be read (%s). Starting with an empty state; existing valid "
                    "outputs will be recognised again. Backup: %s",
                    self.path, exc, backup or "not created",
                )

    def get(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._files.get(key)
            return copy.deepcopy(record) if record is not None else None

    def set(self, key: str, record: dict[str, Any]) -> None:
        with self._lock:
            self._files[key] = copy.deepcopy(record)
            self.save()

    def remove(self, key: str) -> bool:
        with self._lock:
            if key not in self._files:
                return False
            del self._files[key]
            self.save()
            return True

    def keys(self) -> list[str]:
        with self._lock:
            return list(self._files)

    def reset_failures(self) -> int:
        """Make every failed file eligible for an immediate retry. Returns how many there were."""
        with self._lock:
            count = 0
            for record in self._files.values():
                if record.get("status") == STATUS_FAILED:
                    record["permanent"] = False
                    record["next_retry_at"] = 0
                    count += 1
            if count:
                self.save()
            return count

    def save(self) -> None:
        """Write the state atomically: temp file, flush to disk, then replace."""
        with self._lock:
            data = {"version": self.VERSION, **self._meta, "updated_at": now_iso(), "files": self._files}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=1)
                fh.flush()
                os.fsync(fh.fileno())
            _replace_with_retry(tmp, self.path)


class InstanceLock:
    """An OS-level file lock so that only one process works on a (source, destination) pair.

    The operating system releases the lock automatically if the process crashes.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._fh = None

    def acquire(self) -> bool:
        if self._fh is not None:
            return True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(self.path, "a+b")
        try:
            if os.name == "nt":
                import msvcrt

                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            return False
        self._fh = fh
        return True

    def release(self) -> None:
        if self._fh is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self._fh.seek(0)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            self._fh.close()
            self._fh = None
