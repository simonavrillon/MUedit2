"""Adaptive post-processing helpers for online-style decomposition batches."""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np

from muedit.decomp.adaptation import AdaptiveDecomp, BatchSink, Calibration
from muedit.decomp.types import DecompositionParameters
from muedit.models import BoolArray, FloatArray, IntArray
from muedit.signal.decomp_primitives import enforce_refractory, extend_mask, signed_square
from muedit.signal.streaming import SampleSource, StreamedExtender
from muedit.storage import ArrayStore, RamStore


def _run_bidirectional(
    source: StreamedExtender,
    whiten_mat: FloatArray,
    mu_filters: FloatArray,
    calib_start: int,
    calib_end: int,
    params: DecompositionParameters,
    fsamp: float,
    sink: BatchSink,
) -> None:
    """Run adaptive decomposition forward from calib_start and, if needed, backward over the pre-calibration segment."""

    def _clipped(lo: int, hi: int) -> BatchSink:
        def _emit(start: int, pulse_trains_batch: np.ndarray, spikes: np.ndarray) -> None:
            a, b = max(start, lo), min(start + len(pulse_trains_batch), hi)
            if a < b:
                sink(a, pulse_trains_batch[a - start : b - start], spikes[a - start : b - start])

        return _emit

    def _pass(calibration: tuple[int, int] | Calibration) -> AdaptiveDecomp:
        return AdaptiveDecomp(
            source=source,
            whiten_mat=whiten_mat,
            mu_filters=mu_filters,
            calibration=calibration,
            params=params,
            fsamp=fsamp,
        )

    # The forward pass warms up on the batch before the calibration window;
    # that batch adapts the state but its output is dropped.
    fwd = _pass((calib_start, calib_end))
    fwd_start = max(0, calib_start - fwd.batch_size)
    fwd.run(fwd_start, source.n_samples, sink=_clipped(calib_start, source.n_samples))
    if calib_start > 0:
        _pass(fwd.calibration).run(0, calib_start, reverse=True, sink=_clipped(0, calib_start))


def adaptive_batch_process(
    mu_filters_by_window: dict[int, FloatArray],
    whiten_mat_by_window: dict[int, FloatArray],
    grid_data: Mapping[int, SampleSource],  # kept channels per grid, read batch by batch
    coordinates_plateau: list[int],
    total_samples: int,
    fsamp: float,
    nwindows: int,
    win_mean_by_window: dict[int, FloatArray],
    params: DecompositionParameters,
    artifact_mask: BoolArray | None = None,
    store: ArrayStore | None = None,
) -> tuple[FloatArray, list[IntArray]]:
    """Apply adaptive post-processing across all decomposition windows and grids.

    The adaptation runs in ``params.work_dtype``; the pulse trains are written into
    ``store`` (heap by default) batch by batch.
    """
    total_mus = sum(f.shape[1] for f in mu_filters_by_window.values() if f.size > 0)
    if total_mus == 0:
        return np.array([]), []

    store = store if store is not None else RamStore()
    pulse_trains = store.allocate(
        "pulse_trains_all", (total_mus, total_samples), np.float32, zero=True
    )
    found_times: list[IntArray] = []
    spike_units: list[IntArray] = []
    mu_nb = 0

    for win_global in sorted(mu_filters_by_window.keys()):
        filters = mu_filters_by_window[win_global]
        if filters.size == 0:
            continue

        grid_idx = win_global // max(1, nwindows)
        calib_start, calib_end = (
            coordinates_plateau[win_global * 2],
            coordinates_plateau[win_global * 2 + 1],
        )
        grid = grid_data[grid_idx]
        win_mean = win_mean_by_window[win_global]
        source = StreamedExtender(
            grid,
            whiten_mat_by_window[win_global].shape[0] // grid.shape[0],
            offset=win_mean,
            dtype=params.work_dtype,
            samples_first=True,
            artifact_mask=artifact_mask,
        )
        rows = slice(mu_nb, mu_nb + filters.shape[1])

        def _collect(
            start: int, pulse_trains_batch: np.ndarray, spikes: np.ndarray, rows: slice = rows
        ) -> None:
            stop = min(start + len(pulse_trains_batch), total_samples)
            pulse_trains[rows, start:stop] = signed_square(
                pulse_trains_batch[: stop - start].astype(np.float64)
            ).T
            t, unit = np.nonzero(spikes[: stop - start])
            found_times.append(start + t)
            spike_units.append(rows.start + unit)

        _run_bidirectional(
            source=source,
            whiten_mat=whiten_mat_by_window[win_global],
            mu_filters=filters,
            calib_start=calib_start,
            calib_end=calib_end,
            params=params,
            fsamp=fsamp,
            sink=_collect,
        )
        if artifact_mask is not None:
            pulse_trains[rows][:, extend_mask(artifact_mask[:total_samples], source.ex_factor)] = (
                0.0
            )
        mu_nb += filters.shape[1]

    times = np.concatenate(found_times) if found_times else np.array([], dtype=int)
    units = np.concatenate(spike_units) if spike_units else np.array([], dtype=int)
    order = np.lexsort((times, units))
    by_unit = np.split(
        times[order].astype(int), np.searchsorted(units[order], np.arange(1, total_mus))
    )

    # Detection is per batch, so a pair straddling a batch boundary or the
    # backward/forward seam can still breach the refractory period.
    spike_times = [enforce_refractory(by_unit[j], pulse_trains[j], fsamp) for j in range(total_mus)]

    return pulse_trains, spike_times
