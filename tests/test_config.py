"""Output folders: the data root and the project folders under it."""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

import muedit.api.config as config
from muedit.api.app_factory import create_app
from muedit.api.routes import include_routers

API = "/api/v1"


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
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MUEDIT_CACHE_DIR", str(tmp_path / "cache"))
        monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "data")
        app = create_app()
        include_routers(app)
        with TestClient(app) as client:
            r = client.post(
                f"{API}/decompose_stream",
                data={"bids_export": "true", "project": "../outside"},
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


class TestDataRoot:
    def test_the_env_override_comes_first(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv(config.DATA_ROOT_ENV, str(tmp_path / "env"))
        assert config.default_data_root() == tmp_path / "env"

    def test_then_the_checkout_data_folder_else_documents(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.delenv(config.DATA_ROOT_ENV, raising=False)
        root = config.repo_root()
        assert root is not None
        assert config.default_data_root() == root / "data"
        monkeypatch.setattr(config, "repo_root", lambda: None)
        monkeypatch.setattr(config, "documents_dir", lambda: tmp_path / "Documents" / "MUedit")
        assert config.default_data_root() == tmp_path / "Documents" / "MUedit"
