"""Old-vs-new parity for the memory rewrites of the decomposition (plan stages 4-7)."""

from __future__ import annotations

import numpy as np
import pytest

from muedit.adapt_decomp.adaptation import AdaptiveDecomp, run_adaptive_decomposition
from muedit.adapt_decomp.config import Config
from muedit.decomp import algorithm
from muedit.decomp.algorithm import (
    column_energy,
    covariance,
    pca_extended_signal,
    subtract_mu_waveforms,
    whiten_extended_signal,
)
from muedit.decomp.postprocess import remove_duplicates_by_grid
from muedit.decomp.preview import abs_means
from muedit.decomp.types import DecompositionParameters
from muedit.models import FloatArray
from muedit.signal.decomp_primitives import extend_signal
from muedit.signal.filters import (
    bandpass_inplace,
    bandpass_signals,
    notch_inplace,
    notch_signals,
)
from muedit.signal.streaming import RowSelection, StreamedExtender, extend_mask
from tests._synthetic_emg import motor_unit_emg

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


# ── Stage 4: FastICA temporaries ──────────────────────────────────────────────


def _legacy_whiten(signal: np.ndarray, vecs: np.ndarray, vals: np.ndarray) -> np.ndarray:
    whitening = vecs @ np.linalg.inv(np.sqrt(vals)) @ vecs.T
    return whitening @ signal


def _legacy_subtract(x: np.ndarray, spikes: np.ndarray, fsamp: float, win: float) -> np.ndarray:
    window_l = int(np.round(win * fsamp))
    valid = spikes[(spikes >= window_l) & (spikes < x.shape[1] - window_l)]
    if valid.size == 0:
        return x
    idx = valid[:, None] + np.arange(-window_l, window_l + 1)[None, :]
    waveforms = x[:, idx].mean(axis=1)
    emg_temp = np.zeros_like(x)
    for s in valid:
        emg_temp[:, s - window_l : s + window_l + 1] += waveforms
    return x - emg_temp


@pytest.mark.usefixtures("small_chunks")
class TestFastIcaTemporaries:
    """Chunked covariance, in-place whitening/peel-off and cached energy match the old code."""

    def test_covariance_matches_np_cov(self, extended: np.ndarray) -> None:
        np.testing.assert_allclose(covariance(extended), np.cov(extended, bias=True), rtol=RTOL)

    def test_covariance_on_columns_matches_fancy_index(self, extended: np.ndarray) -> None:
        cols = np.flatnonzero(np.arange(extended.shape[1]) % 7 != 3)
        np.testing.assert_allclose(
            covariance(extended, cols), np.cov(extended[:, cols], bias=True), rtol=RTOL
        )

    def test_inplace_whitening_matches_matmul(self, extended: np.ndarray) -> None:
        vecs, vals = pca_extended_signal(extended)
        expected = _legacy_whiten(extended, vecs, vals)
        work = extended.copy()
        out, _ = whiten_extended_signal(work, vecs, vals, inplace=True)
        assert out is work
        np.testing.assert_allclose(out, expected, rtol=RTOL, atol=RTOL * np.abs(expected).max())

    def test_inplace_whitening_on_trimmed_view(self, extended: np.ndarray) -> None:
        vecs, vals = pca_extended_signal(extended[:, 50:-50])
        expected = _legacy_whiten(extended[:, 50:-50], vecs, vals)
        work = extended.copy()[:, 50:-50]
        whiten_extended_signal(work, vecs, vals, inplace=True)
        np.testing.assert_allclose(work, expected, rtol=RTOL, atol=RTOL * np.abs(expected).max())

    def test_column_energy_is_exact(self, extended: np.ndarray) -> None:
        np.testing.assert_array_equal(column_energy(extended), np.sum(extended**2, axis=0))

    def test_inplace_peel_off_matches_accumulate_then_subtract(self, extended: np.ndarray) -> None:
        # Spikes closer than the waveform window, so the overlap-sum path is exercised.
        spikes = np.array([3, 40, 70, 95, 400, 430, 3000, extended.shape[1] - 2])
        expected = _legacy_subtract(extended, spikes, 2000.0, 0.025)
        work = extended.copy()
        subtract_mu_waveforms(work, spikes, 2000.0, 0.025)
        np.testing.assert_allclose(work, expected, rtol=RTOL, atol=RTOL * np.abs(expected).max())

    def test_peel_off_without_valid_spikes_leaves_x(self, extended: np.ndarray) -> None:
        work = extended.copy()
        subtract_mu_waveforms(work, np.array([0, extended.shape[1] - 1]), 2000.0, 0.025)
        np.testing.assert_array_equal(work, extended)


