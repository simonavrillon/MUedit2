"""Viewport series as MUB1 frames: upload EMG, grid overview, aux, and run or edit pulse trains."""

from __future__ import annotations

from functools import partial
from typing import Any, Literal

import numpy as np
from fastapi.responses import Response

from muedit.api.binary import frame_response
from muedit.api.cache import (
    SignalViews,
    get_edit_session,
    held_run_result,
    hold_upload,
)
from muedit.api.common import missing_upload
from muedit.editing.session import out_of_range
from muedit.errors import MueditError
from muedit.models import FloatArray, IntArray, SignalImport
from muedit.signal.filters import FILTER_BLOCK_ROWS, bandpass_inplace, map_rows
from muedit.signal.grid import grid_rows
from muedit.signal.pyramid import MinMaxPyramid, Reader, SeriesView, envelope, view
from muedit.storage import ArrayStore

SeriesKind = Literal["emg", "overview", "aux"]
MAX_BINS = 8192
#: Samples bandpassed on each side of an EMG window read at full resolution.
FILTER_PAD_SEC = 1.0
PREVIEW_MOVING_AVG_MS: float = 25.0


def moving_average_ms(series: FloatArray, fsamp: float, win_ms: float) -> FloatArray:
    """Compute moving average with window size specified in milliseconds."""
    x = np.asarray(series, dtype=np.float32).reshape(-1)
    if x.size == 0 or fsamp <= 0 or win_ms <= 0:
        return x
    window_samples = max(1, int(round((win_ms / 1000.0) * fsamp)))
    if window_samples == 1:
        return x
    kernel = np.ones(window_samples, dtype=np.float32)
    sums = np.convolve(x, kernel, mode="same")
    counts = np.convolve(np.ones_like(x), kernel, mode="same")
    return (sums / counts).astype(np.float32)


def grid_emg_type(emg_types: list[int], grid: int) -> int:
    """The bandpass of grid ``grid`` (surface when the catalogue gives none)."""
    return emg_types[grid] if grid < len(emg_types) else 1


def bandpassed_row(signal: SignalImport, row: int, emg_type: int) -> FloatArray:
    """Row ``row`` of the EMG bandpassed in float64, as float32."""
    samples = np.array(signal.data[row : row + 1], np.float64)
    bandpass_inplace(samples, signal.fsamp, emg_type)
    return samples[0].astype(np.float32)


def build_signal_views(
    signal: SignalImport, store: ArrayStore, grid_channel_counts: list[int], emg_types: list[int]
) -> tuple[SignalViews, list[FloatArray]]:
    """Bandpass the grids once into QC views in ``store``; also each channel's mean ``|EMG|``."""
    n_rows, n_samples = signal.data.shape
    rows_of = grid_rows(grid_channel_counts, n_rows)
    n_grid_rows = rows_of[-1].stop if rows_of else 0
    emg = MinMaxPyramid.allocate(store, "emg-pyramid", n_grid_rows, n_samples)
    overview = store.allocate("overview", (len(grid_channel_counts), n_samples), np.float32)
    channel_means: list[FloatArray] = []
    abs_sums = np.empty(n_samples)
    row = np.empty(n_samples)
    for grid, rows in enumerate(rows_of):
        first, stop = rows.start, rows.stop
        means = np.zeros(stop - first)
        abs_sums[:] = 0.0
        filter_row = partial(bandpassed_row, signal, emg_type=grid_emg_type(emg_types, grid))
        for r, samples in enumerate(map_rows(filter_row, range(first, stop), n_samples), first):
            emg.write(r, samples[None])
            np.abs(samples, out=row)
            means[r - first] = row.mean()
            abs_sums += row
            del samples  # not alive while the next rows are filtered
        overview[grid] = moving_average_ms(
            abs_sums / max(1, stop - first), signal.fsamp, PREVIEW_MOVING_AVG_MS
        )
        channel_means.append(means)
    del row, abs_sums

    overview = store.seal(overview)
    overview_levels = MinMaxPyramid.allocate(
        store, "overview-pyramid", len(grid_channel_counts), n_samples
    )
    overview_levels.write(0, np.asarray(overview))
    aux = MinMaxPyramid.allocate(store, "aux-pyramid", signal.aux_data.shape[0], n_samples)
    for lo in range(0, signal.aux_data.shape[0], FILTER_BLOCK_ROWS):
        aux.write(lo, np.asarray(signal.aux_data[lo : lo + FILTER_BLOCK_ROWS]))

    views = SignalViews(
        fsamp=float(signal.fsamp),
        grid_names=list(signal.grid_names),
        grid_rows=[(rows.start, rows.stop) for rows in rows_of],
        emg_types=list(emg_types),
        emg=emg.seal(store),
        overview=overview,
        overview_levels=overview_levels.seal(store),
        aux=aux.seal(store),
    )
    return views, channel_means


def _emg_reader(signal: SignalImport, views: SignalViews, grid: int) -> Reader:
    """Full-resolution bandpassed EMG of one grid's rows, filtered on demand with padding."""
    n_samples = signal.data.shape[1]
    pad = int(round(FILTER_PAD_SEC * views.fsamp))
    emg_type = grid_emg_type(views.emg_types, grid)

    def read(rows: slice, start: int, end: int) -> FloatArray:
        lo, hi = max(0, start - pad), min(n_samples, end + pad)
        block = np.array(signal.data[rows, lo:hi], np.float64)
        bandpass_inplace(block, views.fsamp, emg_type)
        return block[:, start - lo : end - lo].astype(np.float32)

    return read


