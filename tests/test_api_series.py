"""The series the stages draw (``series_service``): the QC views, upload envelopes, pulse trains."""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest
from starlette.testclient import TestClient

from muedit.api.services.series_service import (
    PREVIEW_MOVING_AVG_MS,
    build_signal_views,
    moving_average_ms,
)
from muedit.decomp.preview import abs_means
from muedit.models import SignalImport
from muedit.signal.filters import bandpass_signals
from muedit.signal.pyramid import MinMaxPyramid
from muedit.storage import RamStore, SessionStore
from tests._api import API, FSAMP, GRID, N_CHANNELS, N_SAMPLES, error, frame, stored_run
from tests._frames import unpack_frame
from tests._platform import deleted

VIEW_SAMPLES = 40_011  # not a multiple of any level's bin


def _signal() -> SignalImport:
    rng = np.random.default_rng(1)
    return SignalImport(
        data=rng.normal(size=(20, VIEW_SAMPLES)).astype(np.float32),
        fsamp=FSAMP,
        grid_names=["A", "B"],
        aux_data=rng.normal(size=(2, VIEW_SAMPLES)).astype(np.float32),
    )


class TestSignalViews:
    """The preview's single pass gives what the stored QC copy gave (``_build_preview_core``)."""

    def _before(self, signal: SignalImport) -> tuple[list[np.ndarray], np.ndarray, np.ndarray]:
        """Channel means and grid overviews as computed from the float32 QC copy until stage 12."""
        qc = np.vstack(
            [
                bandpass_signals(np.asarray(signal.data[:12], np.float64), FSAMP, 1),
                bandpass_signals(np.asarray(signal.data[12:], np.float64), FSAMP, 1),
            ]
        ).astype(np.float32)
        means, overviews = [], []
        for lo, hi in ((0, 12), (12, 20)):
            grid_abs, channel_means = abs_means(qc[lo:hi].astype(np.float64))
            means.append(channel_means)
            overviews.append(moving_average_ms(grid_abs, FSAMP, PREVIEW_MOVING_AVG_MS))
        return means, np.vstack(overviews), qc

    @pytest.mark.parametrize("store_kind", ["ram", "session"])
    def test_channel_means_and_overview_are_unchanged(self, store_kind: str) -> None:
        signal = _signal()
        store = RamStore() if store_kind == "ram" else SessionStore.create("views")
        views, channel_means = build_signal_views(signal, store, [12, 8], [1, 1])
        means, overview, qc = self._before(signal)
        for got, want in zip(channel_means, means, strict=True):
            np.testing.assert_array_equal(got, want)
        np.testing.assert_array_equal(views.overview, overview)
        assert views.grid_rows == [(0, 12), (12, 20)]
        # The EMG pyramid is the pyramid of the bandpassed copy the QC stage used to keep.
        np.testing.assert_array_equal(views.emg.levels[0], _pyramid_of(qc).levels[0])
        np.testing.assert_array_equal(views.aux.levels[0], _pyramid_of(signal.aux_data).levels[0])


def _pyramid_of(x: np.ndarray) -> MinMaxPyramid:
    pyramid = MinMaxPyramid.allocate(RamStore(), "p", x.shape[0], x.shape[1])
    pyramid.write(0, np.asarray(x, np.float32))
    return pyramid


