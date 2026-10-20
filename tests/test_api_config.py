"""Output folders: the data root and the project folders under it."""

from __future__ import annotations

from pathlib import Path

import pytest

import muedit.api.config as config


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

    def test_project_of_names_the_folder_under_the_data_root(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "data")
        assert config.project_of(tmp_path / "data" / "study1") == "study1"
        assert config.project_of(tmp_path / "data" / "lab" / "study1") == "lab"
        assert config.project_of(tmp_path / "data") == ""
        assert config.project_of(tmp_path / "elsewhere" / "study1") == ""

    def test_list_projects_keeps_the_bids_datasets(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        for name in ["study2", "study1", ".hidden"]:
            (tmp_path / name).mkdir()
            (tmp_path / name / "dataset_description.json").write_text("{}")
        (tmp_path / "Intan").mkdir()
        (tmp_path / "recording.otb4").write_text("")
        monkeypatch.setattr(config, "DATA_ROOT", tmp_path)
        assert config.list_projects() == ["study1", "study2"]

    def test_list_projects_is_empty_without_a_data_root(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "missing")
        assert config.list_projects() == []


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
