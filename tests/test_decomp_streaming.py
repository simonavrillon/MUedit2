"""Chunked, streamed and stored decomposition passes against their whole-array definitions."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from muedit.adapt_decomp.adaptation import AdaptiveDecomp
from muedit.adapt_decomp.config import Config
from muedit.decomp import algorithm
from muedit.decomp.algorithm import (
    column_energy,
    covariance,
    pca_extended_signal,
    subtract_mu_waveforms,
    whiten_extended_signal,
)
from muedit.decomp.core import decompose_step
from muedit.decomp.pipeline import run_decomposition
from muedit.decomp.postprocess import postprocess_step
from muedit.decomp.preprocess import load_step, preprocess_step
from muedit.decomp.preview import abs_means
from muedit.decomp.types import (
    POSTPROCESS_MODES,
    DecompositionParameters,
    PostprocessMode,
    PreprocessStepOutput,
)
from muedit.io.store import SessionStore
from muedit.models import SignalImport, resident_nbytes
from muedit.signal.decomp_primitives import extend_signal
from muedit.signal.filters import (
    bandpass_inplace,
    bandpass_signals,
    notch_inplace,
    notch_signals,
)
from muedit.signal.streaming import RowSelection, StreamedExtender, extend_mask
from tests._synthetic_emg import FSAMP, motor_unit_emg

RTOL = 1e-10


@pytest.fixture
def small_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force several column chunks on test-sized inputs."""
    monkeypatch.setattr(algorithm, "_CHUNK_BYTES", 64 * 1024)


@pytest.fixture(scope="module")
def extended() -> np.ndarray:
    """A demeaned 8-channel window extended by 8 (64 rows x ~6k columns)."""
    emg = motor_unit_emg(seed=3, n_samples=6000, activity=(0, 6000))[:8].astype(np.float64)
    return extend_signal(emg - emg.mean(axis=1, keepdims=True), 8)


@pytest.mark.usefixtures("small_chunks")
class TestChunkedFastIca:
    """Chunked covariance and cached energy equal their whole-array definitions."""

    def test_covariance_matches_np_cov(self, extended: np.ndarray) -> None:
        np.testing.assert_allclose(covariance(extended), np.cov(extended, bias=True), rtol=RTOL)

    def test_covariance_on_columns_matches_fancy_index(self, extended: np.ndarray) -> None:
        cols = np.flatnonzero(np.arange(extended.shape[1]) % 7 != 3)
        np.testing.assert_allclose(
            covariance(extended, cols), np.cov(extended[:, cols], bias=True), rtol=RTOL
        )

    def test_column_energy_is_exact(self, extended: np.ndarray) -> None:
        np.testing.assert_array_equal(column_energy(extended), np.sum(extended**2, axis=0))

    def test_peel_off_without_valid_spikes_leaves_x(self, extended: np.ndarray) -> None:
        work = extended.copy()
        subtract_mu_waveforms(work, np.array([0, extended.shape[1] - 1]), 2000.0, 0.025)
        np.testing.assert_array_equal(work, extended)


# ── Streamed passes ──────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def raw() -> np.ndarray:
    """Ten channels x 3000 samples of synthetic MU activity (float64)."""
    return motor_unit_emg(seed=5, n_samples=3000, activity=(0, 3000))[:10].astype(np.float64)


EX = 6


class TestStreamedExtender:
    """Every read equals the matching slice of a whole-recording extension."""

    SPANS = ((0, 40), (2, 9), (EX - 1, 300), (1234, 1500), (2900, 3000))

    @pytest.mark.parametrize(("start", "stop"), SPANS)
    def test_channels_first_matches_full_extension(
        self, raw: np.ndarray, start: int, stop: int
    ) -> None:
        rows = np.array([0, 2, 3, 7, 9])
        offset = raw[rows].mean(axis=1)
        full = extend_signal(raw[rows] - offset[:, None], EX)
        source = StreamedExtender(RowSelection(raw, rows), EX, offset=offset)
        np.testing.assert_array_equal(source.read(start, stop), full[:, start:stop])

    @pytest.mark.parametrize(("start", "stop"), SPANS)
    def test_samples_first_float32_matches_full_extension(
        self, raw: np.ndarray, start: int, stop: int
    ) -> None:
        offset = raw.mean(axis=1)
        demeaned = raw.astype(np.float32) - offset.astype(np.float32)[:, None]
        full = extend_signal(demeaned.T, EX, samples_first=True)
        source = StreamedExtender(raw, EX, offset=offset, dtype=np.float32, samples_first=True)
        out = source.read(start, stop)
        assert out.dtype == np.float32
        np.testing.assert_array_equal(out, full[start:stop])

    def test_mask_is_extended_cut_with_the_batch_and_padded_past_its_end(
        self, raw: np.ndarray
    ) -> None:
        mask = np.zeros(2000, dtype=bool)
        mask[100:110] = True
        source = StreamedExtender(raw, EX, artifact_mask=mask)
        np.testing.assert_array_equal(source.mask(95, 105), [False] * 5 + [True] * 5)
        # 110 .. 110 + EX - 2 still hold masked samples in their look-back.
        np.testing.assert_array_equal(source.mask(108, 118), [True] * 7 + [False] * 3)
        np.testing.assert_array_equal(source.mask(1995, 2005), [False] * 10)
        assert StreamedExtender(raw, EX).mask(0, 10) is None

    def test_extend_mask_matches_the_decompose_step_construction(self) -> None:
        mask = np.zeros(300, dtype=bool)
        mask[[0, 40, 41, 150, 299]] = True
        # core.decompose_step: OR of the mask shifted by every delay, cut to the window.
        expected = np.zeros(mask.size + EX - 1, dtype=bool)
        for m in range(EX):
            expected[m : m + mask.size] |= mask
        np.testing.assert_array_equal(extend_mask(mask, EX), expected[: mask.size])