class TestSeriesRequest:
    def test_an_upload_dropped_mid_request_is_deleted_after_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from muedit.api import cache
        from muedit.api.services import series_service

        store = SessionStore.create("upload")
        signal = _signal()
        views, _ = build_signal_views(signal, store, [12, 8], [1, 1])
        token = cache.store_upload(signal, "/data/rec.mat", store, views, "drop")

        real_view = series_service.view
        seen: list[bool] = []

        def view_after_a_drop(*args: object, **kwargs: object) -> object:
            cache.release_upload("drop")  # a new file in the same tab
            seen.append(store.path.exists())
            return real_view(*args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(series_service, "view", view_after_a_drop)
        response = series_service.series_frame("emg", token, 0, 0, 64, grid=1)
        _, arrays = unpack_frame(bytes(response.body))
        assert seen == [True]
        assert arrays["max"].shape == (8, 64)
        assert deleted(store.path)


# ── /series/emg, /series/overview, /series/aux ──────────────────────────────


def _series(
    client: TestClient, kind: str, **params: Any
) -> tuple[dict[str, Any], dict[str, np.ndarray]]:
    return frame(client.get(f"{API}/series/{kind}", params=params))


class TestSeries:
    def test_emg_envelope_of_the_whole_recording(
        self, client: TestClient, upload_token: str
    ) -> None:
        meta, arrays = _series(client, "emg", upload_token=upload_token, grid=0, bins=100)
        assert meta["kind"] == "envelope"
        assert (meta["start"], meta["end"], meta["bins"]) == (0, N_SAMPLES, 100)
        assert meta["factor"] == 16  # the coarsest level with at least one bin per output bin
        assert (meta["total_samples"], meta["fsamp"], meta["grid"]) == (N_SAMPLES, FSAMP, 0)
        assert len(meta["names"]) == N_CHANNELS
        assert arrays["min"].dtype == np.float32
        assert arrays["min"].shape == arrays["max"].shape == (N_CHANNELS, 100)
        assert (arrays["min"] <= arrays["max"]).all()

    @pytest.mark.parametrize("grid", [-1, 1])
    def test_grid_out_of_range_is_400(
        self, client: TestClient, upload_token: str, grid: int
    ) -> None:
        resp = client.get(f"{API}/series/emg", params={"upload_token": upload_token, "grid": grid})
        err = error(resp, 400)
        assert (err["message"], err["detail"]) == (
            f"grid {grid} is out of range [0, 1)",
            {"field": "grid"},
        )

    def test_emg_samples_when_zoomed_in(self, client: TestClient, upload_token: str) -> None:
        meta, arrays = _series(
            client, "emg", upload_token=upload_token, start=1000, end=1200, bins=512
        )
        assert (meta["kind"], meta["factor"]) == ("samples", 1)
        assert arrays["samples"].shape == (N_CHANNELS, 200)

    def test_overview_has_one_row_per_grid(self, client: TestClient, upload_token: str) -> None:
        meta, arrays = _series(client, "overview", upload_token=upload_token, bins=256)
        assert meta["names"] == [GRID]
        assert arrays["max"].shape == (1, 256)
        assert (arrays["max"] >= 0).all()  # a mean of |EMG|

    def test_aux_without_channels_is_empty(self, client: TestClient, upload_token: str) -> None:
        meta, arrays = _series(client, "aux", upload_token=upload_token, bins=64)
        assert meta["names"] == []
        assert arrays["max"].shape == (0, 64)

    @pytest.mark.parametrize(
        ("params", "status", "code"),
        [
            ({"upload_token": "expired"}, 400, None),
            ({"grid": 1}, 400, None),
            ({"bins": 0}, 422, "validation_error"),
            ({"end": -1}, 422, "validation_error"),
        ],
        ids=["bad-token", "bad-grid", "no-bins", "negative-end"],
    )
    def test_rejected(
        self,
        client: TestClient,
        upload_token: str,
        params: dict[str, Any],
        status: int,
        code: str | None,
    ) -> None:
        query = {"upload_token": upload_token, **params}
        error(client.get(f"{API}/series/emg", params=query), status, code)

    def test_missing_token_is_422(self, client: TestClient) -> None:
        error(client.get(f"{API}/series/emg"), 422, "validation_error")


# ── /series/pulse of a run ──────────────────────────────────────────────────


class TestRunPulse:
    def test_the_runs_train_and_discharges(self, client: TestClient) -> None:
        trains = np.random.default_rng(3).random((2, N_SAMPLES)).astype(np.float32)
        spikes = [[100, 2500, 5000], [300]]
        token = stored_run(spikes, trains)
        meta, arrays = frame(
            client.get(f"{API}/series/pulse", params={"token": token, "mu": 0, "bins": 100})
        )
        assert (meta["kind"], meta["version"], meta["flagged"]) == ("envelope", 0, False)
        np.testing.assert_array_equal(arrays["max"][0], trains[0].reshape(100, -1).max(axis=1))
        np.testing.assert_array_equal(arrays["min"][0], trains[0].reshape(100, -1).min(axis=1))
        assert arrays["spikes"].tolist() == spikes[0]
        np.testing.assert_array_equal(arrays["spike_values"], trains[0, spikes[0]])

    @pytest.mark.parametrize("mu", [-1, 2])
    def test_mu_out_of_range_is_400(self, client: TestClient, mu: int) -> None:
        token = stored_run([[100]], np.zeros((1, N_SAMPLES), np.float32))
        resp = client.get(f"{API}/series/pulse", params={"token": token, "mu": mu})
        assert error(resp, 400)["detail"]["field"] == "mu"

    def test_an_unknown_token_is_400(self, client: TestClient) -> None:
        resp = client.get(f"{API}/series/pulse", params={"token": "gone", "mu": 0})
        assert error(resp, 400)["detail"]["field"] == "token"