# ── Stage 5: streamed passes ──────────────────────────────────────────────────


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

    def test_complete_flags_only_the_recording_start(self, raw: np.ndarray) -> None:
        source = StreamedExtender(raw, EX)
        assert source.complete(0, 10).tolist() == [False] * (EX - 1) + [True] * (11 - EX)
        assert source.complete(500, 510).all()

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


def _legacy_full_trace(
    filters_by_window: dict[int, np.ndarray],
    whiten_by_window: dict[int, np.ndarray],
    means_by_window: dict[int, np.ndarray],
    grid: np.ndarray,
    ex_factor: int,
    fsamp: float,
    artifact_mask: np.ndarray,
) -> tuple[np.ndarray, list[np.ndarray]]:
    """The pre-stage-5 full-trace branch: whole-grid extension, one filter at a time."""
    from muedit.signal.decomp_primitives import (
        POSTPROC_MIN_ISI_SEC,
        find_refractory_peaks,
        signed_square,
        split_by_amplitude,
    )

    ltime = grid.shape[1]
    raw_ext = extend_signal(grid, ex_factor)
    pulses, distime = [], []
    for nwin in sorted(filters_by_window):
        for j in range(filters_by_window[nwin].shape[1]):
            w_dewhite = filters_by_window[nwin][:, j] @ whiten_by_window[nwin]
            pt_full = w_dewhite @ raw_ext
            win_mean = means_by_window[nwin]
            n_ch = win_mean.size
            s = np.array(
                [w_dewhite[k * n_ch : (k + 1) * n_ch] @ win_mean for k in range(ex_factor)]
            )
            corr = np.zeros(raw_ext.shape[1])
            for k in range(ex_factor):
                corr[k : ltime + k] += s[k]
            pt = signed_square((pt_full - corr)[:ltime])
            pt[artifact_mask] = 0.0
            spikes = find_refractory_peaks(pt, fsamp, min_isi_sec=POSTPROC_MIN_ISI_SEC)
            spikes, _, _ = split_by_amplitude(pt, spikes, kmeans_iter=10)
            distime.append(spikes[~artifact_mask[spikes]])
            pulses.append(pt)
    return np.array(pulses), distime