@pytest.mark.parametrize(
    ("n_samples", "edges_sec", "ex_factor", "expected"),
    [
        (2000, 0.2, 16, 400),  # edges_sec, wider than the 15 zero-padded columns
        (2000, 0.0, 16, 15),  # no edges: the padded columns still go
        (2000, 0.002, 16, 15),  # edges narrower than the padding
        (500, 0.2, 16, 15),  # too short for edges_sec, long enough for the padding
        (20, 0.2, 16, 0),  # too short for either
        (2000, 0.0, 1, 0),  # nothing extended, nothing padded
    ],
)
def test_window_trim(n_samples: int, edges_sec: float, ex_factor: int, expected: int) -> None:
    assert algorithm.window_trim(n_samples, 2000.0, edges_sec, ex_factor) == expected


def test_window_without_edges_drops_the_zero_padded_columns() -> None:
    """With ``edges_sec`` 0, decompose and postprocess both cut the ``ex_factor - 1`` padded columns."""
    from muedit.decomp.postprocess import _reconstruct_window_signal
    from muedit.signal.filters import demean

    ex, (start, end) = 4, (500, 2500)
    data = np.random.default_rng(4).standard_normal((64, 3000))
    params = DecompositionParameters(
        nbextchan=64 * ex, edges_sec=0.0, niter=1, compute_dtype="float64"
    )
    prep = PreprocessStepOutput(
        signal=SignalImport(data=np.zeros((0, 0)), fsamp=2000.0, gridname=["GR08MM1305"]),
        data=data,
        fsamp=2000.0,
        grid_names=["GR08MM1305"],
        coordinates=[np.zeros((64, 2))],
        ied=[8.0],
        discard_channels=[np.zeros(64, dtype=int)],
        muscles=[],
        loader_meta={},
        roi_list=[(start, end)],
        ngrid=1,
        coordinates_plateau=[start, end],
    )
    decomposed = decompose_step(prep, params, np.random.default_rng(0), None)
    assert decomposed.coordinates_plateau == [start + ex - 1, end - (ex - 1)]

    window = _reconstruct_window_signal(prep, params, 0, np.eye(64 * ex))
    complete = extend_signal(demean(data[:, start:end]), ex)[:, ex - 1 : end - start]
    np.testing.assert_array_equal(window, complete)


def _adaptive_setup(
    raw: np.ndarray, **config_kw: object
) -> tuple[np.ndarray, dict[str, Any], Config]:
    """Samples-first float32 EMG, calibrated whitening and filters, and a small-batch config."""
    emg = (raw - raw.mean(axis=1, keepdims=True)).T.astype(np.float32)
    calib = emg[:1200]
    ext = extend_signal(calib.T.astype(np.float64), EX)
    vecs, vals = pca_extended_signal(ext)
    _, whitening = whiten_extended_signal(ext, vecs, vals)
    filters = np.linalg.qr(np.random.default_rng(2).standard_normal((ext.shape[0], 3)))[0]
    config = Config(fsamp=2000, ex_factor=EX, batch_ms=50, **config_kw)  # type: ignore[arg-type]
    state = {
        "whitening": whitening,
        "sep_vectors": filters.T,
        "base_centr": None,
        "spikes_centr": None,
        "emg_calib": calib,
    }
    return emg, state, config


