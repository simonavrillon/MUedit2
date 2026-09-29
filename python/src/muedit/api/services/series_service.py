"""Viewport series of an upload: EMG, grid overview and aux envelopes as MUB1 frames."""

from __future__ import annotations

from typing import Any, Literal

import numpy as np
from fastapi import HTTPException
from fastapi.responses import Response

from muedit.api.binary import FRAME_FORMAT, FRAME_MEDIA_TYPE, pack_frame
from muedit.api.cache import SignalViews, _hold_upload
from muedit.io.store import ArrayStore
from muedit.models import FloatArray, SignalImport
from muedit.signal.downsample import PREVIEW_MOVING_AVG_MS, moving_average_ms
from muedit.signal.filters import FILTER_BLOCK_ROWS, bandpass_inplace
from muedit.signal.pyramid import MinMaxPyramid, Reader, SeriesView, view

SeriesKind = Literal["emg", "overview", "aux"]
MAX_BINS = 8192
#: Samples bandpassed on each side of an EMG window read at full resolution.
FILTER_PAD_SEC = 1.0


def grid_rows(grid_counts: list[int], n_rows: int) -> list[tuple[int, int]]:
    """``[first, stop)`` EMG rows of each grid, cut at the rows the signal has."""
    rows = []
    first = 0
    for n_channels in grid_counts:
        rows.append((min(first, n_rows), min(first + n_channels, n_rows)))
        first += n_channels
    return rows


def grid_emg_type(emg_types: list[int], grid: int) -> int:
    """The bandpass of grid ``grid`` (surface when the catalogue gives none)."""
    return emg_types[grid] if grid < len(emg_types) else 1


def bandpassed_rows(signal: SignalImport, lo: int, hi: int, emg_type: int) -> FloatArray:
    """Rows ``[lo, hi)`` of the EMG bandpassed in float64, as float32."""
    block = np.array(signal.data[lo:hi], np.float64)
    bandpass_inplace(block, signal.fsamp, emg_type)
    return block.astype(np.float32)


def build_signal_views(
    signal: SignalImport, store: ArrayStore, grid_counts: list[int], emg_types: list[int]
) -> tuple[SignalViews, list[FloatArray]]:
    """Bandpass the grid channels once and keep what the QC stage draws, in ``store``.

    Also returns each grid's per-channel mean ``|EMG|``. Nothing full-length stays on the
    heap: the EMG pyramid is filled a few rows at a time.
    """
    n_rows, n_samples = signal.data.shape
    rows_of = grid_rows(grid_counts, n_rows)
    n_grid_rows = rows_of[-1][1] if rows_of else 0
    emg = MinMaxPyramid.allocate(store, "emg-pyramid", n_grid_rows, n_samples)
    overview = store.allocate("overview", (len(grid_counts), n_samples), np.float32)
    channel_means: list[FloatArray] = []
    abs_sums = np.empty(n_samples)
    row = np.empty(n_samples)
    for grid, (first, stop) in enumerate(rows_of):
        means = np.zeros(stop - first)
        abs_sums[:] = 0.0
        for lo in range(first, stop, FILTER_BLOCK_ROWS):
            block = bandpassed_rows(
                signal, lo, min(lo + FILTER_BLOCK_ROWS, stop), grid_emg_type(emg_types, grid)
            )
            emg.write(lo, block)
            for r in range(block.shape[0]):
                np.abs(block[r], out=row)
                means[lo - first + r] = row.mean()
                abs_sums += row
            del block  # not alive while the next block is filtered
        overview[grid] = moving_average_ms(
            abs_sums / max(1, stop - first), signal.fsamp, PREVIEW_MOVING_AVG_MS
        )
        channel_means.append(means)
    del row, abs_sums

    overview = store.seal(overview)
    overview_levels = MinMaxPyramid.allocate(store, "overview-pyramid", len(grid_counts), n_samples)
    overview_levels.write(0, np.asarray(overview))
    aux = MinMaxPyramid.allocate(store, "aux-pyramid", signal.auxiliary.shape[0], n_samples)
    for lo in range(0, signal.auxiliary.shape[0], FILTER_BLOCK_ROWS):
        aux.write(lo, np.asarray(signal.auxiliary[lo : lo + FILTER_BLOCK_ROWS]))

    views = SignalViews(
        fsamp=float(signal.fsamp),
        grid_names=list(signal.gridname),
        grid_rows=rows_of,
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


def _missing_upload() -> HTTPException:
    return HTTPException(
        status_code=400,
        detail={
            "field": "upload_token",
            "reason": "Missing or expired upload; request /api/v1/preview-by-path first",
        },
    )


def series_frame(
    kind: SeriesKind, upload_token: str, start: int, end: int, bins: int, grid: int = 0
) -> Response:
    """``[start, end)`` of an upload's ``kind`` series in ``bins`` bins (``end`` 0 = the end).

    The frame holds ``samples`` ``(rows, end - start)`` when the window has no more samples
    than bins, else ``min`` and ``max`` ``(rows, bins)``.
    """
    held = _hold_upload(upload_token)
    if held is None or held.views is None:
        if held is not None:
            held.release()
        raise _missing_upload()
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
    n_samples = int(signal.data.shape[1])
    s = max(0, min(int(start), n_samples))
    e = n_samples if end <= 0 else max(s, min(int(end), n_samples))

    names: list[str]
    if kind == "emg":
        if not 0 <= grid < len(views.grid_rows):
            raise HTTPException(status_code=400, detail="grid out of range")
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
        auxiliary = signal.auxiliary
        series = view(views.aux, lambda r, a, b: auxiliary[r, a:b], slice(None), s, e, bins)
        names = list(signal.auxiliaryname)
    return _frame(series, kind, bins, n_samples, views.fsamp, names, grid)


def _frame(
    series: SeriesView,
    kind: SeriesKind,
    bins: int,
    n_samples: int,
    fsamp: float,
    names: list[str],
    grid: int,
) -> Response:
    meta: dict[str, Any] = {
        "series": kind,
        "start": series.start,
        "end": series.end,
        "bins": bins,
        "factor": series.factor,
        "total_samples": n_samples,
        "fsamp": fsamp,
        "names": names,
    }
    if kind == "emg":
        meta["grid"] = grid
    arrays: dict[str, tuple[Any, str]]
    if series.samples is not None:
        meta["kind"] = "samples"
        arrays = {"samples": (series.samples, "f4")}
    else:
        meta["kind"] = "envelope"
        arrays = {"min": (series.mins, "f4"), "max": (series.maxs, "f4")}
    return Response(
        content=pack_frame(meta, arrays),
        media_type=FRAME_MEDIA_TYPE,
        headers={"x-muedit-format": FRAME_FORMAT},
    )
