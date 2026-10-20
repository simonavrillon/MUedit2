"""MUedit — FastICA-based Motor Unit decomposition of High-Density EMG signals."""

from __future__ import annotations

import importlib.metadata


def installed_version() -> str | None:
    """The installed MUedit version, or ``None`` when the package metadata is missing."""
    try:
        return importlib.metadata.version("muedit")
    except importlib.metadata.PackageNotFoundError:
        return None
