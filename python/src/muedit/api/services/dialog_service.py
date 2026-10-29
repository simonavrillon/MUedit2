"""Native OS file and folder choosers (AppleScript on macOS, tkinter elsewhere), and the output root."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from muedit.api import config
from muedit.errors import MueditError
from muedit.io.factory import supported_extensions

#: The recordings a loader reads, and the decomposition files the edit stage opens.
_EXTENSIONS = [*supported_extensions(), ".npz"]


def _open_dialog_macos() -> str | None:
    """Use AppleScript — works from any thread/process on macOS."""
    ext_list = "{" + ", ".join(f'"{e}"' for e in _EXTENSIONS) + "}"
    script = (
        'tell application "System Events"\n'
        "  activate\n"
        f'  set f to choose file with prompt "Select EMG signal or decomposition file" '
        f"of type {ext_list}\n"
        "  return POSIX path of f\n"
        "end tell"
    )
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    path = result.stdout.strip()
    return path or None


# Python snippet for tkinter — run in a subprocess so it owns the main thread,
# which is required on Linux/Windows. Its argument is the file patterns to offer.
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
        filetypes=[('EMG signal or decomposition files', sys.argv[1]), ('All files', '*')],
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
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    result = subprocess.run(
        [sys.executable, "-c", _TKINTER_SCRIPT, " ".join(f"*{e}" for e in _EXTENSIONS)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        **kwargs,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "tkinter dialog failed")
    path = result.stdout.strip()
    return path or None


def choose_file() -> str | None:
    """The path the user picks in the native open dialog, or ``None`` when cancelled."""
    return _open_dialog_macos() if sys.platform == "darwin" else _open_dialog_tkinter()


_FOLDER_PROMPT = "Select the output folder: MUedit's projects are the folders inside it"

# The prompt and the starting folder go in as arguments, so no path is quoted into the script.
_FOLDER_APPLESCRIPT = """\
on run argv
  set start to POSIX file (item 2 of argv)
  tell application "System Events"
    activate
    set f to choose folder with prompt (item 1 of argv) default location start
  end tell
  return POSIX path of f
end run
"""

# Its argv: the prompt, then the starting folder.
_TKINTER_FOLDER_SCRIPT = """\
import sys
try:
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk()
    root.withdraw()
    root.attributes('-topmost', True)
    path = filedialog.askdirectory(title=sys.argv[1], initialdir=sys.argv[2], mustexist=True)
    root.destroy()
    print(path or '', end='')
except Exception as e:
    sys.stderr.write(str(e))
    sys.exit(1)
"""


def choose_folder(start: Path) -> Path | None:
    """The folder the user picks in the native dialog, opened on ``start``; None when cancelled."""
    # The dialog cannot open on a folder that is gone.
    start = start if start.is_dir() else Path.home()
    kwargs: dict = {}
    if sys.platform == "darwin":
        command = ["osascript", "-e", _FOLDER_APPLESCRIPT, _FOLDER_PROMPT, str(start)]
    else:
        command = [sys.executable, "-c", _TKINTER_FOLDER_SCRIPT, _FOLDER_PROMPT, str(start)]
        if sys.platform == "win32":
            # Suppress the console window that would briefly flash on Windows
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    result = subprocess.run(
        command, capture_output=True, text=True, timeout=120, check=False, **kwargs
    )
    # A cancelled AppleScript dialog exits non-zero too, with nothing on stdout.
    if result.returncode != 0 and sys.platform != "darwin":
        raise RuntimeError(result.stderr.strip() or "tkinter dialog failed")
    path = result.stdout.strip()
    return Path(path) if path else None


def choose_data_folder() -> Path | None:
    """Make the folder picked in the native dialog the output root; None when cancelled."""
    if config.data_root_override() is not None:
        raise MueditError(
            f"The output folder is set by {config.DATA_ROOT_ENV}. Unset it and relaunch MUedit "
            "to choose one here."
        )
    folder = choose_folder(config.DATA_ROOT)
    if folder is not None:
        config.set_data_root(folder)
    return folder
