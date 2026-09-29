"""float32 compute against float64: the same motor units, RoA >= 0.99 on every pair (plan stage 16)."""

from __future__ import annotations

import time
import tracemalloc
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from muedit.adapt_decomp.adaptation import AdaptiveDecomp
from muedit.decomp.algorithm import (
    _stream_full_trace,
    covariance,
    fixed_point_alg,
    get_spikes,
    vec_mat,
)
from muedit.decomp.core import decompose_step
from muedit.decomp.postprocess import postprocess_step
from muedit.decomp.types import POSTPROCESS_MODES, DecompositionParameters
from muedit.models import SignalImport
from muedit.signal.decomp_primitives import extend_signal, zeroed_matmul
from muedit.signal.filters import demean, notch_inplace
from muedit.signal.streaming import StreamedExtender
from tests._metrics_helpers import build_signal, greedy_one_to_one, score_pairs
from tests._report import record
from tests._synthetic_emg import FSAMP, motor_unit_emg
from tests.test_decomp_benchmark import _EDITED_NPZ, _SIM_NITER, _SIM_ROI, _prepare, real_reference

__all__ = ["real_reference"]  # the benchmark's fixture, shared

GATE_ROA = 0.99
# float32 should move a discharge by one sample at most, and never shift a whole train.
JITTER = 1
MAX_LAG = 10

_MODES = ("windowed", "adaptive", "full-trace")


def _run(prep: Any, params: DecompositionParameters) -> tuple[dict[str, list[np.ndarray]], float]:
    """Discharge times of every postprocess mode, and the decompose time."""
    t0 = time.perf_counter()
    decomposed = decompose_step(
        prep=prep, params=params, rng=np.random.default_rng(params.random_seed), progress_cb=None
    )
    seconds = time.perf_counter() - t0
    out = {}
    for mode in _MODES:
        post = postprocess_step(
            prep=prep,
            decomposed=decomposed,
            params=replace(params, **POSTPROCESS_MODES[mode]),
            progress_cb=None,
        )
        out[mode] = [np.asarray(d) for d in post.distime]
    return out, seconds


def _compare(
    dataset: str,
    prepare: Any,
    params: DecompositionParameters,
) -> dict[str, dict[str, Any]]:
    """Decompose with both types (each on its own preprocessed signal) and pair the units."""
    runs = {}
    times = {}
    for dtype in ("float64", "float32"):
        p = replace(params, compute_dtype=dtype)
        runs[dtype], times[dtype] = _run(prepare(p), p)
    results: dict[str, dict[str, Any]] = {}
    for mode in _MODES:
        d64, d32 = runs["float64"][mode], runs["float32"][mode]
        pairs = greedy_one_to_one(
            score_pairs(
                d32, d64, jitter=JITTER, max_lag=MAX_LAG, ref_indices=list(range(len(d64)))
            ),
            0.0,
        )
        roas = [m[4] for m in pairs]
        results[mode] = {
            "n_float64": len(d64),
            "n_float32": len(d32),
            "n_paired": len(pairs),
            "n_below_gate": sum(1 for r in roas if r < GATE_ROA),
            "min_roa": round(min(roas), 4) if roas else 0.0,
            "mean_roa": round(float(np.mean(roas)), 4) if roas else 0.0,
        }
        record(
            "compute_dtype",
            caption=(
                f"float32 vs float64 units per dataset and mode (one-to-one, jitter {JITTER}, "
                f"gate RoA >= {GATE_ROA})"
            ),
            dataset=dataset,
            mode=mode,
            **results[mode],
            decompose_sec_float64=round(times["float64"], 2),
            decompose_sec_float32=round(times["float32"], 2),
        )
    return results


def _assert_gate(results: dict[str, dict[str, Any]]) -> None:
    for mode, r in results.items():
        assert r["n_float32"] == r["n_float64"], f"{mode}: MU count {r}"
        assert r["n_paired"] == r["n_float64"], f"{mode}: unpaired units {r}"
        assert r["n_below_gate"] == 0, f"{mode}: pairs below RoA {GATE_ROA}: {r}"


# ── Gate ─────────────────────────────────────────────────────────────────────


def _synthetic_signal() -> SignalImport:
    n = 60_000
    return SignalImport(
        data=motor_unit_emg(seed=11, n_samples=n, fsamp=FSAMP, activity=(5_000, 55_000)),
        fsamp=FSAMP,
        gridname=["GR08MM1305"],
        muscle=["ta"],
        auxiliary=np.zeros((0, n)),
        auxiliaryname=[],
    )


def test_synthetic_gate() -> None:
    signal = _synthetic_signal()
    params = DecompositionParameters(niter=25, peel_off_enabled=True, nwindows=2)
    _assert_gate(
        _compare(
            "synthetic",
            lambda p: _prepare("synthetic", signal, (10_000, 50_000), p, [(30_000, 30_400)]),
            params,
        )
    )


