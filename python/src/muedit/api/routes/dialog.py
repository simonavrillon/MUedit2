"""Native OS dialog endpoints."""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/dialog")

# Extensions accepted by the dialog
_EXTENSIONS = ["mat", "otb+", "otb4", "npz"]


def _open_dialog_macos() -> str | None:
    """Use AppleScript — works from any thread/process on macOS."""
    ext_list = "{" + ", ".join(f'".{e}"' for e in _EXTENSIONS) + "}"
    script = (
        'tell application "System Events"\n'
        '  activate\n'
        f'  set f to choose file with prompt "Select EMG signal or decomposition file" '
        f'of type {ext_list}\n'
        '  return POSIX path of f\n'
        'end tell'
    )
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
        timeout=120,
    )
    path = result.stdout.strip()
    return path if path else None


# Python snippet for tkinter — run in a subprocess so it owns the main thread,
# which is required on Linux/Windows.
_TKINTER_SCRIPT = """\
import sys
try:
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk()
    root.withdraw()
    root.attributes('-topmost', True)
    path = filedialog.askopenfilename(
        title='Select EMG signal or decomposition file',
        filetypes=[
            ('EMG signal files', '*.mat *.otb+ *.otb4 *.npz'),
            ('MATLAB files', '*.mat'),
            ('OTB files', '*.otb+ *.otb4'),
            ('Decomposition files', '*.npz'),
            ('All files', '*'),
        ],
    )
    root.destroy()
    print(path or '', end='')
except Exception as e:
    sys.stderr.write(str(e))
    sys.exit(1)
"""


def _open_dialog_tkinter() -> str | None:
    """Open a native file chooser using tkinter in a subprocess."""
    kwargs: dict = {}
    if sys.platform == "win32":
        # Suppress the console window that would briefly flash on Windows
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
    result = subprocess.run(
        [sys.executable, "-c", _TKINTER_SCRIPT],
        capture_output=True,
        text=True,
        timeout=120,
        **kwargs,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "tkinter dialog failed")
    path = result.stdout.strip()
    return path if path else None


@router.get("/open-file")
def open_file_dialog() -> dict[str, str | None]:
    """Return the selected file path and basename from a native open dialog."""
    try:
        if sys.platform == "darwin":
            path = _open_dialog_macos()
        else:
            path = _open_dialog_tkinter()

        if not path:
            return {"path": None, "name": None}
        return {"path": path, "name": Path(path).name}
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=408, detail="File dialog timed out") from None
    except Exception as exc:
        logger.exception("Native file dialog failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
