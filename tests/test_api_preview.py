"""Loading a recording, its automatic QC and its target segmentation (``preview_service``)."""

from __future__ import annotations

import itertools
import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import scipy.io
from httpx import Response
from starlette.testclient import TestClient

from muedit.api import cache
from muedit.api.services.series_service import build_signal_views
from muedit.io.bids import export_bids_emg
from muedit.models import SignalImport
from muedit.signal.filters import bandpass_signals
from muedit.signal.grid import format_hdemg_signal
from muedit.storage import SessionStore
from tests._api import (
    API,
    FSAMP,
    GRID,
    N_CHANNELS,
    N_SAMPLES,
    SESSION_HEADER,
    error,
    ok,
    write_recording,
)
from tests._io_files import write_mat


@pytest.fixture(scope="module")
def target_upload(client: TestClient, tmp_path_factory: pytest.TempPathFactory) -> str:
    """Upload of a recording whose aux channels are a flat accelerometer and a trapezoid target."""
    n = int(16 * FSAMP)
    t = np.arange(n) / FSAMP
    target = np.interp(t, [0, 2, 5, 11, 14, 16], [0, 0, 20, 20, 0, 0])
    path = write_recording(
        tmp_path_factory.mktemp("target") / "synthetic_target.mat",
        np.random.default_rng(2).normal(0, 1, (N_CHANNELS, n)).astype(np.float32),
        aux=np.vstack([np.zeros(n), target]),
        aux_names=["Acc", "Target"],
    )
    data = ok(
        client.post(
            f"{API}/preview-by-path",
            json={"path": str(path)},
            headers={SESSION_HEADER: "fixture-target"},
        )
    )
    assert data["target_index"] == 1
    return data["upload_token"]


# ── /preview-by-path ────────────────────────────────────────────────────────


PREVIEW_KEYS = {
    "upload_token",
    "grid_names",
    "total_samples",
    "fsamp",
    "channel_means",
    "coordinates",
    "metadata",
    "muscle_names",
    "aux_names",
}


def _check_preview(data: dict[str, Any]) -> None:
    assert set(data) >= PREVIEW_KEYS
    assert isinstance(data["upload_token"], str) and data["upload_token"]
    assert data["grid_names"] == [GRID]
    assert data["total_samples"] == N_SAMPLES
    assert data["fsamp"] == FSAMP
    assert len(data["channel_means"]) == 1 and len(data["channel_means"][0]) == N_CHANNELS
    assert len(data["coordinates"]) == 1 and len(data["coordinates"][0]) == N_CHANNELS
    # Series travel as viewport envelopes (/series/*), never as JSON lists.
    assert not {"mean_abs", "grid_mean_abs", "auxiliary"} & set(data)


