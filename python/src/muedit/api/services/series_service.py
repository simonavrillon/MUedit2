"""Viewport series as MUB1 frames: an upload's EMG, grid overview and aux envelopes, and the
pulse trains and discharge times of a run or an edit session."""

from __future__ import annotations

from typing import Any, Literal

import numpy as np
from fastapi import HTTPException
from fastapi.responses import Response

from muedit.api.binary import FRAME_FORMAT, FRAME_MEDIA_TYPE, pack_frame
from muedit.api.cache import (
    SignalViews,
    _get_edit_session,
    _get_run_result_entry,
    _hold_upload,
)
from muedit.decomp.decomposition_file import pack_csr
from muedit.io.store import ArrayStore
from muedit.models import FloatArray, IntArray, SignalImport
from muedit.signal.downsample import PREVIEW_MOVING_AVG_MS, moving_average_ms
from muedit.signal.filters import FILTER_BLOCK_ROWS, bandpass_inplace
from muedit.signal.pyramid import MinMaxPyramid, Reader, SeriesView, envelope, view

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


def _missing_token() -> HTTPException:
    return HTTPException(
        status_code=400,
        detail={"field": "token", "reason": "No open edit session or run result for this token"},
    )


def _window(n_samples: int, start: int, end: int) -> tuple[int, int]:
    s = max(0, min(int(start), n_samples))
    return s, n_samples if end <= 0 else max(s, min(int(end), n_samples))


def _in_view(times: IntArray, start: int, end: int) -> IntArray:
    lo, hi = np.searchsorted(times, [start, end])
    return times[lo:hi]


def pulse_frame(token: str, mu: int, start: int, end: int, bins: int) -> Response:
    """``[start, end)`` of one MU's pulse train in ``bins`` bins (``end`` 0 = the end).

    Like the upload series: ``samples`` ``(1, end - start)``, or ``min`` and ``max``
    ``(1, bins)``. Also ``spikes`` and ``artifacts`` in view (int32) with the train's value
    at each (``spike_values``, ``artifact_values``). ``meta.version`` changes with every
    edit of the MU, so a client can cache frames by it.
    """
    meta: dict[str, Any]
    arrays: dict[str, tuple[Any, str]]
    edit = _get_edit_session(token)
    if edit is not None:
        with edit.lock:
            if not 0 <= mu < edit.n_mu:
                raise HTTPException(status_code=400, detail="mu out of range")
            n_samples = edit.total_samples
            s, e = _window(n_samples, start, end)
            series = envelope(lambda a, b: edit.values(mu, a, b), s, e, bins)
            spikes = _in_view(edit.spikes[mu], s, e)
            artifacts = _in_view(edit.artifacts[mu], s, e)
            meta = {
                "version": edit.versions[mu],
                "flagged": edit.flagged[mu],
                "has_pulse": edit.has_pulse(mu),
                "fsamp": edit.fsamp,
            }
            arrays = {
                "spikes": (spikes, "i4"),
                "spike_values": (edit.values_at(mu, spikes), "f4"),
                "artifacts": (artifacts, "i4"),
                "artifact_values": (edit.values_at(mu, artifacts), "f4"),
            }
    else:
        run = _get_run_result_entry(token)
        if run is None:
            raise _missing_token()
        pulse = run.pulse_trains
        if not 0 <= mu < pulse.shape[0]:
            raise HTTPException(status_code=400, detail="mu out of range")
        n_samples = int(pulse.shape[1])
        s, e = _window(n_samples, start, end)
        series = envelope(lambda a, b: pulse[mu, a:b], s, e, bins)
        spikes = _in_view(run.spikes[mu], s, e) if mu < len(run.spikes) else np.zeros(0, np.int32)
        meta = {"version": 0, "flagged": False, "has_pulse": True}
        arrays = {
            "spikes": (spikes, "i4"),
            "spike_values": (pulse[mu, spikes], "f4"),
        }
    meta.update(
        {
            "series": "pulse",
            "mu": mu,
            "start": series.start,
            "end": series.end,
            "bins": bins,
            "factor": series.factor,
            "total_samples": n_samples,
        }
    )
    if series.samples is not None:
        meta["kind"] = "samples"
        arrays["samples"] = (series.samples, "f4")
    else:
        meta["kind"] = "envelope"
        arrays["min"] = (series.mins, "f4")
        arrays["max"] = (series.maxs, "f4")
    return Response(
        content=pack_frame(meta, arrays),
        media_type=FRAME_MEDIA_TYPE,
        headers={"x-muedit-format": FRAME_FORMAT},
    )


def spikes_frame(token: str, mu: str) -> Response:
    """Discharge times of every MU (``mu`` ``all``) or one, as CSR ``spikes``/``spike_offsets``.

    An edit session adds its artifacts the same way (``artifacts``/``artifact_offsets``).
    """
    edit = _get_edit_session(token)
    artifacts: list[IntArray] | None = None
    if edit is not None:
        with edit.lock:
            spikes, artifacts = list(edit.spikes), list(edit.artifacts)
    else:
        run = _get_run_result_entry(token)
        if run is None:
            raise _missing_token()
        spikes = list(run.spikes)
    if mu != "all":
        try:
            index = int(mu)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="mu must be an index or 'all'") from exc
        if not 0 <= index < len(spikes):
            raise HTTPException(status_code=400, detail="mu out of range")
        spikes = [spikes[index]]
        artifacts = [artifacts[index]] if artifacts is not None else None
    values, offsets = pack_csr(spikes, np.int32)
    arrays: dict[str, tuple[Any, str]] = {
        "spikes": (values, "i4"),
        "spike_offsets": (offsets, "i8"),
    }
    if artifacts is not None:
        art_values, art_offsets = pack_csr(artifacts, np.int32)
        arrays["artifacts"] = (art_values, "i4")
        arrays["artifact_offsets"] = (art_offsets, "i8")
    return Response(
        content=pack_frame({"n_mu": len(spikes)}, arrays),
        media_type=FRAME_MEDIA_TYPE,
        headers={"x-muedit-format": FRAME_FORMAT},
    )