def test_backward_pass_reads_complete_lookback(raw: np.ndarray) -> None:
    """Frozen, a backward pass is a plain projection of complete samples, in any batch order."""
    emg, state, config = _adaptive_setup(raw, adapt_wh=False, adapt_sv=False, adapt_sd=False)
    model = AdaptiveDecomp(emg=emg, config=config, **state)
    starts: list[int] = []
    ipts = np.full((len(emg), model.n_motor_units), np.nan, dtype=np.float32)

    def sink(start: int, batch: np.ndarray, _spikes: np.ndarray) -> None:
        starts.append(start)
        ipts[start : start + len(batch)] = batch

    stop = 2345  # not a multiple of the batch size: the partial batch is processed last
    model.run(0, stop, reverse=True, sink=sink)

    assert starts == sorted(starts, reverse=True)
    assert starts[-1] == EX - 1, "the recording start is never processed"
    assert np.isnan(ipts[: EX - 1]).all() and np.isnan(ipts[stop:]).all()
    ext = extend_signal(emg, EX, samples_first=True)
    want = (model.sep_vectors @ (model.whitening @ ext[EX - 1 : stop].T)).T
    np.testing.assert_allclose(ipts[EX - 1 : stop], want, rtol=1e-4, atol=1e-5 * np.abs(want).max())


# ── Filters and previews by channel block ───────────────────────────────────


@pytest.mark.parametrize("dtype", [np.float64, np.float32])
def test_block_bandpass_is_bit_identical(dtype: type[np.floating]) -> None:
    x = np.random.default_rng(0).standard_normal((21, 20_000)).astype(dtype)
    for fsamp, emg_type in ((2048.0, 1), (10240.0, 2)):
        y = x.copy()
        bandpass_inplace(y, fsamp, emg_type)
        np.testing.assert_array_equal(y, bandpass_signals(x, fsamp, emg_type).astype(dtype))


@pytest.mark.parametrize("dtype", [np.float64, np.float32])
def test_inplace_notch_is_bit_identical(dtype: type[np.floating]) -> None:
    t = np.arange(20_001) / 2048.0
    x = np.random.default_rng(1).standard_normal((5, t.size)) + 4 * np.sin(2 * np.pi * 50 * t)
    x = x.astype(dtype)
    y = x.copy()
    notch_inplace(y, 2048.0)
    np.testing.assert_array_equal(y, notch_signals(x, 2048.0))


@pytest.mark.parametrize("dtype", [np.float64, np.float32])
def test_row_by_row_abs_means_are_bit_identical(dtype: type[np.floating]) -> None:
    rng = np.random.default_rng(2)
    x = rng.standard_normal((37, 50_003)).astype(dtype)
    keep = rng.random(37) > 0.3
    per_sample, per_row = abs_means(x)
    np.testing.assert_array_equal(per_sample, np.mean(np.abs(x), axis=0))
    np.testing.assert_array_equal(per_row, np.mean(np.abs(x), axis=1))
    np.testing.assert_array_equal(abs_means(x, keep)[0], np.mean(np.abs(x[keep]), axis=0))


# ── Filtered EMG and pulse trains in the session store ──────────────────

_STORE_ROI = (5_000, 35_000)
_STORE_PARAMS = DecompositionParameters(niter=8)


def _store_files(store: SessionStore) -> list[str]:
    return sorted(p.name.split("-")[0].removesuffix(".npy") for p in store.path.glob("*.npy"))


@pytest.fixture(scope="module")
def synthetic_signal() -> SignalImport:
    """One float32 64-channel recording, as loaders now return it."""
    emg = motor_unit_emg(seed=5, n_samples=40_000, fsamp=FSAMP, activity=_STORE_ROI)
    return SignalImport(
        data=emg.astype(np.float32), fsamp=FSAMP, gridname=["GR08MM1305"], muscle=["ta"]
    )


@pytest.fixture(scope="module")
def store_prep(synthetic_signal: SignalImport) -> dict[str, object]:
    """Preprocessing on the heap and into a session store, and one decomposition."""
    loaded = load_step("synthetic.mat", None, synthetic_signal, None)

    def prep(store: SessionStore | None) -> PreprocessStepOutput:
        return preprocess_step(
            loaded=loaded,
            duration=None,
            manual_roi=False,
            roi=_STORE_ROI,
            rois=None,
            params=_STORE_PARAMS,
            discard_overrides=None,
            bids_root=None,
            bids_entities=None,
            bids_metadata=None,
            store=store,
        )

    store = SessionStore.create("parity")
    heap = prep(None)
    decomposed = decompose_step(
        prep=heap, params=_STORE_PARAMS, rng=np.random.default_rng(0), progress_cb=None
    )
    assert sum(f.shape[1] for f in decomposed.mu_filters.values() if f.size), "no filters"
    return {"heap": heap, "stored": prep(store), "store": store, "decomposed": decomposed}