class TestPreview:
    def test_by_path(self, client: TestClient, signal_mat: Path) -> None:
        _check_preview(ok(client.post(f"{API}/preview-by-path", json={"path": str(signal_mat)})))

    def test_by_path_bids_recording_brings_its_sidecar_fields(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """A recording named ``<label>_emg.edf`` is found by its label, not by its file stem."""
        coordinates, ied, discard, _ = format_hdemg_signal(["GR08MM1305"])
        out = export_bids_emg(
            np.random.default_rng(0).normal(size=(64, 4 * 2048)) * 0.05,
            2048.0,
            ["GR08MM1305"],
            coordinates,
            discard,
            tmp_path / "study",
            ied=ied,
            subject="01",
            task="ramp",
            powerline_freq=60,
            placement_scheme_description="Grid over the muscle belly.",
            reference_location="wet band around the ankle",
            ground_location="wet band around the wrist",
        )
        data = ok(client.post(f"{API}/preview-by-path", json={"path": str(out["edf"])}))
        assert data["reference_location"] == "wet band around the ankle"
        assert data["ground_location"] == "wet band around the wrist"
        assert data["powerline_freq"] == 60

    def test_by_path_missing_body_is_422(self, client: TestClient) -> None:
        err = error(client.post(f"{API}/preview-by-path", json={}), 422, "validation_error")
        assert isinstance(err["detail"], list)

    def test_by_path_without_json_content_type_is_422(self, client: TestClient) -> None:
        body = json.dumps({"path": "x.mat"}).encode()
        resp = client.post(f"{API}/preview-by-path", content=body, headers={"content-type": ""})
        error(resp, 422, "validation_error")

    def test_by_path_nonexistent_file_is_404(self, client: TestClient, tmp_path: Path) -> None:
        missing = str(tmp_path / "nope.mat")
        err = error(client.post(f"{API}/preview-by-path", json={"path": missing}), 404)
        assert (err["message"], err["detail"]) == (f"File not found: {missing}", {"field": "path"})

    def test_by_path_unsupported_format_is_400(self, client: TestClient, tmp_path: Path) -> None:
        path = tmp_path / "notes.txt"
        path.write_text("not a recording")
        err = error(client.post(f"{API}/preview-by-path", json={"path": str(path)}), 400)
        assert err["detail"] == {"field": "path"}
        assert "Unsupported file format" in err["message"]

    def test_by_path_unknown_grid_is_400(self, client: TestClient, tmp_path: Path) -> None:
        path = tmp_path / "unknown_grid.mat"
        emg = np.zeros((N_CHANNELS, N_SAMPLES))
        scipy.io.savemat(
            path,
            {"signal": {"data": emg, "fsamp": FSAMP, "gridname": "NOT-A-GRID", "muscle": "ta"}},
        )
        err = error(client.post(f"{API}/preview-by-path", json={"path": str(path)}), 400)
        assert err["message"].startswith("Unknown grid model 'NOT-A-GRID'")

    def test_by_path_corrupt_recording_is_400(self, client: TestClient, tmp_path: Path) -> None:
        path = tmp_path / "corrupt.otb+"
        path.write_bytes(b"not an archive")
        err = error(client.post(f"{API}/preview-by-path", json={"path": str(path)}), 400)
        assert err["detail"] == {"field": "path"}
        assert err["message"].startswith("Failed to extract OTB+ file")

    @pytest.mark.parametrize("version", ["v5", "v7.3"])
    @pytest.mark.parametrize(
        ("struct", "reason"),
        [
            ("emg", "Key 'signal' not found in MAT file."),
            (
                "signal",
                "MAT file is a MUedit decomposition (signal.Pulsetrain + signal.Dischargetimes); "
                "open it in edit mode.",
            ),
        ],
        ids=["no-signal-struct", "decomposition-fields"],
    )
    def test_by_path_mat_without_a_recording_is_400(
        self, client: TestClient, tmp_path: Path, version: str, struct: str, reason: str
    ) -> None:
        fields = {"Pulsetrain": np.zeros((1, 10)), "Dischargetimes": np.zeros((1, 3))}
        path = write_mat(tmp_path / f"{struct}_{version}.mat", version, struct, fields)
        err = error(client.post(f"{API}/preview-by-path", json={"path": str(path)}), 400)
        assert (err["message"], err["detail"]) == (reason, {"field": "path"})


# ── /qc/auto ────────────────────────────────────────────────────────────────


def _correlated_emg(n_grids: int, n_samples: int) -> np.ndarray:
    """Band-limited noise with a shared component, so every channel correlates with the others."""
    rng = np.random.default_rng(0)
    common = rng.normal(0, 1, (1, n_samples))
    raw = 0.8 * common + 0.6 * rng.normal(0, 1, (N_CHANNELS * n_grids, n_samples))
    return (0.05 * bandpass_signals(raw.astype(np.float32), FSAMP, emg_type=1)).astype(np.float64)


def _qc_upload(data: np.ndarray, n_grids: int) -> str:
    """Keep ``data`` as an upload of ``n_grids`` grids, as /preview-by-path keeps a file."""
    signal = SignalImport(
        data=data, fsamp=FSAMP, grid_names=[GRID] * n_grids, aux_data=np.zeros((0, data.shape[1]))
    )
    store = SessionStore.create("upload")
    views, _ = build_signal_views(signal, store, [N_CHANNELS] * n_grids, [1] * n_grids)
    return cache.store_upload(signal, "/data/rec.mat", store, views)


class TestQcAuto:
    def test_finds_the_dead_channel_and_the_transient(self, client: TestClient) -> None:
        data = _correlated_emg(n_grids=2, n_samples=20_000)
        data[N_CHANNELS + 7] = 0.0
        data[:, 10_000:10_100] += 50.0
        token = _qc_upload(data, n_grids=2)
        out = ok(client.post(f"{API}/qc/auto", json={"upload_token": token}))
        assert set(out) == {"bad_channels_per_grid", "artifact_regions"}
        bad = out["bad_channels_per_grid"]
        assert [len(g) for g in bad] == [N_CHANNELS, N_CHANNELS]
        assert set(bad[0]) <= {0, 1} and bad[1][7] == 1
        assert any(s <= 10_050 < e for s, e in out["artifact_regions"])

    def test_bad_token(self, client: TestClient) -> None:
        err = error(client.post(f"{API}/qc/auto", json={"upload_token": "expired"}), 400)
        assert err["detail"]["field"] == "upload_token"


# ── /qc/segment ─────────────────────────────────────────────────────────────


class TestQcSegment:
    def _segment(self, client: TestClient, token: str, **extra: Any) -> Response:
        return client.post(f"{API}/qc/segment", json={"upload_token": token, **extra})

    @pytest.mark.parametrize("extra", [{}, {"target_index": 1}], ids=["by-name", "by-index"])
    def test_trapezoid_target(
        self, client: TestClient, target_upload: str, extra: dict[str, Any]
    ) -> None:
        data = ok(self._segment(client, target_upload, **extra))
        assert (data["target_index"], data["target_name"]) == (1, "Target")
        segments = data["segments"]
        assert [(s["kind"], s["direction"]) for s in segments] == [
            ("ramp", "up"),
            ("plateau", None),
            ("ramp", "down"),
        ]
        assert all(a["end"] == b["start"] for a, b in itertools.pairwise(segments))
        assert segments[1]["start"] / FSAMP == pytest.approx(5, abs=0.15)

    def test_preview_without_target_names_none(self, client: TestClient, signal_mat: Path) -> None:
        data = ok(client.post(f"{API}/preview-by-path", json={"path": str(signal_mat)}))
        assert data["target_index"] is None

    def test_no_auxiliary_channels(self, client: TestClient, upload_token: str) -> None:
        err = error(self._segment(client, upload_token), 400)
        assert err["detail"]["field"] == "target_index"

    @pytest.mark.parametrize("index", [0, 2], ids=["flat-channel", "out-of-range"])
    def test_rejected_channel(self, client: TestClient, target_upload: str, index: int) -> None:
        err = error(self._segment(client, target_upload, target_index=index), 400)
        assert err["detail"]["field"] == "target_index"

    def test_bad_token(self, client: TestClient) -> None:
        err = error(self._segment(client, "expired"), 400)
        assert err["detail"]["field"] == "upload_token"
