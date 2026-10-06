"""What the tests may expect of the platform they run on."""

from __future__ import annotations

import sys
from pathlib import Path

#: Windows cannot delete a file this process still maps: a store's purge removes it later.
MAPPED_FILES_STAY = sys.platform == "win32"


def deleted(path: Path) -> bool:
    """Whether ``path`` is gone, or may stay because Windows still maps it."""
    return MAPPED_FILES_STAY or not path.exists()
