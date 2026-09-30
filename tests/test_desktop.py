"""Desktop mode: the app token, output folders, settings, the instance lock and the JS bridge."""

from __future__ import annotations

import sys
import types
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

import muedit.api.config as config
from muedit import desktop, settings
from muedit.api.app_factory import TOKEN_HEADER, create_app, mount_frontend
from muedit.api.routes import include_routers
from muedit.paths import frontend_dir

API = "/api/v1"
TOKEN = "s3cret-token"  # noqa: S105 (a test token)


@pytest.fixture()
def desktop_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """The app as ``muedit.desktop`` builds it."""
    monkeypatch.setenv("MUEDIT_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "data")
    app = create_app(allowed_hosts=["127.0.0.1"], token=TOKEN)
    include_routers(app)
    frontend = frontend_dir()
    assert frontend is not None
    mount_frontend(app, frontend)
    with TestClient(app, base_url="http://127.0.0.1:50123") as client:
        yield client


class TestToken:
    def test_the_health_probe_and_the_page_need_no_token(self, desktop_client: TestClient) -> None:
        assert desktop_client.get(f"{API}/health").status_code == 200
        assert desktop_client.get("/").status_code == 200
        assert desktop_client.get("/src/app/platform.js").status_code == 200

    def test_api_requests_need_the_token(self, desktop_client: TestClient) -> None:
        for headers in ({}, {TOKEN_HEADER: "wrong"}):
            r = desktop_client.get(f"{API}/debug/memory", headers=headers)
            assert r.status_code == 401
            assert r.json()["error"]["code"] == "unauthorized"
        # A form post is a "simple" request a hostile page can send without a preflight.
        r = desktop_client.post(f"{API}/decompose_stream", data={"project": "x"})
        assert r.status_code == 401
        ok = desktop_client.get(f"{API}/debug/memory", headers={TOKEN_HEADER: TOKEN})
        assert ok.status_code == 200

    def test_only_the_loopback_address_is_a_valid_host(self, desktop_client: TestClient) -> None:
        r = desktop_client.get(f"{API}/health", headers={"Host": "localhost:50123"})
        assert r.status_code == 400

    def test_without_a_token_the_api_is_open(self, tmp_path: Path) -> None:
        app = create_app()
        include_routers(app)
        with TestClient(app) as client:
            assert client.get(f"{API}/debug/memory").status_code == 200


class TestProjects:
    @pytest.mark.parametrize("name", ["", "  ", None])
    def test_an_empty_project_is_the_default_folder(
        self, name: str | None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(config, "DATA_ROOT", tmp_path)
        assert config.resolve_bids_root(name) == tmp_path / config.DEFAULT_PROJECT

    @pytest.mark.parametrize("name", ["study1", "Study 2", "pilot.v2"])
    def test_a_folder_name_is_a_project(
        self, name: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(config, "DATA_ROOT", tmp_path)
        assert config.resolve_bids_root(name) == tmp_path / name

    @pytest.mark.parametrize("name", ["..", ".", "../x", "a/b", "a\\b", "/abs", "C:\\x", "c:x"])
    def test_a_path_is_refused(self, name: str) -> None:
        with pytest.raises(ValueError, match="folder name"):
            config.resolve_bids_root(name)

    def test_a_run_with_a_path_as_project_is_refused_before_it_starts(
        self, desktop_client: TestClient
    ) -> None:
        r = desktop_client.post(
            f"{API}/decompose_stream",
            data={"bids_export": "true", "project": "../outside"},
            headers={TOKEN_HEADER: TOKEN},
        )
        assert r.status_code == 400
        assert r.json()["error"]["detail"]["field"] == "project"

    def test_project_of_names_the_folder_under_the_data_root(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "data")
        assert config.project_of(tmp_path / "data" / "study1") == "study1"
        assert config.project_of(tmp_path / "data" / "lab" / "study1") == "lab"
        assert config.project_of(tmp_path / "data") == ""
        assert config.project_of(tmp_path / "elsewhere" / "study1") == ""


@pytest.fixture()
def settings_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Settings read from and saved to a tmp folder, never the user's own."""
    folder = tmp_path / "config"
    monkeypatch.setattr(settings, "config_dir", lambda: folder)
    return folder


class TestDataRoot:
    def test_the_env_override_comes_first(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, settings_dir: Path
    ) -> None:
        settings.save_setting(config.DATA_ROOT_SETTING, str(tmp_path / "saved"))
        monkeypatch.setenv(config.DATA_ROOT_ENV, str(tmp_path / "env"))
        assert config.default_data_root() == tmp_path / "env"

    def test_then_the_folder_picked_in_the_app(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, settings_dir: Path
    ) -> None:
        monkeypatch.delenv(config.DATA_ROOT_ENV, raising=False)
        settings.save_setting(config.DATA_ROOT_SETTING, str(tmp_path / "saved"))
        assert config.default_data_root() == tmp_path / "saved"

    def test_then_the_checkout_data_folder_else_documents(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, settings_dir: Path
    ) -> None:
        monkeypatch.delenv(config.DATA_ROOT_ENV, raising=False)
        root = config.repo_root()
        assert root is not None
        assert config.default_data_root() == root / "data"
        monkeypatch.setattr(config, "repo_root", lambda: None)
        monkeypatch.setattr(config, "documents_dir", lambda: tmp_path / "Documents" / "MUedit")
        assert config.default_data_root() == tmp_path / "Documents" / "MUedit"


class TestSettings:
    def test_settings_round_trip(self, settings_dir: Path) -> None:
        assert settings.load_settings() == {}
        settings.save_setting("data_root", "/somewhere")
        settings.save_setting("other", 1)
        assert settings.load_settings() == {"data_root": "/somewhere", "other": 1}
        assert [p.name for p in settings_dir.iterdir()] == ["settings.json"]

    @pytest.mark.parametrize("content", ["{not json", "[1, 2]"])
    def test_an_unreadable_file_is_no_settings(self, settings_dir: Path, content: str) -> None:
        settings_dir.mkdir()
        (settings_dir / "settings.json").write_text(content, encoding="utf-8")
        assert settings.load_settings() == {}


def test_a_second_app_does_not_get_the_lock(tmp_path: Path) -> None:
    path = tmp_path / "desktop.lock"
    first = desktop.acquire_instance_lock(path)
    assert first is not None
    try:
        assert desktop.acquire_instance_lock(path) is None
    finally:
        first.close()
    again = desktop.acquire_instance_lock(path)
    assert again is not None
    again.close()


class _FakeWindow:
    def __init__(self, picked: list[str] | None) -> None:
        self.picked = picked
        self.calls: list[tuple[Any, ...]] = []

    def create_file_dialog(self, dialog_type: int, directory: str = "") -> list[str] | None:
        self.calls.append((dialog_type, directory))
        return self.picked


@pytest.fixture()
def fake_webview(monkeypatch: pytest.MonkeyPatch) -> types.SimpleNamespace:
    """A stand-in for pywebview, which CI does not install."""
    module = types.SimpleNamespace(windows=[], FileDialog=types.SimpleNamespace(OPEN=10, FOLDER=20))
    monkeypatch.setitem(sys.modules, "webview", module)
    return module


class TestBridge:
    def test_the_page_sees_only_the_bridge_calls(self) -> None:
        api = desktop.DesktopApi(TOKEN)
        public = sorted(n for n in dir(api) if not n.startswith("_"))
        assert public == ["app_info", "choose_output_folder", "open_file", "token"]
        assert api.token() == TOKEN

    def test_open_file_answers_like_the_dialog_route(
        self, fake_webview: types.SimpleNamespace
    ) -> None:
        api = desktop.DesktopApi(TOKEN)
        assert api.open_file() == {"path": None, "name": None}
        fake_webview.windows.append(_FakeWindow(["/data/rec.otb+"]))
        assert api.open_file() == {"path": "/data/rec.otb+", "name": "rec.otb+"}
        fake_webview.windows[0].picked = None
        assert api.open_file() == {"path": None, "name": None}

    def test_a_chosen_output_folder_is_used_and_kept(
        self,
        fake_webview: types.SimpleNamespace,
        settings_dir: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "before")
        window = _FakeWindow([str(tmp_path / "chosen")])
        fake_webview.windows.append(window)
        api = desktop.DesktopApi(TOKEN)
        assert api.choose_output_folder() == {"path": str(tmp_path / "chosen")}
        assert window.calls == [(20, str(tmp_path / "before"))]
        assert tmp_path / "chosen" == config.DATA_ROOT
        assert settings.load_settings() == {"data_root": str(tmp_path / "chosen")}
        assert api.app_info()["data_root"] == str(tmp_path / "chosen")

        window.picked = None
        assert api.choose_output_folder() == {"path": None}
        assert tmp_path / "chosen" == config.DATA_ROOT


class TestNoWindow:
    def test_without_pywebview_the_launcher_is_told_to_use_the_browser(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setitem(sys.modules, "webview", None)
        assert desktop.main() == desktop.NO_WINDOW

    def test_without_a_gui_toolkit_the_server_stops_and_the_launcher_is_told(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, settings_dir: Path
    ) -> None:
        class NoToolkit(Exception):
            pass

        def start(*_: Any, **__: Any) -> None:
            raise NoToolkit("You must have either QT or GTK")

        module = types.SimpleNamespace(
            create_window=lambda *_, **__: object(),
            start=start,
            errors=types.SimpleNamespace(WebViewException=NoToolkit),
        )
        monkeypatch.setitem(sys.modules, "webview", module)
        monkeypatch.setitem(sys.modules, "webview.errors", module.errors)

        stopped: list[bool] = []

        class Server:
            token = TOKEN

            def __init__(self, _token: str) -> None:
                pass

            def start(self) -> None:
                pass

            def stop(self) -> None:
                stopped.append(True)

        monkeypatch.setattr(desktop, "_Server", Server)
        monkeypatch.setattr(desktop, "lock_path", lambda: tmp_path / "desktop.lock")
        monkeypatch.setattr(desktop, "log_to_file", lambda _path: None)
        monkeypatch.setattr(config, "DATA_ROOT", config.DATA_ROOT)
        assert desktop.main() == desktop.NO_WINDOW
        assert stopped == [True]
