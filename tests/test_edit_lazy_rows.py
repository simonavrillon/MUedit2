"""Lazy edit load: the pulse-train matrix stays on the server, rows are fetched one at a time."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from starlette.testclient import TestClient

API = "/api/v1"
FSAMP = 2000.0
N_SAMPLES = 6000
N_CHANNELS = 64
GRID = "GR08MM1305"
FILE_LABEL = "sub-04_task-lazy_decomp.npz"
ENTITY = "sub-04_task-lazy"
LAZY = {"x-muedit-lazy-rows": "1"}


@pytest.fixture(scope="module")
def workspace(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    import muedit.api.config as config
    import muedit.api.services.editing_service as editing_service

    root = tmp_path_factory.mktemp("edit_lazy")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(config, "DATA_ROOT", root)
        mp.setattr(editing_service, "DATA_ROOT", root)
        mp.chdir(root)
        yield root


@pytest.fixture(scope="module")
def client(workspace: Path) -> Iterator[TestClient]:
    from muedit.api.app_factory import create_app
    from muedit.api.routes import include_routers

    app = create_app()
    include_routers(app)
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture(scope="module")
def source_pulse() -> np.ndarray:
    rng = np.random.default_rng(23)
    return rng.standard_normal((2, N_SAMPLES)) * rng.uniform(1.0, 300.0, (2, 1))


@pytest.fixture(scope="module")
def decomp_npz(workspace: Path, source_pulse: np.ndarray) -> Path:
    from muedit.decomp.decomposition_file import pack_object_array, save_decomposition_npz

    path = workspace / FILE_LABEL
    save_decomposition_npz(
        path,
        pulse_trains=source_pulse,
        distimes=[[1000, 1400, 1800, 2200, 2600, 3000], [1200, 2000, 2800]],
        fsamp=FSAMP,
        grid_names=[GRID],
        mu_grid_index=[0, 0],
        muscles=["ta"],
        parameters={},
        total_samples=N_SAMPLES,
        extras={
            "emg_data": np.random.default_rng(0).normal(0, 1, (N_CHANNELS, N_SAMPLES)),
            "discard_channels": pack_object_array([np.zeros(N_CHANNELS, dtype=int)]),
            "coordinates": pack_object_array([np.zeros((N_CHANNELS, 2))]),
        },
    )
    return path


def _lazy_load(client: TestClient, path: Path) -> dict[str, Any]:
    resp = client.post(f"{API}/edit/load-by-path", json={"path": str(path)}, headers=LAZY)
    assert resp.status_code == 200, resp.text
    data: dict[str, Any] = resp.json()["data"]
    return data


def _row(client: TestClient, meta: dict[str, Any], uid: str, rev: int = 0) -> Any:
    return client.post(
        f"{API}/edit/session/row",
        json={"session_token": meta["session_token"], "mu_uid": uid, "row_rev": rev},
    )


class TestLazyLoad:
    def test_no_matrix_is_sent_but_the_metadata_and_token_are(
        self, client: TestClient, decomp_npz: Path
    ) -> None:
        meta = _lazy_load(client, decomp_npz)
        assert meta["lazy_rows"] is True
        assert "pulse_trains_full" not in meta
        assert meta["session_token"] and meta["mu_uids"] == ["g0_mu0", "g0_mu1"]
        assert meta["total_samples"] == N_SAMPLES
        assert len(meta["distime_all"]) == 2

    def test_without_the_header_the_matrix_is_still_sent(
        self, client: TestClient, decomp_npz: Path
    ) -> None:
        resp = client.post(f"{API}/edit/load-by-path", json={"path": str(decomp_npz)})
        assert resp.content[:4] == b"MELD"


class TestRowEndpoint:
    def test_a_row_is_bit_identical_to_the_float64_source(
        self, client: TestClient, decomp_npz: Path, source_pulse: np.ndarray
    ) -> None:
        meta = _lazy_load(client, decomp_npz)
        resp = _row(client, meta, "g0_mu1")
        assert resp.status_code == 200
        assert resp.headers["x-muedit-format"] == "edit-row-f64-v1"
        row = np.frombuffer(resp.content, dtype="<f8")
        assert row.shape == (N_SAMPLES,)
        assert np.array_equal(row, source_pulse[1])  # no float32 narrowing left in the path

    def test_a_recomputed_revision_is_served_and_revision_zero_is_kept(
        self, client: TestClient, decomp_npz: Path, source_pulse: np.ndarray
    ) -> None:
        meta = _lazy_load(client, decomp_npz)
        resp = client.post(
            f"{API}/edit/update-filter",
            json={
                "project": "lazy",
                "edit_signal_token": meta["edit_signal_token"],
                "file_label": FILE_LABEL,
                "distimes": meta["distime_all"],
                "mu_index": 0,
                "view_start": 500,
                "view_end": 4500,
                "nbextchan": 200,
                "session_token": meta["session_token"],
                "mu_uid": "g0_mu0",
                "omit_pulse_train": True,
            },
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert "pulse_train" not in data  # the editor fetches the row instead
        assert data["row_rev"] == 1
        new = np.frombuffer(_row(client, meta, "g0_mu0", 1).content, dtype="<f8")
        old = np.frombuffer(_row(client, meta, "g0_mu0", 0).content, dtype="<f8")
        assert not np.array_equal(new, old)
        assert np.array_equal(old, source_pulse[0])

    def test_errors_say_what_is_wrong(self, client: TestClient, decomp_npz: Path) -> None:
        meta = _lazy_load(client, decomp_npz)
        assert _row(client, meta, "g9_mu9").status_code == 400
        assert _row(client, meta, "g0_mu0", 7).status_code == 409
        bad = client.post(
            f"{API}/edit/session/row",
            json={"session_token": "nope", "mu_uid": "g0_mu0", "row_rev": 0},
        )
        assert bad.status_code == 404
        assert "reopen the file" in bad.text


class TestEditsUseTheSessionRow:
    def test_delete_spikes_reads_the_row_from_the_session(
        self, client: TestClient, decomp_npz: Path, source_pulse: np.ndarray
    ) -> None:
        meta = _lazy_load(client, decomp_npz)
        via_session = client.post(
            f"{API}/edit/delete-spikes",
            json={
                "distimes": meta["distime_all"],
                "mu_index": 0,
                "session_token": meta["session_token"],
                "mu_uid": "g0_mu0",
                "row_rev": 0,
                "x_start": 900,
                "x_end": 2000,
                "y_min": -1e9,
                "y_max": 1e9,
            },
        )
        uploaded = client.post(
            f"{API}/edit/delete-spikes",
            json={
                "distimes": meta["distime_all"],
                "mu_index": 0,
                "pulse_train": source_pulse[0].tolist(),
                "x_start": 900,
                "x_end": 2000,
                "y_min": -1e9,
                "y_max": 1e9,
            },
        )
        assert via_session.status_code == 200, via_session.text
        assert via_session.json()["data"] == uploaded.json()["data"]

    def test_an_edit_without_any_pulse_train_is_a_400(
        self, client: TestClient, decomp_npz: Path
    ) -> None:
        meta = _lazy_load(client, decomp_npz)
        resp = client.post(
            f"{API}/edit/remove-outliers",
            json={"distimes": meta["distime_all"], "mu_index": 0, "fsamp": FSAMP},
        )
        assert resp.status_code == 400