def test_real_gate(
    novecento_otb4_file: Path, novecento_emg: SignalImport, real_reference: dict[str, Any]
) -> None:
    p = real_reference["params"]
    params = DecompositionParameters(
        niter=p["niter"],
        peel_off_enabled=p["peel_off_enabled"],
        sil_thr=p["sil_thr"],
        duplicatesthresh=p["duplicatesthresh"],
        duplicatesbgrids=True,
        random_seed=p["random_seed"],
    )
    _assert_gate(
        _compare(
            "real_novecento",
            lambda q: _prepare(
                str(novecento_otb4_file),
                novecento_emg,
                real_reference["roi"],
                q,
                real_reference["artifact_regions"],
            ),
            params,
        )
    )


@pytest.mark.parametrize("pct", [20, 40, 60])
def test_simulation_gate(simulation_loaded: dict[int, dict[str, Any]], pct: int) -> None:
    signal = build_signal(simulation_loaded[pct])
    params = DecompositionParameters(niter=_SIM_NITER, peel_off_enabled=True)
    _assert_gate(
        _compare(f"simulated_{pct}", lambda p: _prepare("simulated", signal, _SIM_ROI, p), params)
    )


# ── float32 plumbing ─────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def window32() -> np.ndarray:
    """A float32 extended window (64 rows x ~20k columns)."""
    emg = motor_unit_emg(seed=5, n_samples=20_000, activity=(0, 20_000))[:8]
    return extend_signal(demean(emg.astype(np.float32)), 8, dtype=np.float32)


def test_extend_signal_keeps_the_requested_dtype() -> None:
    x = np.ones((3, 10), dtype=np.float32)
    assert extend_signal(x, 4, dtype=np.float32).dtype == np.float32
    assert extend_signal(x, 4).dtype == np.float64


@pytest.mark.parametrize("samples_first", [False, True])
def test_streamed_batches_keep_the_extender_dtype(samples_first: bool) -> None:
    source = np.ones((3, 50), dtype=np.float64)
    extender = StreamedExtender(source, 4, dtype=np.float32, samples_first=samples_first)
    assert extender.read(10, 30).dtype == np.float32


def test_demean_takes_the_mean_in_float64() -> None:
    x = (np.random.default_rng(0).standard_normal((4, 100_001)) + 1e4).astype(np.float32)
    out = demean(x)
    assert out.dtype == np.float32
    expected = (x - x.astype(np.float64).mean(axis=1, keepdims=True)).astype(np.float32)
    np.testing.assert_array_equal(out, expected)


def test_float32_notch_filters_in_float64() -> None:
    t = np.arange(20_001) / 2048.0
    x = np.random.default_rng(1).standard_normal((3, t.size)) + 4 * np.sin(2 * np.pi * 50 * t)
    x32, x64 = x.astype(np.float32), x.astype(np.float32).astype(np.float64)
    notch_inplace(x32, 2048.0)
    notch_inplace(x64, 2048.0)
    np.testing.assert_array_equal(x32, x64.astype(np.float32))


def test_float32_covariance_accumulates_in_float64(window32: np.ndarray) -> None:
    np.testing.assert_allclose(
        covariance(window32), covariance(window32.astype(np.float64)), rtol=1e-10, atol=1e-10
    )


def test_fixed_point_never_promotes_the_window(window32: np.ndarray) -> None:
    """A float64 ``w`` against a float32 window must not copy the window as float64."""
    w = np.random.default_rng(2).standard_normal(window32.shape[0])
    w /= np.linalg.norm(w)
    basis = np.zeros((window32.shape[0], 0))
    tracemalloc.start()
    out = fixed_point_alg(w, window32, basis, 50, "skew")
    get_spikes(out, window32, FSAMP)
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    assert out.dtype == np.float64
    assert peak < window32.nbytes, (
        f"peak {peak / 1e6:.1f} MB for a {window32.nbytes / 1e6:.1f} MB window"
    )


def test_vec_mat_ignores_stale_output_memory() -> None:
    """A NaN left in freed memory never reaches ``v @ m``.

    Accelerate's float32 ``cblas_sgemv`` (transposed) computes ``0 * old`` for some outputs
    when beta = 0 (here the last 39 of 20519 columns); a fresh buffer that reuses freed
    NaN memory then yields NaN. ``vec_mat`` writes into a zeroed buffer instead.
    """
    rng = np.random.default_rng(0)
    m = rng.standard_normal((1024, 20519)).astype(np.float32)
    v = rng.standard_normal(1024)
    expected = v @ m.astype(np.float64)
    for _ in range(20):
        stale = np.full(m.shape[1], np.nan, dtype=np.float32)
        del stale  # the next buffer of this size reuses its memory
        out = vec_mat(v, m)
        assert np.isfinite(out).all()
        np.testing.assert_allclose(out, expected, rtol=1e-4, atol=1e-3)


def test_one_row_products_ignore_stale_output_memory() -> None:
    """A grid or window with one MU projects through ``(1, k) @ (k, n)``, the same sgemv path."""
    rng = np.random.default_rng(0)
    m = rng.standard_normal((1024, 12345)).astype(np.float32)
    w = rng.standard_normal((1, 1024)).astype(np.float32)
    expected = w.astype(np.float64) @ m.astype(np.float64)
    for _ in range(20):
        stale = np.full((1, m.shape[1]), np.nan, dtype=np.float32)
        del stale
        out = zeroed_matmul(w, m)
        assert out.dtype == np.float32 and np.isfinite(out).all()
        np.testing.assert_allclose(out, expected, rtol=1e-4, atol=1e-3)


