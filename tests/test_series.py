"""Min/max pyramids and the QC stage's series, against the whole-array computations (stage 12)."""

from __future__ import annotations

import numpy as np
import pytest

from muedit.api.services.series_service import build_signal_views
from muedit.decomp.preview import abs_means
from muedit.io.store import RamStore, SessionStore
from muedit.models import SignalImport
from muedit.signal.downsample import PREVIEW_MOVING_AVG_MS, moving_average_ms
from muedit.signal.filters import bandpass_signals
from muedit.signal.pyramid import MinMaxPyramid, pyramid_factors, view
from tests._platform import deleted

FSAMP = 2048.0
N_SAMPLES = 40_011  # not a multiple of any level's bin


def _series(n_rows: int = 5) -> np.ndarray:
    return np.random.default_rng(0).normal(size=(n_rows, N_SAMPLES)).astype(np.float32)


def _pyramid(x: np.ndarray) -> MinMaxPyramid:
    pyramid = MinMaxPyramid.allocate(RamStore(), "p", x.shape[0], x.shape[1])
    pyramid.write(0, x[:2])
    pyramid.write(2, x[2:])
    return pyramid


class TestPyramid:
    def test_levels_are_the_min_and_max_of_their_bins(self) -> None:
        x = _series()
        pyramid = _pyramid(x)
        assert pyramid.factors == [16, 64, 256]  # a level keeps at least 64 bins
        for factor, level in zip(pyramid.factors, pyramid.levels, strict=True):
            for b in (0, 7, level.shape[2] - 1):  # the last bin is short
                chunk = x[:, b * factor : (b + 1) * factor]
                np.testing.assert_array_equal(level[0, :, b], chunk.min(axis=1))
                np.testing.assert_array_equal(level[1, :, b], chunk.max(axis=1))

    def test_short_series_have_no_levels(self) -> None:
        assert pyramid_factors(16 * 64 - 1) == []

    @pytest.mark.parametrize(
        ("start", "end", "bins"),
        [(0, N_SAMPLES, 1000), (123, 39_000, 97), (5, 20_000, 1), (100, 900, 200)],
    )
    def test_each_bin_holds_its_samples_and_at_most_one_level_bin_more(
        self, start: int, end: int, bins: int
    ) -> None:
        x = _series()
        got = view(_pyramid(x), lambda r, a, b: x[r, a:b], slice(1, 4), start, end, bins)
        assert got.mins is not None and got.maxs is not None
        assert got.mins.shape == (3, bins)
        edges = start + (np.arange(bins + 1) * (end - start)) // bins
        f = got.factor
        for b in range(bins):
            inside = x[1:4, edges[b] : edges[b + 1]]
            around = x[1:4, max(0, edges[b] - f) : edges[b + 1] + f]
            assert (got.mins[:, b] <= inside.min(axis=1)).all()
            assert (got.maxs[:, b] >= inside.max(axis=1)).all()
            assert (got.mins[:, b] >= around.min(axis=1)).all()
            assert (got.maxs[:, b] <= around.max(axis=1)).all()
            if f == 1:  # from the samples themselves: exact
                np.testing.assert_array_equal(got.maxs[:, b], inside.max(axis=1))

    def test_a_short_window_is_its_samples(self) -> None:
        x = _series()
        got = view(_pyramid(x), lambda r, a, b: x[r, a:b], slice(0, 5), 10, 60, 64)
        assert got.samples is not None and got.factor == 1
        np.testing.assert_array_equal(got.samples, x[:, 10:60])


def _signal() -> SignalImport:
    rng = np.random.default_rng(1)
    return SignalImport(
        data=rng.normal(size=(20, N_SAMPLES)).astype(np.float32),
        fsamp=FSAMP,
        gridname=["A", "B"],
        auxiliary=rng.normal(size=(2, N_SAMPLES)).astype(np.float32),
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
        np.testing.assert_array_equal(views.aux.levels[0], _pyramid_of(signal.auxiliary).levels[0])


def _pyramid_of(x: np.ndarray) -> MinMaxPyramid:
    pyramid = MinMaxPyramid.allocate(RamStore(), "p", x.shape[0], x.shape[1])
    pyramid.write(0, np.asarray(x, np.float32))
    return pyramid


class TestSeriesRequest:
    def test_an_upload_dropped_mid_request_is_deleted_after_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from muedit.api import cache
        from muedit.api.binary import unpack_frame
        from muedit.api.services import series_service

        store = SessionStore.create("upload")
        signal = _signal()
        views, _ = build_signal_views(signal, store, [12, 8], [1, 1])
        token = cache._store_upload_signal(signal, session="drop", store=store, views=views)

        real_view = series_service.view
        seen: list[bool] = []

        def view_after_a_drop(*args: object, **kwargs: object) -> object:
            cache._release_upload("drop")  # a new file in the same tab
            seen.append(store.path.exists())
            return real_view(*args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(series_service, "view", view_after_a_drop)
        response = series_service.series_frame("emg", token, 0, 0, 64, grid=1)
        _, arrays = unpack_frame(bytes(response.body))
        assert seen == [True]
        assert arrays["max"].shape == (8, 64)
        assert deleted(store.path)
