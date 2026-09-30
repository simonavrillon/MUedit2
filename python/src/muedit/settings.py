"""The user's saved settings: a small JSON file in the per-user config folder."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from muedit.paths import config_dir


def settings_path() -> Path:
    """The settings file."""
    return config_dir() / "settings.json"


def load_settings() -> dict[str, Any]:
    """The saved settings; empty when there are none or the file is unreadable."""
    try:
        data = json.loads(settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_setting(key: str, value: Any) -> None:
    """Save one setting, replacing the file whole so a crash never leaves half of it."""
    settings = load_settings()
    settings[key] = value
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    tmp.replace(path)