def test_streamed_full_trace_matches_whole_grid_extension(
    raw: np.ndarray, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(algorithm, "_FULL_TRACE_BATCH_BYTES", 8 * 10 * EX * 256)
    rng = np.random.default_rng(1)
    n_ext = raw.shape[0] * EX
    filters: dict[int, FloatArray] = {
        0: rng.standard_normal((n_ext, 3)),
        1: rng.standard_normal((n_ext, 2)),
    }
    whiten: dict[int, FloatArray] = {
        k: rng.standard_normal((n_ext, n_ext)) / n_ext for k in filters
    }
    means: dict[int, FloatArray] = {k: rng.standard_normal((raw.shape[0],)) for k in filters}
    mask = np.zeros(raw.shape[1], dtype=bool)
    mask[700:760] = True

    expected_pulses, expected_spikes = _legacy_full_trace(
        filters, whiten, means, raw, EX, 2000.0, extend_mask(mask, EX)
    )
    pulses, spikes = algorithm.batch_process_filters(
        filters,
        {},
        [],
        raw.shape[1],
        2000.0,
        whiten_mat_by_window=whiten,
        grid_data={0: raw},
        win_means_by_window=means,
        artifact_mask=mask,
        pulse_dtype=np.float64,
    )
    scale = np.abs(expected_pulses).max()
    np.testing.assert_allclose(pulses, expected_pulses, rtol=RTOL, atol=RTOL * scale)
    for got, want in zip(spikes, expected_spikes, strict=True):
        np.testing.assert_array_equal(got, want)


def _adaptive_setup(
    raw: np.ndarray, **config_kw: object
) -> tuple[np.ndarray, dict[str, np.ndarray], Config]:
    """Samples-first float32 EMG, calibrated whitening/filters/centroids, and a small-batch config."""
    from muedit.decomp.adaptive_batch import _compute_calibration_stats

    emg = (raw - raw.mean(axis=1, keepdims=True)).T.astype(np.float32)
    calib = emg[:1200]
    ext = extend_signal(calib.T.astype(np.float64), EX)
    vecs, vals = pca_extended_signal(ext)
    _, whitening = whiten_extended_signal(ext, vecs, vals)
    filters = np.linalg.qr(np.random.default_rng(2).standard_normal((ext.shape[0], 3)))[0]
    base, spikes = _compute_calibration_stats(
        raw, raw.mean(axis=1), whitening, filters, 0, len(calib), 2000.0
    )
    config = Config(fsamp=2000, ex_factor=EX, batch_ms=50, **config_kw)  # type: ignore[arg-type]
    state = {
        "whitening": whitening,
        "sep_vectors": filters.T,
        "base_centr": base,
        "spikes_centr": spikes,
        "emg_calib": calib,
    }
    return emg, state, config


def _legacy_forward_run(
    model: AdaptiveDecomp, emg: np.ndarray, mask: np.ndarray | None
) -> tuple[np.ndarray, np.ndarray, dict[str, np.ndarray]]:
    """The pre-stage-5 ``run()``: one whole-pass extension, pass-relative edge context."""
    ext = extend_signal(emg, model.config.ex_factor, samples_first=True)
    n, bs = ext.shape[0], model.config.batch_size
    n_batches = n // bs
    ipts_out = np.zeros((n, model.n_motor_units), dtype=np.float32)
    spikes_out = np.zeros((n, model.n_motor_units), dtype=np.int32)
    wh, total = np.full(n_batches, np.nan), np.full(n_batches, np.nan)
    sv = np.full((n_batches, model.n_motor_units), np.nan)

    def edges(s: int, e: int) -> tuple[np.ndarray | None, np.ndarray | None]:
        lo = model._project(ext[s - 1 : s]) if s > 0 else None
        hi = model._project(ext[e : e + 1]) if e < n else None
        return lo, hi

    for b in range(n_batches):
        s = b * bs + (model.config.ex_factor - 1 if b == 0 else 0)
        e = (b + 1) * bs
        bmask = mask[s:e] if mask is not None else None
        ctx = edges(s, e)
        if bmask is not None and bmask.any():
            ipts = model._separate(model.whitening @ ext[s:e].T)
            spk = model._detect_spikes_with_context(ipts, ctx, update_centroids=False)
            spk[bmask] = 0
        else:
            whitened = model._whiten(ext[s:e])
            ipts = model._separate(whitened)
            spk = model._detect_spikes_with_context(ipts, ctx, update_centroids=True)
            whl = model._wh_loss(model._kl_divergence())
            svl = model._sv_loss(model._contrast_value(ipts, spk))
            wh[b], sv[b], total[b] = whl, svl, (0.0 if np.isnan(whl) else whl) + np.nansum(svl)
            model._update_separation_vectors(whitened, ipts, spk)
        ipts_out[s:e], spikes_out[s:e] = ipts, spk
    s = n_batches * bs
    if s < n:
        whitened = model.whitening @ ext[s:].T
        ipts = model._separate(whitened)
        spk = model._detect_spikes_with_context(ipts, edges(s, n), update_centroids=True)
        if mask is not None:
            spk[mask[s:]] = 0
        if not (mask is not None and mask[s:].any()):
            model._update_separation_vectors(whitened, ipts, spk)
        ipts_out[s:], spikes_out[s:] = ipts, spk
    return ipts_out, spikes_out, {"wh_loss": wh, "sv_loss": sv, "total_loss": total}


@pytest.mark.parametrize("masked", [False, True])
def test_streamed_adaptive_forward_matches_whole_pass_extension(
    raw: np.ndarray, masked: bool
) -> None:
    """From the recording start, streaming reads change nothing: same ipts, spikes, losses."""
    emg, state, config = _adaptive_setup(raw, compute_loss=True)
    mask = None
    if masked:
        mask = np.zeros(len(emg), dtype=bool)
        mask[1510:1540] = True
    legacy = AdaptiveDecomp(emg=emg, config=config, artifact_mask=mask, **state)
    legacy_mask = None if mask is None else extend_mask(mask, EX)
    want_ipts, want_spikes, want_losses = _legacy_forward_run(legacy, emg, legacy_mask)

    ipts, spikes, losses = run_adaptive_decomposition(
        emg=emg, config=config, artifact_mask=mask, **state
    )
    np.testing.assert_array_equal(ipts, want_ipts)
    np.testing.assert_array_equal(spikes, want_spikes)
    assert spikes.sum() > 0
    for key in want_losses:
        np.testing.assert_allclose(losses[key], want_losses[key], rtol=1e-6)


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


def _legacy_calibration(whitening: np.ndarray, calib: np.ndarray, config: Config) -> np.ndarray:
    """The pre-streaming whitening calibration: stack the whole whitened window, then np.cov."""
    whitened = (extend_signal(calib, EX, samples_first=True) @ whitening.T)[EX - 1 :]
    cov = np.cov(whitened.T).astype(np.float32)
    bs = config.batch_size
    for lo in range(0, len(whitened) - bs + 1, bs):
        batch_cov = np.cov(whitened[lo : lo + bs].T).astype(np.float32)
        cov = (1 - config.cov_alpha) * cov + config.cov_alpha * batch_cov
    return cov


def test_streamed_calibration_matches_stacked_window(
    raw: np.ndarray, monkeypatch: pytest.MonkeyPatch
) -> None:
    from muedit.adapt_decomp import adaptation

    monkeypatch.setattr(adaptation, "_CALIB_CHUNK", 257)
    emg, state, config = _adaptive_setup(raw, compute_loss=True)
    model = AdaptiveDecomp(emg=emg, config=config, **state)
    want_cov = _legacy_calibration(model.whitening, state["emg_calib"], config)
    np.testing.assert_allclose(model.whitening_covariance, want_cov, rtol=1e-5, atol=1e-6)

    # A sample range of the pass's own source reads the same calibration.
    shared = AdaptiveDecomp(
        emg=emg,
        whitening=state["whitening"],
        sep_vectors=state["sep_vectors"],
        base_centr=state["base_centr"],
        spikes_centr=state["spikes_centr"],
        emg_calib=(0, len(state["emg_calib"])),
        config=config,
    )
    np.testing.assert_array_equal(shared.whitening_covariance, model.whitening_covariance)
    np.testing.assert_array_equal(
        shared.calibration.contrast_calib_mean, model.calibration.contrast_calib_mean
    )


# ── Stage 6: duplicate removal on spike times ─────────────────────────────────


def _legacy_rem_duplicates(
    distime: list[np.ndarray], maxlag: int, jitter: float, tol: float, fsamp: float, l_sig: int
) -> list[int]:
    """The pre-stage-6 duplicate removal: jittered Python sets and a dense lag raster."""
    jitter_samples = int(round(jitter * fsamp))
    n_mus = len(distime)
    jittered: list[np.ndarray] = []
    for i in range(n_mus):
        d_times = np.asarray(distime[i], dtype=int)
        d_times = d_times[d_times < l_sig]
        expanded_set = set(d_times.tolist())
        for j in range(1, jitter_samples + 1):
            expanded_set.update((d_times - j).tolist())
            expanded_set.update((d_times + j).tolist())
        expanded = np.array(list(expanded_set), dtype=int)
        jittered.append(expanded[(expanded >= 0) & (expanded < l_sig)])

    kept: list[int] = []
    active = np.ones(n_mus, dtype=bool)
    lags_vec = np.arange(-2 * maxlag, 2 * maxlag + 1)
    for i in range(n_mus):
        if not active[i] or len(jittered[i]) == 0:
            continue
        ref = jittered[i]
        duplicates = [i]
        ref_raster = np.zeros(l_sig, dtype=bool)
        ref_raster[ref] = True
        for j in range(i + 1, n_mus):
            target = jittered[j]
            if not active[j] or len(target) == 0:
                continue
            norm = np.sqrt(max(len(ref), 1) * max(len(target), 1))
            shifted = target[:, None] + lags_vec[None, :]
            valid = (shifted >= 0) & (shifted < l_sig)
            overlap = (ref_raster[np.clip(shifted, 0, l_sig - 1)] & valid).sum(axis=0)
            max_overlap = int(overlap.max())
            best_lag = int(lags_vec[int(np.argmax(overlap))]) if max_overlap > 0 else 0
            best_corr = max_overlap / norm if max_overlap > 0 else 0.0
            aligned = target + best_lag if best_corr > 0.2 else target
            common = np.intersect1d(ref, aligned)
            n_common = 1 + int(np.count_nonzero(np.diff(common) != 1)) if len(common) else 0
            longest = max(len(distime[i]), len(distime[j]))
            if (n_common / longest if longest > 0 else 0) >= tol:
                duplicates.append(j)
        covs = [algorithm.isi_cov(distime[k], 1.0, fallback=100.0) for k in duplicates]
        kept.append(duplicates[int(np.argmin(covs))])
        active[duplicates] = False
    return kept


def _duplicate_trains(seed: int, n_samples: int, fsamp: float) -> list[np.ndarray]:
    """Units plus lagged, jittered, thinned and merged copies, some spikes off the recording."""
    rng = np.random.default_rng(seed)
    jit = int(round(algorithm.DEDUP_JITTER * fsamp))
    trains: list[np.ndarray] = []
    for _ in range(6):
        isi = rng.normal(fsamp / 12, fsamp / 60, size=int(n_samples / (fsamp / 12)) + 5)
        base = np.cumsum(np.abs(isi)).astype(int) + int(rng.integers(-30, 30))
        trains.append(base)
        trains.append(base + int(rng.integers(-int(fsamp) // 20, int(fsamp) // 20)))
        trains.append(np.sort(base + rng.integers(-jit - 1, jit + 2, size=base.size)))
        trains.append(base[rng.random(base.size) > rng.uniform(0.1, 0.8)])
    trains.append(np.sort(np.concatenate([trains[0], trains[4]])))
    trains.append(np.array([], dtype=int))
    order = rng.permutation(len(trains))
    return [trains[k] for k in order]


@pytest.mark.parametrize(("seed", "fsamp"), [(0, 2048.0), (1, 10240.0), (2, 4000.0)])
@pytest.mark.parametrize("tol", [0.3, 0.6])
def test_rem_duplicates_matches_dense_raster(seed: int, fsamp: float, tol: float) -> None:
    n_samples = int(20 * fsamp)
    trains = _duplicate_trains(seed, n_samples, fsamp)
    maxlag = round(fsamp / algorithm.DEDUP_MAXLAG_RATIO)
    want = _legacy_rem_duplicates(trains, maxlag, algorithm.DEDUP_JITTER, tol, fsamp, n_samples)
    got = algorithm.rem_duplicates(
        trains, trains, maxlag, algorithm.DEDUP_JITTER, tol, fsamp, n_samples
    )
    assert got == want
    assert len(want) < len(trains) - 1


def test_remove_duplicates_by_grid_indexes_the_pulse_matrix_once() -> None:
    fsamp, n_samples = 2048.0, 40_960
    trains = _duplicate_trains(5, n_samples, fsamp)
    grids = [k % 2 for k in range(len(trains))]
    pulse_t = np.random.default_rng(0).standard_normal((len(trains), n_samples))
    params = DecompositionParameters(duplicatesbgrids=True)
    maxlag = round(fsamp / algorithm.DEDUP_MAXLAG_RATIO)

    per_grid: list[int] = []
    per_grid_grids: list[int] = []
    for g in (0, 1):
        idx = [k for k, gk in enumerate(grids) if gk == g]
        local = _legacy_rem_duplicates(
            [trains[k] for k in idx], maxlag, algorithm.DEDUP_JITTER, 0.3, fsamp, n_samples
        )
        per_grid += [idx[k] for k in local]
        per_grid_grids += [g] * len(local)
    across = _legacy_rem_duplicates(
        [trains[k] for k in per_grid], maxlag, algorithm.DEDUP_JITTER, 0.3, fsamp, n_samples
    )
    want = [per_grid[k] for k in across]

    out, distime, gidx, kept = remove_duplicates_by_grid(pulse_t, trains, grids, 2, params, fsamp)
    assert kept == want
    assert gidx == [per_grid_grids[k] for k in across]
    np.testing.assert_array_equal(out, pulse_t[want])
    assert all(d is trains[k] for d, k in zip(distime, want, strict=True))


# ── Stage 7: filters and previews by channel block ────────────────────────────


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