def test_single_mu_full_trace_ignores_stale_output_memory() -> None:
    """The streamed full-trace pass of a grid with one MU over one batch of 12345 samples."""
    rng = np.random.default_rng(1)
    n_ch, ex, n = 64, 16, 12345
    source = StreamedExtender(
        rng.standard_normal((n_ch, n)).astype(np.float32), ex, dtype=np.float32
    )
    w = rng.standard_normal((n_ch * ex, 1))
    for _ in range(10):
        out = np.zeros((1, n), dtype=np.float32)  # before the stale buffer, or it takes its memory
        stale = np.full((1, n), np.nan, dtype=np.float32)
        del stale
        _stream_full_trace(source, w, None, out, np.array([0]))
        assert np.isfinite(out).all()


def test_single_mu_adaptive_separation_ignores_stale_output_memory() -> None:
    """``AdaptiveDecomp._separate`` with one MU over a batch of 520 samples (5.2 kHz, 100 ms)."""
    rng = np.random.default_rng(2)
    n_ext, n = 1024, 520
    model = AdaptiveDecomp.__new__(AdaptiveDecomp)
    model.sep_vectors = rng.standard_normal((1, n_ext)).astype(np.float32)
    whitened = rng.standard_normal((n_ext, n)).astype(np.float32)
    for _ in range(20):
        stale = np.full((1, n), np.nan, dtype=np.float32)
        del stale
        ipts = model._separate(whitened)
        assert ipts.shape == (n, 1) and np.isfinite(ipts).all()


# ── update_filter gate ───────────────────────────────────────────────────────


def _update_filter_gate(
    dataset: str,
    emg: np.ndarray,
    mask: np.ndarray,
    trains: list[np.ndarray],
    fsamp: float,
    view_sec: float,
) -> None:
    """Refit several MUs over a view in both types; the spikes in view must agree (RoA >= GATE_ROA)."""
    from muedit.editing.operations import update_motor_unit_filter_window
    from tests._metrics_helpers import compute_metrics

    view, edge = int(view_sec * fsamp), int(round(0.1 * fsamp))
    mus = [i for i, t in enumerate(trains) if t.size > 30][:6]
    for k, mu in enumerate(mus):
        start = max(0, min(int(np.median(trains[mu])) - view // 2, emg.shape[1] - view))
        others = [trains[i].tolist() for i in mus if i != mu]
        spikes = {}
        for dtype in ("float64", "float32"):
            _, updated = update_motor_unit_filter_window(
                emg,
                mask,
                trains[mu].tolist(),
                fsamp,
                start,
                start + view,
                peeloff_spike_times=others,
                use_peeloff=bool(k % 2),
                compute_dtype=dtype,
            )
            spikes[dtype] = np.array(
                [s for s in updated if start + edge <= s < start + view - edge], dtype=int
            )
        roa = compute_metrics(spikes["float32"], spikes["float64"], JITTER, MAX_LAG)[3]
        record(
            "compute_dtype_update_filter",
            caption=f"update_filter spikes in view, float32 vs float64 (gate RoA >= {GATE_ROA})",
            dataset=dataset,
            mu=mu,
            use_peeloff=bool(k % 2),
            n_float64=int(spikes["float64"].size),
            n_float32=int(spikes["float32"].size),
            roa=round(roa, 4),
        )
        assert roa >= GATE_ROA, f"{dataset} MU {mu}: RoA {roa:.4f}"


def test_update_filter_gate_real(
    novecento_emg: SignalImport, real_reference: dict[str, Any]
) -> None:
    from muedit.signal.grid import format_hdemg_signal

    edited = np.load(str(_EDITED_NPZ), allow_pickle=True)
    grids = np.asarray(edited["mu_grid_index"], dtype=int)
    trains = [np.asarray(d, dtype=int) for d in edited["discharge_times"]]
    _, _, discard, _ = format_hdemg_signal(novecento_emg.gridname)
    grid = 0
    _update_filter_gate(
        "real_novecento",
        np.asarray(novecento_emg.data[64 * grid : 64 * (grid + 1)]),
        discard[grid],
        [t for t, g in zip(trains, grids, strict=True) if g == grid],
        novecento_emg.fsamp,
        10.0,
    )


def test_update_filter_gate_simulation(simulation_loaded: dict[int, dict[str, Any]]) -> None:
    from muedit.signal.grid import format_hdemg_signal

    sim = simulation_loaded[20]
    signal = build_signal(sim)
    _, _, discard, _ = format_hdemg_signal(signal.gridname)
    _update_filter_gate(
        "simulated_20",
        np.asarray(signal.data),
        discard[0],
        [np.asarray(s, dtype=int) for s in sim["gt_spike_times"]],
        signal.fsamp,
        5.0,
    )
