"""Append-only JSONL log of an edit session's operations, replayed to recover unsaved edits."""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

from muedit.paths import cache_dir

logger = logging.getLogger(__name__)

LOG_FORMAT = 1
#: Logs older than this are deleted at startup, recovered or not.
MAX_LOG_AGE_SEC = 30 * 24 * 3600


def edit_logs_dir() -> Path:
    """Folder of the edit logs; outside the session stores, which a restart deletes."""
    return cache_dir() / "edit-logs"


def _source_key(source: str | Path) -> str:
    return hashlib.blake2b(str(Path(source).resolve()).encode("utf-8"), digest_size=8).hexdigest()


def _source_stat(source: str | Path) -> dict[str, Any]:
    stat = Path(source).stat()
    return {
        "source": str(Path(source).resolve()),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def _read(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Header and operation records of a log; a line cut short by a crash is ignored."""
    header: dict[str, Any] = {}
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for i, line in enumerate(fh):
            try:
                entry = json.loads(line)
            except ValueError:
                break
            if not isinstance(entry, dict):
                break
            if i == 0:
                header = entry
            else:
                records.append(entry)
    return header, records


def net_edits(records: list[dict[str, Any]]) -> int:
    """Operations left after the undos in ``records``."""
    count = 0
    for rec in records:
        count = max(0, count - 1) if rec.get("op") == "undo" else count + 1
    return count


@dataclass
class RecoverableLog:
    """A log left behind for a file, with the operations to replay."""

    path: Path
    records: list[dict[str, Any]]

    @property
    def edits(self) -> int:
        return net_edits(self.records)

    def discard(self) -> None:
        self.path.unlink(missing_ok=True)


class EditLog:
    """One session's log: a header naming the file it edits, then one line per operation."""

    def __init__(self, path: Path, handle: TextIO) -> None:
        self.path = path
        self._fh = handle
        self.records = 0
        self.net = 0

    @classmethod
    def create(cls, source: str | Path, token: str) -> EditLog | None:
        """A new log for edits of ``source``, or None when it cannot be written."""
        folder = edit_logs_dir()
        path = folder / f"{_source_key(source)}-{token}.jsonl"
        try:
            folder.mkdir(parents=True, exist_ok=True)
            header = {"muedit_edit_log": LOG_FORMAT, "pid": os.getpid(), **_source_stat(source)}
            handle = path.open("w", encoding="utf-8")
            handle.write(json.dumps(header) + "\n")
            handle.flush()
        except OSError:
            logger.warning("Cannot write the edit log %s; edits will not be recoverable", path)
            return None
        return cls(path, handle)

    def append(self, op: str, args: dict[str, Any]) -> None:
        """Record one operation; flushed, so it survives a crash of the app."""
        record = {"op": op, "args": args, "t": time.time()}
        try:
            self._fh.write(json.dumps(record) + "\n")
            self._fh.flush()
        except (OSError, ValueError):
            logger.warning("Writing the edit log %s failed", self.path, exc_info=True)
            return
        self.records += 1
        self.net = max(0, self.net - 1) if op == "undo" else self.net + 1

    def close(self, *, keep: bool) -> None:
        """Close the file; delete it unless ``keep`` (unsaved edits worth recovering)."""
        with contextlib.suppress(OSError, ValueError):
            self._fh.close()
        if not keep:
            self.path.unlink(missing_ok=True)


def find_recoverable(source: str | Path, live: set[Path]) -> RecoverableLog | None:
    """The newest log of unsaved edits to ``source`` that no open session is writing.

    Logs written against another version of the file are deleted.
    """
    folder = edit_logs_dir()
    if not folder.is_dir():
        return None
    try:
        current = _source_stat(source)
    except OSError:
        return None
    found: list[tuple[float, RecoverableLog]] = []
    for path in folder.glob(f"{_source_key(source)}-*.jsonl"):
        if path in live:
            continue
        try:
            header, records = _read(path)
            mtime = path.stat().st_mtime
        except OSError:
            continue
        same_file = all(header.get(k) == current[k] for k in ("source", "size", "mtime_ns"))
        if not same_file or net_edits(records) == 0:
            path.unlink(missing_ok=True)
            continue
        found.append((mtime, RecoverableLog(path, records)))
    if not found:
        return None
    found.sort(key=lambda item: item[0])
    return found[-1][1]


def purge_old_logs(max_age_sec: float = MAX_LOG_AGE_SEC) -> None:
    """Delete logs nobody recovered for ``max_age_sec``."""
    folder = edit_logs_dir()
    if not folder.is_dir():
        return
    cutoff = time.time() - max_age_sec
    for path in folder.glob("*.jsonl"):
        with contextlib.suppress(OSError):
            if path.stat().st_mtime < cutoff:
                path.unlink()
