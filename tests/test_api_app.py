"""The app: its error envelope, route table, tab sessions, native file dialog and health probe."""

from __future__ import annotations

import logging
import re
import subprocess
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import Response
from starlette.testclient import TestClient

from muedit.api import cache
from muedit.errors import SignalLoadError
from tests._api import API, SESSION_HEADER, error, ok, upload
from tests.conftest import REPO_ROOT

LIVE_ENDPOINTS: list[tuple[str, str]] = [
    ("GET", "/health"),
    ("GET", "/dialog/open-file"),
    ("POST", "/preview-by-path"),
    ("GET", "/series/emg"),
    ("GET", "/series/overview"),
    ("GET", "/series/aux"),
    ("POST", "/qc/auto"),
    ("POST", "/qc/segment"),
    ("POST", "/decompose_stream"),
    ("POST", "/decompose/cancel"),
    ("GET", "/series/pulse"),
    ("POST", "/edit/session/open"),
    ("GET", "/edit/session"),
    ("POST", "/edit/session/recover"),
    ("POST", "/edit/session/prepare-grid"),
    ("POST", "/edit/session/save"),
    ("POST", "/edit/ops/{op}"),
    ("POST", "/edit/save"),
    ("POST", "/session/close"),
]


# Path Item keys that are operations; the rest (``parameters``, ``summary``, ...) are metadata.
OPENAPI_OPERATION_KEYS = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}


# ── Errors and routes ───────────────────────────────────────────────────────


class TestErrorHandlers:
    @pytest.fixture
    def failing(self) -> Iterator[TestClient]:
        from muedit.api.app_factory import create_app

        app = create_app()

        @app.get("/fixable")
        def fixable() -> None:
            raise SignalLoadError("pick another file")

        @app.get("/bug")
        def bug() -> None:
            raise RuntimeError("secret internal detail")

        with TestClient(app, raise_server_exceptions=False) as c:
            yield c

    def test_a_user_fixable_error_is_400_with_its_message(
        self, failing: TestClient, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.INFO, logger="muedit.api.errors"):
            err = error(failing.get("/fixable"), 400)
        assert err == {
            "code": "http_400",
            "message": "pick another file",
            "detail": {"field": "path"},
        }
        (logged,) = [r for r in caplog.records if r.name == "muedit.api.errors"]
        assert (logged.levelno, logged.exc_info) == (logging.INFO, None)
        assert logged.getMessage() == "GET /fixable: 400 pick another file"

    def test_a_bug_is_a_generic_500_logged_with_its_traceback(
        self, failing: TestClient, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.ERROR, logger="muedit.api.errors"):
            err = error(failing.get("/bug"), 500, "internal_error")
        assert err == {"code": "internal_error", "message": "Internal server error"}
        logged = [r for r in caplog.records if r.name == "muedit.api.errors"]
        assert len(logged) == 1
        assert logged[0].exc_info is not None
        assert str(logged[0].exc_info[1]) == "secret internal detail"


class TestRouteTable:
    def test_every_live_endpoint_is_registered(self, client: TestClient) -> None:
        app = client.app
        assert isinstance(app, FastAPI)
        # FastAPI >=0.137 keeps included routers nested, so ``app.routes`` no longer lists them.
        registered = {
            (method.upper(), path)
            for path, path_item in app.openapi()["paths"].items()
            for method in path_item
            if method in OPENAPI_OPERATION_KEYS
        }
        missing = [(m, API + p) for m, p in LIVE_ENDPOINTS if (m, API + p) not in registered]
        assert not missing

    def test_frontend_static_routes_are_live(self) -> None:
        """Every literal path in ``routes.js`` is covered by this tier."""
        js = (REPO_ROOT / "frontend" / "src" / "api" / "routes.js").read_text(encoding="utf-8")
        static = set(re.findall(r':\s*"(/[^"]+)"', js))
        assert static, "no routes parsed from routes.js"
        assert static <= {p for _, p in LIVE_ENDPOINTS}

    def test_unknown_route_uses_error_envelope(self, client: TestClient) -> None:
        assert error(client.get(f"{API}/does-not-exist"), 404)["message"] == "Not Found"

    def test_wrong_method_uses_error_envelope(self, client: TestClient) -> None:
        resp = client.get(f"{API}/edit/save")
        error(resp, 405)
        assert resp.headers["allow"] == "POST"


