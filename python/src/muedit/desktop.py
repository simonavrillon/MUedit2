"""The desktop app: the MUedit server and a native window on it, in one process."""

from __future__ import annotations

import contextlib
import html
import logging
import os
import secrets
import socket
import sys
import threading
import time
from collections.abc import Iterator
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import IO, TYPE_CHECKING

from muedit.api import config
from muedit.app_log import log_to_file
from muedit.paths import cache_dir, frontend_dir, log_dir
from muedit.settings import save_setting

if TYPE_CHECKING:
    import uvicorn
    import webview

logger = logging.getLogger(__name__)

HOST = "127.0.0.1"
DEBUG_ENV = "MUEDIT_DEBUG"
STARTUP_TIMEOUT_SEC = 60.0
SHUTDOWN_TIMEOUT_SEC = 15.0
#: Exit status when no native window can open here; the launchers then use the browser.
NO_WINDOW = 3

_PAGE = """<!doctype html><html><head><meta charset="utf-8"><style>
body{{margin:0;height:100vh;display:flex;align-items:center;justify-content:center;
background:#1e1e1e;color:#e6e6e6;font:15px -apple-system,"Segoe UI",sans-serif}}
p{{max-width:32em;text-align:center;line-height:1.5}}</style></head>
<body><p>{}</p></body></html>"""


def app_version() -> str:
    """The installed MUedit version."""
    try:
        return version("muedit")
    except PackageNotFoundError:
        return "unknown"


# ── one instance ─────────────────────────────────────────────────────────────


def lock_path() -> Path:
    """The file a running app holds locked."""
    return cache_dir() / "desktop.lock"


def acquire_instance_lock(path: Path) -> IO[bytes] | None:
    """Lock ``path`` for as long as the returned file stays open; None when another app holds it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    try:
        if sys.platform == "win32":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


# ── the bridge the page calls as window.pywebview.api ───────────────────────


class DesktopApi:
    """What the page may ask of the app; pywebview exposes the public methods only."""

    def __init__(self, token: str) -> None:
        self._token = token

    def token(self) -> str:
        """The secret every API request must carry."""
        return self._token

    def app_info(self) -> dict[str, str]:
        """The version, output folder and log file, for the page to show."""
        return {
            "version": app_version(),
            "data_root": str(config.DATA_ROOT),
            "log_file": str(log_dir() / "muedit.log"),
        }

    def open_file(self) -> dict[str, str | None]:
        """A file the user picks, in the shape of ``GET /dialog/open-file``."""
        import webview

        if not webview.windows:
            return {"path": None, "name": None}
        # No filter: pywebview's filter syntax cannot express the ``.otb+`` extension.
        picked = webview.windows[0].create_file_dialog(webview.FileDialog.OPEN)
        if not picked:
            return {"path": None, "name": None}
        path = str(picked[0])
        return {"path": path, "name": Path(path).name}

    def choose_output_folder(self) -> dict[str, str | None]:
        """Let the user pick the output folder, and keep it for later sessions."""
        import webview

        if not webview.windows:
            return {"path": None}
        picked = webview.windows[0].create_file_dialog(
            webview.FileDialog.FOLDER, directory=str(config.DATA_ROOT)
        )
        if not picked:
            return {"path": None}
        path = str(picked[0])
        config.set_data_root(path)
        try:
            save_setting(config.DATA_ROOT_SETTING, path)
        except OSError:
            logger.exception("Could not save the settings")
        return {"path": path}


# ── server ───────────────────────────────────────────────────────────────────


class _Server:
    """Uvicorn on a free loopback port, run on a thread so the window can own the main one."""

    def __init__(self, token: str) -> None:
        self.token = token
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.bind((HOST, 0))
        self.port: int = self.socket.getsockname()[1]
        self.failed: BaseException | None = None
        self._server: uvicorn.Server | None = None
        self._thread = threading.Thread(target=self._run, name="muedit-server", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _run(self) -> None:
        try:
            # Imported here, so the window is up before NumPy and SciPy load.
            import uvicorn

            from muedit.api.app_factory import create_app, mount_frontend
            from muedit.api.routes import include_routers

            app = create_app(version=app_version(), allowed_hosts=[HOST], token=self.token)
            include_routers(app)
            frontend = frontend_dir()
            if frontend is None:
                raise RuntimeError("The frontend files are missing from this installation")
            mount_frontend(app, frontend)
            self._server = uvicorn.Server(
                uvicorn.Config(app, log_level="warning", access_log=False, log_config=None)
            )
            self._server.run(sockets=[self.socket])
        except BaseException as exc:
            logger.exception("The server stopped")
            self.failed = exc

    def wait_until_started(self, timeout: float) -> bool:
        """True once the server accepts requests; False when it failed or took too long."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and self.failed is None and self._thread.is_alive():
            if self._server is not None and self._server.started:
                return True
            time.sleep(0.05)
        return False

    def stop(self) -> None:
        """Stop serving, which stops any decomposition, and wait for it to finish."""
        if self._server is not None:
            self._server.should_exit = True
        self._thread.join(SHUTDOWN_TIMEOUT_SEC)


# ── window ───────────────────────────────────────────────────────────────────


def _message_page(text: str) -> str:
    return _PAGE.format(html.escape(text))


def _load_app(window: webview.Window, server: _Server) -> None:
    """Replace the starting page with the app once the server answers, or with the error."""
    if server.wait_until_started(STARTUP_TIMEOUT_SEC):
        # ``desktop`` tells the page to take its token and dialogs from the bridge.
        window.load_url(f"http://{HOST}:{server.port}/?desktop=1")
    else:
        window.load_html(
            _message_page(f"MUedit could not start. The details are in {log_dir() / 'muedit.log'}.")
        )


@contextlib.contextmanager
def _single_instance() -> Iterator[bool]:
    handle = acquire_instance_lock(lock_path())
    try:
        yield handle is not None
    finally:
        if handle is not None:
            handle.close()


def main() -> int:
    """Run the desktop app until its window closes; ``NO_WINDOW`` when it cannot open one."""
    try:
        import webview
        from webview.errors import WebViewException
    except ImportError:
        print("pywebview is not installed: uv sync --extra desktop", file=sys.stderr)
        return NO_WINDOW

    with _single_instance() as first:
        if not first:
            webview.create_window(
                "MUedit", html=_message_page("MUedit is already running."), width=420, height=180
            )
            webview.start()
            return 0

        log_to_file(log_dir() / "muedit.log")
        logger.info("MUedit %s starting", app_version())

        server = _Server(secrets.token_urlsafe(32))
        server.start()
        api = DesktopApi(server.token)
        window = webview.create_window(
            "MUedit",
            html=_message_page("Starting MUedit…"),
            js_api=api,
            width=1440,
            height=900,
            min_size=(1024, 680),
            text_select=True,
            background_color="#1e1e1e",
        )
        if window is None:
            raise RuntimeError("pywebview did not create the window")
        try:
            webview.start(_load_app, (window, server), debug=os.environ.get(DEBUG_ENV, "") == "1")
        except WebViewException as exc:
            # No GUI toolkit pywebview can use, e.g. Linux without WebKitGTK and PyGObject.
            logger.warning("No native window: %s", exc)
            print(f"No native window here: {exc}", file=sys.stderr)
            return NO_WINDOW
        finally:
            server.stop()
            logger.info("MUedit closed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