def test_filtered_emg_in_the_store_matches_the_heap(store_prep: dict) -> None:
    heap, stored = store_prep["heap"], store_prep["stored"]
    np.testing.assert_array_equal(stored.data, heap.data)
    assert stored.data.dtype == _STORE_PARAMS.work_dtype
    assert resident_nbytes(stored.data) == 0
    assert not stored.data.flags.writeable


@pytest.mark.parametrize("mode", list(POSTPROCESS_MODES))
def test_pulse_trains_in_the_store_match_the_heap(store_prep: dict, mode: PostprocessMode) -> None:
    flags = POSTPROCESS_MODES[mode]
    params = replace(
        _STORE_PARAMS, use_adaptive=flags["use_adaptive"], full_trace=flags["full_trace"]
    )
    store = store_prep["store"]
    heap = postprocess_step(
        prep=store_prep["heap"],
        decomposed=store_prep["decomposed"],
        params=params,
        progress_cb=None,
    )
    stored = postprocess_step(
        prep=store_prep["stored"],
        decomposed=store_prep["decomposed"],
        params=params,
        progress_cb=None,
        store=store,
    )
    assert len(heap.distime) > 0
    np.testing.assert_array_equal(stored.pulse_t, heap.pulse_t)
    assert stored.pulse_t.dtype == np.float32
    assert len(stored.distime) == len(heap.distime)
    for got, want in zip(stored.distime, heap.distime, strict=True):
        np.testing.assert_array_equal(got, want)
    assert stored.mu_grid_index == heap.mu_grid_index
    assert resident_nbytes(stored.pulse_t) == 0
    assert "pulse_all" not in _store_files(store)


def test_run_in_a_store_matches_the_heap_run(
    synthetic_signal: SignalImport, tmp_path: Path
) -> None:
    def run(store: SessionStore | None) -> dict:
        result, _ = run_decomposition(
            str(tmp_path / "synthetic.mat"),
            params=_STORE_PARAMS,
            save_npz=False,
            roi=_STORE_ROI,
            include_full_preview=True,
            preloaded_signal=synthetic_signal,
            store=store,
        )
        return result

    heap = run(None)
    store = SessionStore.create("run")
    stored = run(store)
    np.testing.assert_array_equal(stored["signal"]["PulseT"], heap["signal"]["PulseT"])
    for got, want in zip(
        stored["signal"]["Dischargetimes"], heap["signal"]["Dischargetimes"], strict=True
    ):
        np.testing.assert_array_equal(got, want)
    assert resident_nbytes(stored["preview"]["pulse_trains_full"]) == 0
    # Only the kept pulse trains outlive the run: the filtered EMG and the
    # pre-dedup matrix were deleted.
    assert _store_files(store) == ["pulse_trains"]


def _calibration_case(raw: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Whitening, filters and window mean of a calibration window over samples 200-2600."""
    window = raw[:, 200:2600]
    ext = extend_signal(window - window.mean(axis=1, keepdims=True), EX)
    vecs, vals = pca_extended_signal(ext)
    _, whitening = whiten_extended_signal(ext, vecs, vals)
    filters = np.linalg.qr(np.random.default_rng(4).standard_normal((ext.shape[0], 4)))[0]
    return whitening, filters, window.mean(axis=1)


def test_backward_pass_starts_from_the_calibration_centroids(raw: np.ndarray) -> None:
    """The forward pass adapts its centroids; the backward pass starts from the fitted ones."""
    whitening, filters, win_mean = _calibration_case(raw)

    def source() -> StreamedExtender:
        return StreamedExtender(raw, EX, offset=win_mean, dtype=np.float32, samples_first=True)

    config = Config(fsamp=2000, ex_factor=EX, batch_ms=50)
    common: dict[str, Any] = {
        "whitening": whitening,
        "sep_vectors": filters.T,
        "base_centr": None,
        "spikes_centr": None,
    }
    fwd = AdaptiveDecomp(emg=source(), emg_calib=(200, 2600), config=config, **common)
    fitted = (fwd.base_centr.copy(), fwd.spikes_centr.copy())
    fwd.run(200, raw.shape[1])
    assert not (
        np.array_equal(fwd.base_centr, fitted[0]) and np.array_equal(fwd.spikes_centr, fitted[1])
    )
    bwd = AdaptiveDecomp(emg=source(), emg_calib=fwd.calibration, config=config, **common)
    np.testing.assert_array_equal(bwd.base_centr, fitted[0])
    np.testing.assert_array_equal(bwd.spikes_centr, fitted[1])
