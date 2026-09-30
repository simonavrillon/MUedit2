"""The edit session: one decomposition being edited, held at full resolution by the server.

Pulse trains stay where they were loaded (a memory-mapped file member or the session
store); an MU whose train an edit changes gets its own copy in the store first. Discharge
times are sorted int32 arrays, one per MU. Every operation can be undone, and each one is
recorded in an ``EditLog`` so unsaved edits can be replayed after a crash.
"""

from __future__ import annotations

import inspect
import itertools
import logging
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from muedit.decomp.types import DEFAULT_NBEXTCHAN, DEFAULT_PEEL_OFF_WIN_SEC
from muedit.editing.edit_log import EditLog, RecoverableLog
from muedit.editing.operations import (
    add_artifact_in_roi,
    add_spikes_in_roi,
    delete_artifacts_in_roi,
    delete_high_discharge_rate_spikes_in_roi,
    delete_spikes_in_roi,
    remove_discharge_rate_outliers,
    update_motor_unit_filter_window,
)
from muedit.io.npz import RowSource
from muedit.io.store import SessionStore, copy_into
from muedit.models import EditSignalContext, FloatArray, IntArray, resident_nbytes
from muedit.signal.filters import FILTER_BLOCK_ROWS, emg_filter_inplace
from muedit.signal.grid import format_hdemg_signal

logger = logging.getLogger(__name__)

#: Operations kept for undo; older ones are forgotten.
MAX_UNDO = 100
#: Undo patches up to this size stay on the heap; larger ones go to the session store.
HEAP_PATCH_BYTES = 1024 * 1024

#: ``(key, row)`` of an MU's pulse train in ``EditSession.arrays``; None when the file has
#: no pulse trains for it, and a binary train is drawn from its discharge times instead.
PulseRef = tuple[str, int] | None
#: ``(project, grid, store)`` → the grid's raw EMG over the whole recording from the BIDS
#: dataset, written into ``store`` as writable float32 rows, with its fsamp and discard mask;
#: None when the dataset does not have it.
BidsGridReader = Callable[
    [str | None, int, SessionStore], tuple[FloatArray, float, IntArray] | None
]
#: ``(discharge times, grid of each MU)`` → indices of the MUs duplicate removal keeps.
DuplicateFinder = Callable[[list[IntArray], list[int]], list[int]]


class EditError(ValueError):
    """An edit that cannot be applied: a bad index or window, or no EMG to refit on."""


def spike_array(values: Any) -> IntArray:
    """Sorted, unique, non-negative sample indices as int32."""
    arr = np.asarray(values if values is not None else [], dtype=np.int64).reshape(-1)
    return np.unique(arr[arr >= 0]).astype(np.int32)


def timestamp() -> str:
    """UTC time in the frontend's ``Date.toISOString`` format."""
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _diff(before: IntArray, after: IntArray) -> tuple[list[int], list[int]]:
    """Samples in ``after`` only, and in ``before`` only."""
    return (
        np.setdiff1d(after, before, assume_unique=True).tolist(),
        np.setdiff1d(before, after, assume_unique=True).tolist(),
    )


@dataclass
class _MuState:
    spikes: IntArray
    artifacts: IntArray
    flagged: bool
    row: PulseRef


@dataclass
class _Undo:
    op: str
    history_len: int
    mu: int = -1
    before: _MuState | None = None
    patch: tuple[int, FloatArray] | None = None  # (start, values) overwritten in place
    appended: bool = False  # the op added the last MU


@dataclass
class Change:
    """What an operation changed, for the response to the client."""

    changed: list[int] = field(default_factory=list)  # MUs whose spikes, flag or train changed
    history_start: int = 0  # the client's log is cut here, then the new entries appended
    kept: list[int] | None = None  # MUs kept, in their new order, when some were removed
    info: dict[str, Any] = field(default_factory=dict)


