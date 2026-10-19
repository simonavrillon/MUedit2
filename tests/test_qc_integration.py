"""Tests for how the automatic QC pipeline is wired into the app."""

from __future__ import annotations

from collections.abc import Iterator

import numpy as np
import pytest
from starlette.testclient import TestClient

from muedit.decomp.types import LoadStepOutput
from muedit.models import SignalImport
from muedit.signal.filters import bandpass_signals

FSAMP = 2000.0
N_CHANNELS = 64
N_SAMPLES = 20_000
GRID = "GR08MM1305"


def _raw_emg(ngrid: int = 1, seed: int = 0) -> np.ndarray:
    """Band-limited noise with a shared component (all channels well correlated)."""
    rng = np.random.default_rng(seed)
    n_ch = N_CHANNELS * ngrid
    common = rng.normal(0, 1, (1, N_SAMPLES))
    raw = 0.8 * common + 0.6 * rng.normal(0, 1, (n_ch, N_SAMPLES))
    return (0.05 * bandpass_signals(raw.astype(np.float32), FSAMP, emg_type=1)).astype(np.float64)


def _loaded(data: np.ndarray, grids: list[str]) -> LoadStepOutput:
    signal = SignalImport(
        data=data,
        fsamp=FSAMP,
        grid_names=grids,
        muscle_names=["ta"] * len(grids),
        aux_data=np.zeros((0, data.shape[1])),
    )
    return LoadStepOutput(full_path="synthetic.mat", signal=signal)


# ── build_manual_artifact_mask ───────────────────────────────────────────────


# ── preprocess_step ──────────────────────────────────────────────────────────


# ── POST /api/v1/qc/auto ─────────────────────────────────────────────────────


@pytest.fixture()
def api_client() -> Iterator[TestClient]:
    from fastapi import FastAPI

    from muedit.api.routes import include_routers

    app = FastAPI()
    include_routers(app)
    with TestClient(app) as client:
        yield client


def _qc_token(data: np.ndarray, grids: list[str]) -> str:
    """Cache ``data`` as an upload; /qc/auto bandpasses it, as for a file."""
    from muedit.api.cache import _store_upload_signal

    return _store_upload_signal(_loaded(data, grids).signal)


class TestQcAutoRoute:
    def test_returns_bad_channels_and_artifact_regions(self, api_client: TestClient) -> None:
        data = _raw_emg(ngrid=2)
        data[N_CHANNELS + 7] = 0.0
        data[:, 10_000:10_100] += 50.0
        token = _qc_token(data, [GRID, GRID])

        resp = api_client.post("/api/v1/qc/auto", json={"upload_token": token})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["meta"]["api_version"] == "v1"
        out = body["data"]

        bad = out["bad_channels_per_grid"]
        assert [len(g) for g in bad] == [N_CHANNELS, N_CHANNELS]
        assert set(bad[0]) <= {0, 1} and set(bad[1]) <= {0, 1}
        assert bad[1][7] == 1

        regions = out["artifact_regions"]
        assert regions, "full-montage transient was not detected"
        assert any(s <= 10_050 < e for s, e in regions)


# ── NPZ persistence ──────────────────────────────────────────────────────────