def test_health(client: TestClient) -> None:
    assert ok(client.get(f"{API}/health")) == {"status": "ok"}


# ── Tab sessions ────────────────────────────────────────────────────────────


class TestSessions:
    def _qc_auto(self, client: TestClient, token: str) -> Response:
        return client.post(f"{API}/qc/auto", json={"upload_token": token})

    def test_loading_the_next_file_releases_the_sessions_upload(
        self, client: TestClient, signal_mat: Path
    ) -> None:
        first = upload(client, signal_mat, "tab-next-file")
        second = upload(client, signal_mat, "tab-next-file")
        error(self._qc_auto(client, first), 400)
        ok(self._qc_auto(client, second))

    def test_a_file_opened_while_another_loads_leaves_both_whole(
        self, client: TestClient, signal_mat: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from muedit.api.services import preview_service

        real_build = preview_service.build_signal_views
        nested: list[str] = []

        def build_then_open_another(*args: Any, **kwargs: Any) -> Any:
            built = real_build(*args, **kwargs)
            if not nested:  # the tab opens a second file while the first is being built
                nested.append("")
                payload = preview_service._build_preview_core(str(signal_mat), "tab-overlap")
                nested[0] = payload["upload_token"]
            return built

        monkeypatch.setattr(preview_service, "build_signal_views", build_then_open_another)
        first = upload(client, signal_mat, "tab-overlap")
        # The tab keeps the upload kept last; the other is gone, not half-deleted.
        ok(self._qc_auto(client, first))
        error(self._qc_auto(client, nested[0]), 400)

    def test_closing_a_session_drops_its_data(
        self, client: TestClient, signal_mat: Path, upload_token: str
    ) -> None:
        token = upload(client, signal_mat, "tab-closing")
        resp = client.post(f"{API}/session/close", params={"session": "tab-closing"})
        assert resp.status_code == 204
        error(self._qc_auto(client, token), 400)
        ok(self._qc_auto(client, upload_token))
        assert "tab-closing" not in cache.BUDGET.sessions

    def test_malformed_session_ids_are_refused(self, client: TestClient) -> None:
        closing = client.post(f"{API}/session/close", params={"session": "../default"})
        assert error(closing, 400)["detail"] == {"field": "session"}
        tagged = client.get(f"{API}/health", headers={SESSION_HEADER: "../default"})
        assert error(tagged, 400)["detail"] == {"field": "X-MUedit-Session"}
        assert error(client.post(f"{API}/session/close"), 422, "validation_error")

    def test_a_request_with_a_session_makes_it_active(self, client: TestClient) -> None:
        client.get(f"{API}/health", headers={SESSION_HEADER: "tab-active"})
        assert cache.BUDGET.active_session == "tab-active"


# ── /dialog/open-file ───────────────────────────────────────────────────────


class TestDialog:
    @pytest.fixture()
    def pick(self, monkeypatch: pytest.MonkeyPatch) -> Callable[[object], None]:
        """Replace both native dialog backends with a controllable stub."""
        import muedit.api.routes.dialog as dialog

        def install(behaviour: object) -> None:
            def fake() -> object:
                if isinstance(behaviour, BaseException):
                    raise behaviour
                return behaviour

            monkeypatch.setattr(dialog, "_open_dialog_macos", fake)
            monkeypatch.setattr(dialog, "_open_dialog_tkinter", fake)

        return install

    def test_selected_file(self, client: TestClient, pick: Callable[[object], None]) -> None:
        pick("/data/rec/sub-01_emg.otb4")
        assert ok(client.get(f"{API}/dialog/open-file")) == {
            "path": "/data/rec/sub-01_emg.otb4",
            "name": "sub-01_emg.otb4",
        }

    def test_cancelled(self, client: TestClient, pick: Callable[[object], None]) -> None:
        pick(None)
        assert ok(client.get(f"{API}/dialog/open-file")) == {"path": None, "name": None}

    def test_timeout_is_408(self, client: TestClient, pick: Callable[[object], None]) -> None:
        pick(subprocess.TimeoutExpired("osascript", 120))
        error(client.get(f"{API}/dialog/open-file"), 408)

    def test_backend_failure_is_500(
        self,
        client: TestClient,
        pick: Callable[[object], None],
    ) -> None:
        pick(RuntimeError("no display"))
        err = error(client.get(f"{API}/dialog/open-file"), 500)
        assert err["message"] == "Could not open the file dialog; see the server log"