class EditSession:
    """Full-resolution state of one decomposition in the edit stage."""

    def __init__(
        self,
        *,
        store: SessionStore,
        fsamp: float,
        total_samples: int,
        spikes: Sequence[Any],
        pulse: FloatArray | None,
        mu_grid_index: list[int],
        mu_uids: list[str],
        artifacts: Sequence[Any] | None = None,
        history: list[dict[str, Any]] | None = None,
        signal: EditSignalContext | None = None,
        bids_grid: BidsGridReader | None = None,
        duplicates: DuplicateFinder | None = None,
    ) -> None:
        n_mu = len(spikes)
        self.store = store
        self.fsamp = float(fsamp)
        self.total_samples = int(total_samples)
        self.signal = signal
        self.bids_grid = bids_grid
        self.duplicates = duplicates
        self.lock = threading.RLock()
        self.log: EditLog | None = None
        self.recovery: RecoverableLog | None = None  # unsaved edits an earlier session left
        self.meta: dict[str, Any] = {}  # file fields the client shows and the save writes back
        self.bids_root: Path | None = None  # BIDS dataset the file was opened from
        self._filtered: dict[tuple[str | None, int], tuple[FloatArray, float, IntArray]] = {}
        # One lock per grid, so a grid filters once while edits go on under ``lock``.
        self._filter_locks: dict[tuple[str | None, int], threading.Lock] = {}
        self._filter_locks_guard = threading.Lock()
        self._closed = False

        self.arrays: dict[str, np.ndarray] = {}
        has_pulse = pulse is not None and pulse.ndim == 2 and pulse.shape[0] == n_mu
        if has_pulse:
            assert pulse is not None
            self.arrays["base"] = pulse
        self.rows: list[PulseRef] = [("base", i) if has_pulse else None for i in range(n_mu)]
        self.owned = [False] * n_mu
        self.spikes = [spike_array(s) for s in spikes]
        raw_artifacts = list(artifacts or [])
        self.artifacts = [
            spike_array(raw_artifacts[i] if i < len(raw_artifacts) else []) for i in range(n_mu)
        ]
        self.flagged = [False] * n_mu
        self.mu_grid_index = [int(g) for g in mu_grid_index]
        self.mu_uids = [str(u) for u in mu_uids]
        self.original_spikes = [s.copy() for s in self.spikes]
        self.original_rows = list(self.rows)
        self._counter = itertools.count(1)
        self.versions = [next(self._counter) for _ in range(n_mu)]
        self.history: list[dict[str, Any]] = list(history or [])
        self.undo_stack: list[_Undo] = []

    # ── state ───────────────────────────────────────────────────────────────

    @property
    def n_mu(self) -> int:
        return len(self.spikes)

    @property
    def dirty(self) -> bool:
        """Whether any MU's discharge times differ from the file's."""
        return any(
            not np.array_equal(now, then)
            for now, then in zip(self.spikes, self.original_spikes, strict=True)
        )

    @property
    def can_undo(self) -> bool:
        return bool(self.undo_stack)

    @property
    def nbytes(self) -> int:
        """Heap bytes: discharge times, undo patches and the signal context."""
        arrays: list[np.ndarray] = [*self.spikes, *self.artifacts, *self.original_spikes]
        arrays += [step.patch[1] for step in self.undo_stack if step.patch is not None]
        arrays += [step.before.spikes for step in self.undo_stack if step.before is not None]
        total = sum(resident_nbytes(a) for a in arrays)
        total += sum(resident_nbytes(a) for a in self.arrays.values())
        return total + (self.signal.nbytes if self.signal is not None else 0)

    def check_mu(self, mu: object) -> int:
        """``mu`` as a valid MU index; a request may send anything, ``null`` included."""
        if not isinstance(mu, int) or not 0 <= mu < self.n_mu:
            raise EditError("mu_index out of range")
        return mu

    def _check_fsamp(self) -> None:
        if self.fsamp <= 0:
            raise EditError("fsamp is required")

    def has_pulse(self, mu: int) -> bool:
        """Whether the MU has a pulse train, not one drawn from its discharge times."""
        return self.rows[mu] is not None

    def values(self, mu: int, start: int, end: int) -> FloatArray:
        """The MU's pulse train over ``[start, end)`` (float32; a copy for a drawn train)."""
        start, end = max(0, start), min(self.total_samples, end)
        ref = self.rows[mu]
        if ref is not None:
            key, row = ref
            return self.arrays[key][row, start:end]
        drawn = np.zeros(max(0, end - start), dtype=np.float32)
        spikes = self.spikes[mu]
        inside = spikes[(spikes >= start) & (spikes < end)]
        drawn[inside - start] = 1.0
        return drawn

    def values_at(self, mu: int, times: IntArray) -> FloatArray:
        """The MU's pulse train at ``times`` (float32)."""
        times = np.asarray(times, dtype=np.int64)
        ref = self.rows[mu]
        if ref is None:
            return np.isin(times, self.spikes[mu]).astype(np.float32)
        key, row = ref
        out = np.zeros(times.shape, dtype=np.float32)
        ok = (times >= 0) & (times < self.total_samples)
        out[ok] = self.arrays[key][row, times[ok]]
        return out

    def _window(self, mu: int, lo: int, hi: int) -> FloatArray:
        """A float64 copy of the pulse train over ``[lo, hi)``, as the ROI edits compute in."""
        return np.asarray(self.values(mu, lo, hi), dtype=np.float64)

    def _snapshot(self, mu: int) -> _MuState:
        return _MuState(self.spikes[mu], self.artifacts[mu], self.flagged[mu], self.rows[mu])

    def _touch(self, mu: int) -> None:
        self.versions[mu] = next(self._counter)

    def _own_row(self, mu: int) -> tuple[np.ndarray, bool]:
        """The MU's pulse train as a writable array only it uses, and whether it was copied."""
        ref = self.rows[mu]
        if ref is not None and self.owned[mu]:
            key, row = ref
            return self.arrays[key][row], False
        key = f"pulse-{next(self._counter)}"
        own = self.store.allocate(key, (1, self.total_samples), np.float32)
        step = 1 << 22
        for start in range(0, self.total_samples, step):
            own[0, start : start + step] = self.values(mu, start, start + step)
        self.arrays[key] = own
        self.rows[mu] = (key, 0)
        self.owned[mu] = True
        return own[0], True

    def _patch_copy(self, values: FloatArray) -> FloatArray:
        if values.nbytes <= HEAP_PATCH_BYTES:
            return np.array(values, dtype=np.float32)
        kept = self.store.allocate(f"undo-{next(self._counter)}", values.shape, np.float32)
        kept[...] = values
        return kept

    def _entry(self, kind: str, mu: int | None = None, **fields: Any) -> dict[str, Any]:
        entry: dict[str, Any] = {"type": kind}
        if mu is not None:
            entry["mu_uid"] = self.mu_uids[mu]
        entry.update({k: v for k, v in fields.items() if v is not None})
        entry["timestamp"] = timestamp()
        self.history.append(entry)
        return entry

    def _push(self, step: _Undo) -> None:
        self.undo_stack.append(step)
        while len(self.undo_stack) > MAX_UNDO:
            self._forget(self.undo_stack.pop(0))

    def _forget(self, step: _Undo) -> None:
        if step.patch is not None:
            self.store.discard(step.patch[1])

    def _clear_undo(self) -> None:
        for step in self.undo_stack:
            self._forget(step)
        self.undo_stack.clear()

    def _begin(self, op: str, mu: int) -> _Undo:
        """Undo record for an operation on one MU, taken before it changes anything."""
        step = _Undo(op, len(self.history), mu, self._snapshot(mu))
        self._push(step)
        return step

    # ── operations ──────────────────────────────────────────────────────────

    OPS = (
        "add-spikes",
        "add-artifact",
        "delete-spikes",
        "delete-dr",
        "remove-outliers",
        "update-filter",
        "flag",
        "reset",
        "duplicate",
        "remove-duplicates",
        "undo",
    )

    def apply(self, op: str, args: dict[str, Any]) -> Change:
        """Run one operation by name and record it in the log."""
        with self.lock:
            handlers: dict[str, Callable[..., Change]] = {
                "add-spikes": self.add_spikes,
                "add-artifact": self.add_artifact,
                "delete-spikes": self.delete_spikes,
                "delete-dr": self.delete_dr,
                "remove-outliers": self.remove_outliers,
                "update-filter": self.update_filter,
                "flag": self.flag,
                "reset": self.reset,
                "duplicate": self.duplicate,
                "remove-duplicates": self.remove_duplicates,
                "undo": self.undo,
            }
            handler = handlers.get(op)
            if handler is None:
                raise EditError(f"Unknown edit operation {op!r}")
            try:
                inspect.signature(handler).bind(**args)
            except TypeError as exc:
                raise EditError(f"Invalid arguments for {op}: {exc}") from exc
            change = handler(**args)
            if self.log is not None:
                self.log.append(op, args)
            return change

    def replay(self, records: Sequence[dict[str, Any]]) -> int:
        """Re-run logged operations; returns how many applied.

        An operation the session refuses is skipped. Any other failure stops the
        replay there, since the operations after it were made on its result.
        """
        applied = 0
        for record in records:
            try:
                self.apply(str(record.get("op")), dict(record.get("args") or {}))
            except EditError:
                continue
            except Exception:  # noqa: BLE001  (logged; the rest depends on it)
                logger.warning("Replay stopped at %s", record.get("op"), exc_info=True)
                break
            applied += 1
        return applied

    def add_spikes(self, mu: int, x_start: int, x_end: int, y_min: float | None = None) -> Change:
        """Add the pulse-train peaks above ``y_min`` inside ``[x_start, x_end]``."""
        return self._add_peaks("add_spikes", mu, x_start, x_end, y_min, artifacts=False)

    def add_artifact(self, mu: int, x_start: int, x_end: int, y_min: float | None = None) -> Change:
        """Mark the pulse-train peaks above ``y_min`` inside ``[x_start, x_end]`` as artifacts."""
        return self._add_peaks("add_artifact", mu, x_start, x_end, y_min, artifacts=True)

    def _add_peaks(
        self, kind: str, mu: int, x_start: int, x_end: int, y_min: float | None, *, artifacts: bool
    ) -> Change:
        self.check_mu(mu)
        self._check_fsamp()
        start = len(self.history)
        self._begin(kind, mu)
        lo, hi = max(0, int(x_start)), min(self.total_samples - 1, int(x_end))
        height = float("inf") if y_min is None else float(y_min)
        peaks = np.zeros(0, dtype=np.int64)
        if hi >= lo:
            # One zero sample on each side keeps peak picking as it is on the whole train.
            w_lo, w_hi = max(0, lo - 1), min(self.total_samples, hi + 2)
            find = add_artifact_in_roi if artifacts else add_spikes_in_roi
            found = find(self._window(mu, w_lo, w_hi), [], self.fsamp, lo - w_lo, hi - w_lo, height)
            peaks = np.asarray(found, dtype=np.int64) + w_lo
        if artifacts:
            before = self.artifacts[mu]
            self.artifacts[mu] = spike_array(np.concatenate([before, peaks]))
            added, _ = _diff(before, self.artifacts[mu])
            self._entry(kind, mu, artifacts_added=added or None)
        else:
            before = self.spikes[mu]
            self.spikes[mu] = spike_array(np.concatenate([before, peaks]))
            added, removed = _diff(before, self.spikes[mu])
            self._entry(kind, mu, spikes_added=added or None, spikes_removed=removed or None)
        self.flagged[mu] = False
        self._touch(mu)
        return Change([mu], start)

    def delete_spikes(
        self,
        mu: int,
        x_start: int,
        x_end: int,
        y_min: float | None = None,
        y_max: float | None = None,
    ) -> Change:
        """Delete the discharges and artifacts inside the box ``[x_start, x_end] × [y_min, y_max]``."""
        self.check_mu(mu)
        low, high = y_min or 0.0, y_max or 0.0
        start = len(self.history)
        self._begin("delete_spikes", mu)
        x0, x1 = int(min(x_start, x_end)), int(max(x_start, x_end))
        lo, hi = max(0, x0), min(self.total_samples, x1 + 1)
        window = self._window(mu, lo, hi)

        def delete(times: IntArray) -> IntArray:
            inside = (times >= lo) & (times < hi)
            kept = delete_spikes_in_roi(
                window, (times[inside] - lo).tolist(), x0 - lo, x1 - lo, low, high
            )
            return spike_array(np.concatenate([times[~inside], np.asarray(kept, int) + lo]))

        before = self.spikes[mu]
        self.spikes[mu] = delete(before)
        _, removed = _diff(before, self.spikes[mu])
        if removed:
            self._entry("delete_spikes", mu, spikes_removed=removed)
        artifacts_before = self.artifacts[mu]
        if artifacts_before.size:
            inside = (artifacts_before >= lo) & (artifacts_before < hi)
            kept = delete_artifacts_in_roi(
                window,
                (artifacts_before[inside] - lo).tolist(),
                x0 - lo,
                x1 - lo,
                low,
                high,
            )
            self.artifacts[mu] = spike_array(
                np.concatenate([artifacts_before[~inside], np.asarray(kept, int) + lo])
            )
            _, gone = _diff(artifacts_before, self.artifacts[mu])
            if gone:
                self._entry("delete_artifact", mu, artifacts_removed=gone)
        self.flagged[mu] = False
        self._touch(mu)
        return Change([mu], start)

    def delete_dr(self, mu: int, x_start: int, x_end: int, y_min: float | None = None) -> Change:
        """Delete one discharge of each pair faster than ``y_min`` Hz centred in the ROI."""
        self.check_mu(mu)
        self._check_fsamp()
        start = len(self.history)
        self._begin("delete_dr", mu)
        height = float("inf") if y_min is None else float(y_min)
        before = self.spikes[mu]
        kept = delete_high_discharge_rate_spikes_in_roi(
            _PulseLookup(self, mu), before.tolist(), self.fsamp, x_start, x_end, height
        )
        self.spikes[mu] = spike_array(kept)
        added, removed = _diff(before, self.spikes[mu])
        self._entry("delete_dr", mu, spikes_added=added or None, spikes_removed=removed or None)
        self.flagged[mu] = False
        self._touch(mu)
        return Change([mu], start)

    def remove_outliers(self, mu: int) -> Change:
        """Remove one discharge of each pair whose rate is an outlier."""
        self.check_mu(mu)
        self._check_fsamp()
        start = len(self.history)
        self._begin("remove_outliers", mu)
        before = self.spikes[mu]
        kept = remove_discharge_rate_outliers(_PulseLookup(self, mu), before.tolist(), self.fsamp)
        self.spikes[mu] = spike_array(kept)
        _, removed = _diff(before, self.spikes[mu])
        if removed:
            self._entry("remove_outliers", mu, spikes_removed=removed)
        self.flagged[mu] = False
        self._touch(mu)
        return Change([mu], start, info={"removed_count": len(removed)})

    def update_filter(
        self,
        mu: int,
        view_start: int,
        view_end: int,
        use_peeloff: bool = False,
        lock_spikes: bool = False,
        project: str | None = None,
        nbextchan: int = DEFAULT_NBEXTCHAN,
        peel_off_win: float = DEFAULT_PEEL_OFF_WIN_SEC,
    ) -> Change:
        """Refit the MU filter on the EMG in view and redetect its discharges there."""
        self.check_mu(mu)
        view_start, view_end = int(view_start), int(view_end)
        if view_end <= view_start:
            raise EditError("view_start/view_end are required")
        grid = self.mu_grid_index[mu]
        emg, fsamp, emg_mask = self._grid_emg(project, grid)
        if view_start < 0 or view_end > emg.shape[1]:
            raise EditError("view window exceeds available EMG samples")
        start = len(self.history)
        step = self._begin("update_filter", mu)
        others = [
            self.spikes[i].tolist()
            for i in range(self.n_mu)
            if i != mu and self.mu_grid_index[i] == grid and not self.flagged[i]
        ]
        pt, updated = update_motor_unit_filter_window(
            emg,
            emg_mask,
            self.spikes[mu].tolist(),
            fsamp,
            view_start,
            view_end,
            nbextchan=int(nbextchan),
            peeloff_spike_times=others,
            peeloff_win=peel_off_win if peel_off_win > 0 else DEFAULT_PEEL_OFF_WIN_SEC,
            use_peeloff=bool(use_peeloff),
            artifact_times=self.artifacts[mu].tolist() or None,
            lock_spikes=bool(lock_spikes),
            artifact_mask=self.signal.artifact_mask if self.signal is not None else None,
            bandpass=False,  # filtered as the decomposition was, by _grid_emg
        )
        if pt is not None:
            edge = int(round(0.1 * fsamp))
            seg_start = view_start + edge
            seg_end = min(view_start + len(pt) - edge, self.total_samples)
            if seg_end > seg_start and len(pt) > 2 * edge:
                row, copied = self._own_row(mu)
                if not copied:
                    step.patch = (seg_start, self._patch_copy(row[seg_start:seg_end]))
                row[seg_start:seg_end] = pt[edge : edge + (seg_end - seg_start)]
        before = self.spikes[mu]
        self.spikes[mu] = spike_array(updated)
        added, removed = _diff(before, self.spikes[mu])
        self._entry(
            "update_filter",
            mu,
            view_start=view_start,
            view_end=view_end,
            use_peeloff=bool(use_peeloff),
            lock_spikes=bool(lock_spikes),
            spikes_added=added or None,
            spikes_removed=removed or None,
        )
        self.flagged[mu] = False
        self._touch(mu)
        return Change([mu], start, info={"fsamp": fsamp})

    def prepare_grid(self, project: str | None, grid: int) -> None:
        """Filter one grid's EMG ahead of its first filter update; call it without ``lock``."""
        if grid not in self.mu_grid_index:
            return  # no MU to refit there
        self.store.hold()  # a close meanwhile deletes the store only once this returns
        try:
            self._grid_emg(project, grid)
        finally:
            self.store.release()

    def _grid_emg(self, project: str | None, grid: int) -> tuple[FloatArray, float, IntArray]:
        """``(emg, fsamp, discard mask)`` of one grid, filtered as the decomposition was (cached)."""
        key = (project, grid)
        with self._filter_locks_guard:
            lock = self._filter_locks.setdefault(key, threading.Lock())
        with lock:
            if self._closed:
                raise EditError("Edit session closed")
            if key not in self._filtered:
                self._filtered[key] = self._filter_grid(project, grid)
            return self._filtered[key]

    def _filter_grid(self, project: str | None, grid: int) -> tuple[FloatArray, float, IntArray]:
        """One grid's EMG, BIDS or embedded, notched and bandpassed whole into the session store."""
        # The notch is an FFT over the whole recording, so it cannot be applied to a view: the
        # grid is filtered once, whole, the way preprocess_step filters it before decomposing.
        if self.bids_grid is not None:
            found = self.bids_grid(project, grid, self.store)
            if found is not None:
                emg, fsamp, mask = found
                self._filter_rows(
                    emg, emg, fsamp, self._emg_type(self.meta.get("grid_names"), grid)
                )
                return self.store.seal(emg), fsamp, mask
        ctx = self.signal
        missing = "No BIDS EMG available. Reload decomposition MAT and retry filter update."
        if ctx is None or ctx.data.size == 0:
            raise EditError(missing)
        data = ctx.data.reshape(1, -1) if ctx.data.ndim == 1 else ctx.data
        if data.ndim != 2:
            raise EditError("Invalid cached EMG context")
        if data.shape[0] > data.shape[1]:
            data = data.T
        if ctx.fsamp <= 0:
            raise EditError("Missing fsamp in MAT signal context")
        coordinates, _, _, emg_types = format_hdemg_signal(ctx.grid_names or ["Grid 1"])
        if not 0 <= grid < len(coordinates):
            raise EditError("grid_index out of range")
        first = sum(int(coordinates[g].shape[0]) for g in range(grid))
        n_ch = int(coordinates[grid].shape[0])
        cell = ctx.emgmask[grid] if grid < len(ctx.emgmask) else np.array([], dtype=int)
        rows = data[first : first + n_ch, :]
        mask = _discard_mask(cell, n_ch)
        if ctx.prefiltered:  # a v1 file's EMG went through these filters before it was saved
            return rows, ctx.fsamp, mask
        out = self.store.allocate(f"filtered-grid{grid}", rows.shape, np.float32)
        self._filter_rows(rows, out, ctx.fsamp, emg_types[grid] if grid < len(emg_types) else 1)
        return self.store.seal(out), ctx.fsamp, mask

    @staticmethod
    def _emg_type(grid_names: Any, grid: int) -> int:
        """The bandpass of grid ``grid`` from the grid catalogue (surface when unknown)."""
        _, _, _, emg_types = format_hdemg_signal(list(grid_names or []) or ["Grid 1"])
        return emg_types[grid] if grid < len(emg_types) else 1

    def _filter_rows(self, rows: FloatArray, out: FloatArray, fsamp: float, emg_type: int) -> None:
        """``rows`` notched and bandpassed into float32 ``out``, a few channels at a time."""
        for lo in range(0, rows.shape[0], FILTER_BLOCK_ROWS):
            if self._closed:
                raise EditError("Edit session closed")
            block = out[lo : lo + FILTER_BLOCK_ROWS]
            block[...] = rows[lo : lo + FILTER_BLOCK_ROWS]
            emg_filter_inplace(block, fsamp, emg_type)

    def flag(self, mu: int, flag: bool | None = None) -> Change:
        """Flag the MU for removal on save, or clear the flag."""
        self.check_mu(mu)
        start = len(self.history)
        self._begin("flag_mu", mu)
        self.flagged[mu] = True if flag is None else bool(flag)
        self._entry("flag_mu", mu, flagged=self.flagged[mu])
        self._touch(mu)
        return Change([mu], start)

    def reset(self, mu: int) -> Change:
        """Bring the MU back to the file's discharge times and pulse train."""
        self.check_mu(mu)
        start = len(self.history)
        self._begin("reset_mu", mu)
        baseline = self.original_spikes[mu]
        added, removed = _diff(self.spikes[mu], baseline)
        artifacts_removed = self.artifacts[mu].tolist()
        was_flagged = self.flagged[mu]
        self.spikes[mu] = baseline.copy()
        self.artifacts[mu] = spike_array([])
        self.flagged[mu] = False
        if self.rows[mu] != self.original_rows[mu]:
            self.rows[mu] = self.original_rows[mu]
            self.owned[mu] = False
        self._entry(
            "reset_mu",
            mu,
            spikes_added=added or None,
            spikes_removed=removed or None,
            artifacts_removed=artifacts_removed or None,
            flagged=False if was_flagged else None,
        )
        self._touch(mu)
        return Change([mu], start)

    def _new_uid(self, grid: int) -> str:
        """``g<grid>_mu<n>`` with ``n`` above every uid the log has named on that grid."""
        known = set(self.mu_uids)
        for entry in self.history:
            known.update(str(entry[k]) for k in ("mu_uid", "source_mu_uid") if entry.get(k))
            known.update(str(uid) for uid in entry.get("removed_mu_uids") or [])
        prefix = f"g{grid}_mu"
        taken = [
            int(uid[len(prefix) :])
            for uid in known
            if uid.startswith(prefix) and uid[len(prefix) :].isdigit()
        ]
        return f"{prefix}{max(taken) + 1 if taken else 0}"

    def duplicate(self, mu: int) -> Change:
        """Append a copy of the MU, to edit it separately."""
        self.check_mu(mu)
        start = len(self.history)
        self._push(_Undo("duplicate_mu", start, appended=True))
        grid = self.mu_grid_index[mu]
        uid = self._new_uid(grid)
        # Both now share one train: the next write to either copies it first.
        self.owned[mu] = False
        self.spikes.append(self.spikes[mu].copy())
        self.artifacts.append(spike_array([]))
        self.flagged.append(False)
        self.mu_grid_index.append(grid)
        self.mu_uids.append(uid)
        self.rows.append(self.rows[mu])
        self.owned.append(False)
        self.original_spikes.append(self.spikes[mu].copy())
        self.original_rows.append(self.rows[mu])
        self.versions.append(next(self._counter))
        self.history.append(
            {
                "type": "duplicate_mu",
                "mu_uid": uid,
                "source_mu_uid": self.mu_uids[mu],
                "timestamp": timestamp(),
            }
        )
        return Change([self.n_mu - 1], start)

    def remove_duplicates(self) -> Change:
        """Drop the MUs the decomposition's duplicate removal finds redundant."""
        start = len(self.history)
        if self.n_mu < 2 or self.duplicates is None:
            return Change([], start, info={"removed_count": 0})
        kept = sorted(self.duplicates(self.spikes, self.mu_grid_index))
        if len(kept) == self.n_mu:
            return Change([], start, info={"removed_count": 0})
        kept_set = set(kept)
        removed_uids = [uid for i, uid in enumerate(self.mu_uids) if i not in kept_set]
        self.keep(kept)
        self._entry(
            "remove_duplicates", removed_count=len(removed_uids), removed_mu_uids=removed_uids
        )
        return Change([], start, kept=kept, info={"removed_count": len(removed_uids)})

    def keep(self, kept: list[int]) -> None:
        """Keep only the MUs at ``kept``, in that order; undo history is dropped."""
        self._clear_undo()
        for name in (
            "spikes",
            "artifacts",
            "flagged",
            "mu_grid_index",
            "mu_uids",
            "rows",
            "owned",
            "original_spikes",
            "original_rows",
            "versions",
        ):
            values = getattr(self, name)
            setattr(self, name, [values[i] for i in kept])
        self._drop_unused_rows()

    def _drop_unused_rows(self) -> None:
        """Delete the stored trains no MU, baseline or undo step refers to any more."""
        refs = [*self.rows, *self.original_rows]
        refs += [step.before.row for step in self.undo_stack if step.before is not None]
        live = {ref[0] for ref in refs if ref is not None}
        for key in [k for k in self.arrays if k not in live]:
            self.store.discard(self.arrays.pop(key))

    def undo(self) -> Change:
        """Take back the last operation."""
        if not self.undo_stack:
            raise EditError("Nothing to undo")
        step = self.undo_stack.pop()
        del self.history[step.history_len :]
        if step.appended:
            for name in (
                "spikes",
                "artifacts",
                "flagged",
                "mu_grid_index",
                "mu_uids",
                "rows",
                "owned",
                "original_spikes",
                "original_rows",
                "versions",
            ):
                getattr(self, name).pop()
            return Change([], step.history_len, kept=list(range(self.n_mu)))
        mu = step.mu
        assert step.before is not None
        self.spikes[mu] = step.before.spikes
        self.artifacts[mu] = step.before.artifacts
        self.flagged[mu] = step.before.flagged
        if step.patch is not None:
            first, values = step.patch
            row, _ = self._own_row(mu)
            row[first : first + len(values)] = values
            self._forget(step)
        elif self.rows[mu] != step.before.row:
            self.rows[mu] = step.before.row
            self.owned[mu] = False
            self._drop_unused_rows()  # the copy the undone refit made
        self._touch(mu)
        return Change([mu], step.history_len, info={"undone": step.op})

    # ── saving ──────────────────────────────────────────────────────────────

    def pulse_rows(self, mus: list[int]) -> RowSource | None:
        """The pulse trains of ``mus``, read a few rows at a time as a save writes them; None when none has one."""
        if not any(self.has_pulse(mu) for mu in mus):
            return None

        def read(first: int, stop: int) -> FloatArray:
            return np.stack([self.values(mu, 0, self.total_samples) for mu in mus[first:stop]])

        return RowSource((len(mus), self.total_samples), read)

    def detach(self, path: str) -> None:
        """Copy the pulse trains and EMG mapped from ``path`` into the store, so the file can be replaced."""
        base = self.arrays.get("base")
        if base is not None and _mapped_from(base, path):
            copy = self.store.allocate("base", base.shape, np.float32)
            step = max(1, (1 << 24) // max(1, base.shape[0]))
            for first in range(0, base.shape[1], step):
                copy[:, first : first + step] = base[:, first : first + step]
            self.arrays["base"] = copy
        if self.signal is not None and _mapped_from(self.signal.data, path):
            self.signal = replace(self.signal, data=copy_into(self.store, "emg", self.signal.data))

    def saved(self, kept: list[int], entries: list[dict[str, Any]]) -> None:
        """The file now holds MUs ``kept``: they become the baseline, and the log starts over."""
        self.keep(kept)
        self.history.extend(entries)
        self.original_spikes = [s.copy() for s in self.spikes]
        self.original_rows = list(self.rows)
        # A baseline train is never written in place: the next refit copies it.
        self.owned = [False] * self.n_mu

    def close(self) -> None:
        """Keep the log only if it holds edits that were not saved, then delete the store."""
        self._closed = True  # a grid being prepared stops at its next block
        with self.lock:
            if self.log is not None:
                self.log.close(keep=self.log.net > 0)
                self.log = None
            self.arrays.clear()
            self._filtered.clear()
            self.store.close()


def _mapped_from(arr: np.ndarray, path: str) -> bool:
    """Whether ``arr`` is a memory map of the file at ``path``."""
    filename = getattr(arr, "filename", None)
    return filename is not None and Path(filename).resolve() == Path(path).resolve()


class _PulseLookup:
    """The MU's pulse train read one sample at a time, for the edits that only look up spikes."""

    def __init__(self, session: EditSession, mu: int) -> None:
        self._session = session
        self._mu = mu

    def __len__(self) -> int:
        return self._session.total_samples

    def __getitem__(self, t: int) -> float:
        session, t = self._session, int(t)
        if not 0 <= t < session.total_samples:
            return 0.0
        ref = session.rows[self._mu]
        if ref is None:
            spikes = session.spikes[self._mu]
            i = int(np.searchsorted(spikes, t))
            return 1.0 if i < spikes.size and spikes[i] == t else 0.0
        key, row = ref
        return float(session.arrays[key][row, t])


def _discard_mask(cell: Any, n_ch: int) -> IntArray:
    """A grid's 0/1 discard mask from a stored one, or from 1- or 0-based discarded indices."""
    cell_arr = np.asarray(cell, dtype=int).flatten()
    if cell_arr.size == n_ch and np.all(np.isin(cell_arr, [0, 1])):
        return cell_arr.copy()
    mask = np.zeros(n_ch, dtype=int)
    if cell_arr.size > 0:
        max_val, min_val = int(np.max(cell_arr)), int(np.min(cell_arr))
        if min_val >= 1 and max_val <= n_ch:
            mask[cell_arr[cell_arr >= 1] - 1] = 1
        elif min_val >= 0 and max_val < n_ch:
            mask[cell_arr[cell_arr >= 0]] = 1
        elif cell_arr.size == n_ch:
            mask = np.asarray(cell_arr != 0, dtype=int)
    return mask
