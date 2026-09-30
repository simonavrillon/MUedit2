"""The app's log file, which its decomposition workers also write to."""

from __future__ import annotations

import logging
import logging.handlers
import os
from pathlib import Path

LOG_FILE_ENV = "MUEDIT_LOG_FILE"
MAX_BYTES = 5 * 1024 * 1024
BACKUPS = 3
_FORMAT = "%(asctime)s %(process)d %(levelname)s %(name)s: %(message)s"


def _attach(handler: logging.Handler) -> None:
    handler.setFormatter(logging.Formatter(_FORMAT))
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def log_to_file(path: Path) -> None:
    """Log this process to ``path``, rotated by size; spawned workers inherit the file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    _attach(
        logging.handlers.RotatingFileHandler(
            path, maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding="utf-8"
        )
    )
    logging.captureWarnings(True)
    os.environ[LOG_FILE_ENV] = str(path)


def log_to_inherited_file() -> None:
    """In a worker, append to the file the app logs to, when it logs to one."""
    path = os.environ.get(LOG_FILE_ENV, "").strip()
    if path:
        # Appends only: the app process alone rotates the file.
        _attach(logging.FileHandler(path, encoding="utf-8"))
        logging.captureWarnings(True)