def series_frame(
    kind: SeriesKind, upload_token: str, start: int, end: int, bins: int, grid: int = 0
) -> Response:
    """``[start, end)`` (``end`` 0 = the end) of an upload series: ``samples`` or min/max bins."""
    held = hold_upload(upload_token)
    if held is None:
        raise missing_upload()
    try:
        return _series(kind, held.signal, held.views, start, end, bins, grid)
    finally:
        held.release()


def _series(
    kind: SeriesKind,
    signal: SignalImport,
    views: SignalViews,
    start: int,
    end: int,
    bins: int,
    grid: int,
) -> Response:
    total_samples = int(signal.data.shape[1])
    s, e = _clamp_window(total_samples, start, end)

    names: list[str]
    if kind == "emg":
        if not 0 <= grid < len(views.grid_rows):
            raise out_of_range("grid", grid, len(views.grid_rows))
        # The EMG pyramid's rows are the EMG rows: grids are laid out from row 0.
        first, stop = views.grid_rows[grid]
        read = _emg_reader(signal, views, grid)
        series = view(views.emg, read, slice(first, stop), s, e, bins)
        names = [str(i + 1) for i in range(stop - first)]
    elif kind == "overview":
        overview = views.overview
        series = view(
            views.overview_levels, lambda r, a, b: overview[r, a:b], slice(None), s, e, bins
        )
        names = list(views.grid_names)
    else:
        aux_data = signal.aux_data
        series = view(views.aux, lambda r, a, b: aux_data[r, a:b], slice(None), s, e, bins)
        names = list(signal.aux_names)
    meta: dict[str, Any] = {"series": kind, "fsamp": views.fsamp, "names": names}
    if kind == "emg":
        meta["grid"] = grid
    return _series_frame(series, meta, {}, bins, total_samples)


def _series_frame(
    series: SeriesView,
    meta: dict[str, Any],
    arrays: dict[str, tuple[Any, str]],
    bins: int,
    total_samples: int,
) -> Response:
    """The frame of ``series`` with ``meta`` and ``arrays``: its ``samples``, or ``min`` and ``max``."""
    meta.update(
        start=series.start,
        end=series.end,
        bins=bins,
        factor=series.factor,
        total_samples=total_samples,
    )
    if series.samples is not None:
        meta["kind"] = "samples"
        arrays["samples"] = (series.samples, "f4")
    else:
        meta["kind"] = "envelope"
        arrays["min"] = (series.mins, "f4")
        arrays["max"] = (series.maxs, "f4")
    return frame_response(meta, arrays)


def _missing_token() -> MueditError:
    return MueditError("No open edit session or run result for this token", "token")


def _clamp_window(total_samples: int, start: int, end: int) -> tuple[int, int]:
    s = max(0, min(int(start), total_samples))
    return s, total_samples if end <= 0 else max(s, min(int(end), total_samples))


def _in_view(times: IntArray, start: int, end: int) -> IntArray:
    lo, hi = np.searchsorted(times, [start, end])
    return times[lo:hi]


def pulse_frame(token: str, mu: int, start: int, end: int, bins: int) -> Response:
    """``[start, end)`` of an MU's pulse train, as ``series_frame``, with spikes and artifacts."""
    meta: dict[str, Any]
    arrays: dict[str, tuple[Any, str]]
    edit = get_edit_session(token)
    # The run's store is held until the frame is packed: its pulse trains are mapped from it.
    with held_run_result(None if edit is not None else token) as run:
        if edit is not None:
            with edit.lock:
                if edit.closed:
                    raise _missing_token()
                edit.check_mu(mu)
                total_samples = edit.total_samples
                s, e = _clamp_window(total_samples, start, end)
                series = envelope(lambda a, b: edit.values(mu, a, b), s, e, bins)
                spikes = _in_view(edit.spike_times[mu], s, e)
                artifacts = _in_view(edit.artifact_times[mu], s, e)
                # ``version`` changes with every edit of the MU, so clients cache frames by it.
                meta = {"version": edit.versions[mu], "flagged": edit.flagged[mu]}
                arrays = {
                    "spikes": (spikes, "i4"),
                    "spike_values": (edit.values_at(mu, spikes), "f4"),
                    "artifacts": (artifacts, "i4"),
                    "artifact_values": (edit.values_at(mu, artifacts), "f4"),
                }
        else:
            if run is None:
                raise _missing_token()
            pulse_trains = run.pulse_trains
            if not 0 <= mu < pulse_trains.shape[0]:
                raise out_of_range("mu", mu, pulse_trains.shape[0])
            total_samples = int(pulse_trains.shape[1])
            s, e = _clamp_window(total_samples, start, end)
            series = envelope(lambda a, b: pulse_trains[mu, a:b], s, e, bins)
            spikes = (
                _in_view(run.spike_times[mu], s, e)
                if mu < len(run.spike_times)
                else np.zeros(0, np.int32)
            )
            meta = {"version": 0, "flagged": False}
            arrays = {
                "spikes": (spikes, "i4"),
                "spike_values": (pulse_trains[mu, spikes], "f4"),
            }
        meta.update(series="pulse", mu=mu)
        return _series_frame(series, meta, arrays, bins, total_samples)
