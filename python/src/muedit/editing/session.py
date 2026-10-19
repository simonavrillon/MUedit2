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
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from muedit.decomp.types import (
    DEFAULT_COMPUTE_DTYPE,
    DEFAULT_NBEXTCHAN,
    DEFAULT_PEEL_OFF_WIN_SEC,
)
from muedit.editing.edit_log import EditLog, RecoverableLog, count_edit
from muedit.editing.operations import (
    delete_spikes_in_roi,
    edge_samples,
    peaks_in_roi,
    remove_discharge_rate_outliers,
    update_motor_unit_filter_window,
)
from muedit.errors import MueditError
from muedit.io.npz import RowSource
from muedit.models import EditSignalContext, FloatArray, IntArray, resident_nbytes, spike_array
from muedit.signal.filters import emg_filter_inplace, for_each_row
from muedit.signal.grid import fallback_grid_name, format_hdemg_signal, grid_rows, grid_spec
from muedit.storage import SessionStore, StoreClosedError, copy_into

logger = logging.getLogger(__name__)

#: Operations kept for undo; older ones are forgotten.
MAX_UNDO = 100
#: Undo patches up to this size stay on the heap; larger ones go to the session store.
HEAP_PATCH_BYTES = 1024 * 1024
SESSION_EXPIRED = "Edit session expired; open the file again"

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
#: ``EditSession`` lists with one item per MU, in the order ``duplicate`` appends to them.
_PER_MU_FIELDS = (
    "spike_times",
    "artifact_times",
    "flagged",
    "mu_grid_index",
    "mu_uids",
    "rows",
    "owned",
    "original_spikes",
    "original_rows",
    "versions",
)


class EditError(MueditError):
    """An edit that cannot be applied: a bad index or window, or no EMG to refit on."""


def out_of_range(field: str, index: object, count: int) -> EditError:
    """The error for an index ``field`` outside ``[0, count)``."""
    return EditError(f"{field} {index} is out of range [0, {count})", field)


def timestamp() -> str:
    """UTC time in the frontend's ``Date.toISOString`` format."""
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def next_mu_uid(grid: int, known: Iterable[str]) -> str:
    """``g<grid>_mu<n>`` with ``n`` above every uid of ``known`` on that grid, so none is reused."""
    prefix = f"g{grid}_mu"
    taken = [
        int(uid[len(prefix) :])
        for uid in known
        if uid[len(prefix) :].isdigit() and uid.startswith(prefix)
    ]
    return f"{prefix}{max(taken) + 1 if taken else 0}"


def initial_mu_uids(mu_grid_index: Sequence[int]) -> list[str]:
    """Uids for MUs that have none yet: numbered from 0 per grid."""
    uids: list[str] = []
    for grid in mu_grid_index:
        uids.append(next_mu_uid(grid, uids))
    return uids


def _diff(before: IntArray, after: IntArray) -> tuple[list[int], list[int]]:
    """Samples in ``after`` only, and in ``before`` only."""
    return (
        np.setdiff1d(after, before, assume_unique=True).tolist(),
        np.setdiff1d(before, after, assume_unique=True).tolist(),
    )


@dataclass
class _MuState:
    spike_times: IntArray
    artifact_times: IntArray
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
        spike_times: Sequence[Any],
        pulse_trains: FloatArray | None,
        mu_grid_index: list[int],
        mu_uids: list[str],
        artifact_times: Sequence[Any] | None = None,
        history: list[dict[str, Any]] | None = None,
        signal: EditSignalContext | None = None,
        bids_grid: BidsGridReader | None = None,
        duplicates: DuplicateFinder | None = None,
    ) -> None:
        n_mu = len(spike_times)
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
        self.sil_by_uid: dict[str, float] = {}  # the file's silhouette score of each MU it held
        self._filtered: dict[tuple[str | None, int], tuple[FloatArray, float, IntArray]] = {}
        # One lock per grid, so a grid filters once while edits go on under ``lock``.
        self._filter_locks: dict[tuple[str | None, int], threading.Lock] = {}
        self._filter_locks_guard = threading.Lock()
        self._closed = False

        self.arrays: dict[str, np.ndarray] = {}
        has_pulse = (
            pulse_trains is not None and pulse_trains.ndim == 2 and pulse_trains.shape[0] == n_mu
        )
        if has_pulse:
            assert pulse_trains is not None
            self.arrays["base"] = pulse_trains
        self.rows: list[PulseRef] = [("base", i) if has_pulse else None for i in range(n_mu)]
        self.owned = [False] * n_mu
        self.spike_times = [spike_array(s) for s in spike_times]
        raw_artifacts = list(artifact_times or [])
        self.artifact_times = [
            spike_array(raw_artifacts[i] if i < len(raw_artifacts) else []) for i in range(n_mu)
        ]
        self.flagged = [False] * n_mu
        self.mu_grid_index = [int(g) for g in mu_grid_index]
        self.mu_uids = [str(u) for u in mu_uids]
        self.original_spikes = [s.copy() for s in self.spike_times]
        self.original_rows = list(self.rows)
        self._counter = itertools.count(1)
        self.versions = [next(self._counter) for _ in range(n_mu)]
        self.history: list[dict[str, Any]] = list(history or [])
        self.undo_stack: list[_Undo] = []
        self.unsaved = 0  # edits since the file was opened or saved, less the undos

    # ── state ───────────────────────────────────────────────────────────────

    @property
    def n_mu(self) -> int:
        return len(self.spike_times)

    @property
    def dirty(self) -> bool:
        """Whether edits were made since the file was opened or saved: what its log keeps."""
        return self.unsaved > 0

    @property
    def can_undo(self) -> bool:
        return bool(self.undo_stack)

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def nbytes(self) -> int:
        """Heap bytes: discharge times, undo patches and the signal context."""
        arrays: list[np.ndarray] = [*self.spike_times, *self.artifact_times, *self.original_spikes]
        arrays += [step.patch[1] for step in self.undo_stack if step.patch is not None]
        arrays += [step.before.spike_times for step in self.undo_stack if step.before is not None]
        total = sum(resident_nbytes(a) for a in arrays)
        # A snapshot: this runs under the budget lock, not ``lock``, while edits change ``arrays``.
        total += sum(resident_nbytes(a) for a in list(self.arrays.values()))
        return total + (self.signal.nbytes if self.signal is not None else 0)

    def check_mu(self, mu: object) -> int:
        """``mu`` as a valid MU index; a request may send anything, ``null`` included."""
        if not isinstance(mu, int) or not 0 <= mu < self.n_mu:
            raise out_of_range("mu", mu, self.n_mu)
        return mu

    def _check_fsamp(self) -> None:
        if self.fsamp <= 0:
            raise EditError("fsamp is required")

    def has_pulse(self, mu: int) -> bool:
        """Whether the MU has a pulse train, not one drawn from its discharge times."""
        return self.rows[mu] is not None

    def values(self, mu: int, start: int, end: int) -> FloatArray:
        """The MU's pulse train over ``[start, end)``: float32, read-only (a copy for a drawn train)."""
        start, end = max(0, start), min(self.total_samples, end)
        ref = self.rows[mu]
        if ref is not None:
            key, row = ref
            view = self.arrays[key][row, start:end].view()
            view.flags.writeable = False
            return view
        drawn = np.zeros(max(0, end - start), dtype=np.float32)
        spikes = self.spike_times[mu]
        inside = spikes[(spikes >= start) & (spikes < end)]
        drawn[inside - start] = 1.0
        return drawn

    def values_at(self, mu: int, times: IntArray) -> FloatArray:
        """The MU's pulse train at ``times`` (float32)."""
        times = np.asarray(times, dtype=np.int64)
        ref = self.rows[mu]
        if ref is None:
            return np.isin(times, self.spike_times[mu]).astype(np.float32)
        key, row = ref
        out = np.zeros(times.shape, dtype=np.float32)
        ok = (times >= 0) & (times < self.total_samples)
        out[ok] = self.arrays[key][row, times[ok]]
        return out

    def _pulse_window(self, mu: int, lo: int, hi: int) -> FloatArray:
        """A float64 copy of the pulse train over ``[lo, hi)``, as the ROI edits compute in."""
        return np.asarray(self.values(mu, lo, hi), dtype=np.float64)

    def _snapshot(self, mu: int) -> _MuState:
        return _MuState(
            self.spike_times[mu], self.artifact_times[mu], self.flagged[mu], self.rows[mu]
        )

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
        if len(self.undo_stack) > MAX_UNDO:
            self._forget(self.undo_stack.pop(0))
            self._drop_unused_rows()  # the trains only the forgotten step went back to

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
        "remove-outliers",
        "update-filter",
        "flag",
        "reset",
        "duplicate",
        "remove-duplicates",
        "undo",
    )

    def apply(self, op: str, args: dict[str, Any]) -> Change:
        """Run one operation by name and record it in the log; one that fails changes nothing."""
        with self.lock:
            if self._closed:
                raise EditError(SESSION_EXPIRED, "token")
            if op not in self.OPS:
                raise EditError(f"Unknown edit operation {op!r}")
            handler: Callable[..., Change] = getattr(self, op.replace("-", "_"))
            try:  # a replayed log's arguments come unchecked
                inspect.signature(handler).bind(**args)
            except TypeError as exc:
                raise EditError(f"Invalid arguments for {op}: {exc}") from exc
            top = self.undo_stack[-1] if self.undo_stack else None
            try:
                change = handler(**args)
            except BaseException:
                self._roll_back(top)
                raise
            self.unsaved = count_edit(self.unsaved, op)
            self._record(op, args)
            return change

    def _roll_back(self, top: _Undo | None) -> None:
        """Undo the steps a failed operation pushed above ``top``, with what it changed."""
        while self.undo_stack and self.undo_stack[-1] is not top:
            step = self.undo_stack[-1]
            try:
                self.undo()
            except Exception:  # noqa: BLE001  (logged; the step is dropped instead)
                logger.warning("Rolling back a failed edit failed", exc_info=True)
                if self.undo_stack and self.undo_stack[-1] is step:
                    self._forget(self.undo_stack.pop())

    def _record(self, op: str, args: dict[str, Any]) -> None:
        """Append to the log; one that cannot be written is closed, keeping what it holds."""
        if self.log is None:
            return
        try:
            self.log.append(op, args)
        except OSError:
            # The lines written so far replay to an earlier state; later edits are only in memory.
            logger.warning(
                "Writing the edit log %s failed; later edits will not be recoverable",
                self.log.path,
                exc_info=True,
            )
            self.log.close(keep=self.log.net > 0)
            self.log = None

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
            window = self._pulse_window(mu, w_lo, w_hi)
            found = peaks_in_roi(window, self.fsamp, lo - w_lo, hi - w_lo, height)
            peaks = found.astype(np.int64) + w_lo
        if artifacts:
            before = self.artifact_times[mu]
            self.artifact_times[mu] = spike_array(np.concatenate([before, peaks]))
            added, _ = _diff(before, self.artifact_times[mu])
            self._entry(kind, mu, artifacts_added=added or None)
        else:
            before = self.spike_times[mu]
            self.spike_times[mu] = spike_array(np.concatenate([before, peaks]))
            added, removed = _diff(before, self.spike_times[mu])
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
        window = self._pulse_window(mu, lo, hi)

        def delete(times: IntArray) -> IntArray:
            inside = (times >= lo) & (times < hi)
            kept = delete_spikes_in_roi(window, times[inside] - lo, x0 - lo, x1 - lo, low, high)
            return spike_array(np.concatenate([times[~inside], kept + lo]))

        before = self.spike_times[mu]
        self.spike_times[mu] = delete(before)
        _, removed = _diff(before, self.spike_times[mu])
        if removed:
            self._entry("delete_spikes", mu, spikes_removed=removed)
        artifacts_before = self.artifact_times[mu]
        self.artifact_times[mu] = delete(artifacts_before)
        _, gone = _diff(artifacts_before, self.artifact_times[mu])
        if gone:
            self._entry("delete_artifact", mu, artifacts_removed=gone)
        self.flagged[mu] = False
        self._touch(mu)
        return Change([mu], start)

    def remove_outliers(self, mu: int) -> Change:
        """Remove one discharge of each pair whose rate is an outlier."""
        self.check_mu(mu)
        self._check_fsamp()
        start = len(self.history)
        self._begin("remove_outliers", mu)
        before = self.spike_times[mu]
        pulse_train = self.values(mu, 0, self.total_samples)
        self.spike_times[mu] = remove_discharge_rate_outliers(pulse_train, before, self.fsamp)
        _, removed = _diff(before, self.spike_times[mu])
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
        peel_off_win: float | None = None,
    ) -> Change:
        """Refit the MU filter on the EMG in view and redetect its discharges there.

        Settings are the run's (``meta["parameters"]``), else the run defaults; ``peel_off_win``
        left None is the run's too.
        """
        self.check_mu(mu)
        run = self.meta.get("parameters") or {}
        nbextchan = int(run.get("nbextchan") or DEFAULT_NBEXTCHAN)
        peel_off_win = float(peel_off_win or run.get("peel_off_win") or DEFAULT_PEEL_OFF_WIN_SEC)
        compute_dtype = run.get("compute_dtype") or DEFAULT_COMPUTE_DTYPE
        view_start, view_end = int(view_start), int(view_end)
        if view_end <= view_start:
            raise EditError("view_start/view_end are required")
        grid = self.mu_grid_index[mu]
        emg, fsamp, discard_mask = self._grid_emg(project, grid)
        if view_start < 0 or view_end > emg.shape[1]:
            raise EditError("view window exceeds available EMG samples")
        start = len(self.history)
        step = self._begin("update_filter", mu)
        others = [
            self.spike_times[i]
            for i in range(self.n_mu)
            if i != mu and self.mu_grid_index[i] == grid and not self.flagged[i]
        ]
        pulse_train, updated = update_motor_unit_filter_window(
            emg,
            discard_mask,
            self.spike_times[mu],
            fsamp,
            view_start,
            view_end,
            nbextchan=nbextchan,
            peel_off_spike_times=others if use_peeloff else None,
            peel_off_win=peel_off_win,
            artifact_times=self.artifact_times[mu],
            lock_spikes=bool(lock_spikes),
            artifact_mask=self.signal.artifact_mask if self.signal is not None else None,
            compute_dtype=compute_dtype,
        )
        if pulse_train is not None:
            edge = edge_samples(fsamp)
            seg_start = view_start + edge
            seg_end = min(view_start + len(pulse_train) - edge, self.total_samples)
            if seg_end > seg_start and len(pulse_train) > 2 * edge:
                row, copied = self._own_row(mu)
                if not copied:
                    step.patch = (seg_start, self._patch_copy(row[seg_start:seg_end]))
                row[seg_start:seg_end] = pulse_train[edge : edge + (seg_end - seg_start)]
        before = self.spike_times[mu]
        self.spike_times[mu] = updated
        added, removed = _diff(before, self.spike_times[mu])
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
        try:
            self.store.hold()  # a close meanwhile deletes the store only once this returns
        except StoreClosedError as exc:
            raise EditError("Edit session closed") from exc
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
                filtered = self._filter_grid(project, grid)
                if self._closed:  # close cleared the cache while this filtered
                    raise EditError("Edit session closed")
                self._filtered[key] = filtered
            return self._filtered[key]

    def _filter_grid(self, project: str | None, grid: int) -> tuple[FloatArray, float, IntArray]:
        """One grid's EMG, BIDS or embedded, notched and bandpassed whole into the session store."""
        # The notch is an FFT over the whole recording, so it cannot be applied to a view: the
        # grid is filtered once, whole, the way preprocess_step filters it before decomposing.
        if self.bids_grid is not None:
            found = self.bids_grid(project, grid, self.store)
            if found is not None:
                emg, fsamp, discard_mask = found
                self._filter_rows(
                    emg, emg, fsamp, self._emg_type(self.meta.get("grid_names"), grid)
                )
                return self.store.seal(emg), fsamp, discard_mask
        ctx = self.signal
        missing = "No BIDS EMG available. Reload decomposition MAT and retry filter update."
        if ctx is None or ctx.data.size == 0:
            raise EditError(missing)
        if ctx.fsamp <= 0:
            raise EditError("Missing fsamp in MAT signal context")
        coordinates, _, _, emg_types = format_hdemg_signal(
            ctx.grid_names or [fallback_grid_name(0)]
        )
        if not 0 <= grid < len(coordinates):
            raise out_of_range("grid", grid, len(coordinates))
        span = grid_rows([c.shape[0] for c in coordinates])[grid]
        n_ch = span.stop - span.start
        masks = ctx.discard_channels
        discard_mask = (
            np.asarray(masks[grid], dtype=int)
            if grid < len(masks) and len(masks[grid]) == n_ch
            else np.zeros(n_ch, dtype=int)
        )
        rows = ctx.data[span, :]
        if ctx.prefiltered:  # a v1 file's EMG went through these filters before it was saved
            return rows, ctx.fsamp, discard_mask
        out = self.store.allocate(f"filtered-grid{grid}", rows.shape, np.float32)
        self._filter_rows(rows, out, ctx.fsamp, emg_types[grid])
        return self.store.seal(out), ctx.fsamp, discard_mask

    @staticmethod
    def _emg_type(grid_names: Any, grid: int) -> int:
        """The bandpass of grid ``grid`` from the grid catalogue; raises for a grid it does not know."""
        names = list(grid_names or [])
        if not 0 <= grid < len(names):
            raise out_of_range("grid", grid, len(names))
        return grid_spec(names[grid]).emg_type

    def _filter_rows(self, rows: FloatArray, out: FloatArray, fsamp: float, emg_type: int) -> None:
        """``rows`` notched and bandpassed into float32 ``out``, a few channels at once."""

        def filter_row(row: int) -> None:
            if self._closed:
                raise EditError("Edit session closed")
            channel = out[row : row + 1]
            channel[...] = rows[row : row + 1]
            emg_filter_inplace(channel, fsamp, emg_type)

        for_each_row(filter_row, range(rows.shape[0]), rows.shape[1])

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
        added, removed = _diff(self.spike_times[mu], baseline)
        artifacts_removed = self.artifact_times[mu].tolist()
        was_flagged = self.flagged[mu]
        self.spike_times[mu] = baseline.copy()
        self.artifact_times[mu] = spike_array([])
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
        """A uid on ``grid`` that neither a current MU nor the log has used."""
        known = set(self.mu_uids)
        for entry in self.history:
            known.update(str(entry[k]) for k in ("mu_uid", "source_mu_uid") if entry.get(k))
            known.update(str(uid) for uid in entry.get("removed_mu_uids") or [])
        return next_mu_uid(grid, known)

    def duplicate(self, mu: int) -> Change:
        """Append a copy of the MU, to edit it separately."""
        self.check_mu(mu)
        start = len(self.history)
        grid = self.mu_grid_index[mu]
        uid = self._new_uid(grid)
        # Both now share one train: the next write to either copies it first.
        self.owned[mu] = False
        new_mu = (
            self.spike_times[mu].copy(),
            spike_array([]),
            False,
            grid,
            uid,
            self.rows[mu],
            False,
            self.spike_times[mu].copy(),
            self.rows[mu],
            next(self._counter),
        )
        for name, value in zip(_PER_MU_FIELDS, new_mu, strict=True):
            getattr(self, name).append(value)
        self._push(_Undo("duplicate_mu", start, appended=True))  # once undo has an MU to pop
        self._entry("duplicate_mu", mu_uid=uid, source_mu_uid=self.mu_uids[mu])
        return Change([self.n_mu - 1], start)

    def remove_duplicates(self) -> Change:
        """Drop the MUs the decomposition's duplicate removal finds redundant."""
        start = len(self.history)
        if self.n_mu < 2 or self.duplicates is None:
            return Change([], start, info={"removed_count": 0})
        kept = sorted(self.duplicates(self.spike_times, self.mu_grid_index))
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
        for name in _PER_MU_FIELDS:
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

    def history_tail_start(self, n: int) -> int:
        """Where a client's copy of the log starts: the last ``n`` entries, back to the oldest undo."""
        start = max(0, len(self.history) - n)
        if self.undo_stack:
            start = min(start, self.undo_stack[0].history_len)
        return start

    def undo(self) -> Change:
        """Take back the last operation."""
        if not self.undo_stack:
            raise EditError("Nothing to undo")
        step = self.undo_stack[-1]
        if step.patch is not None:  # first, so a copy that fails leaves the step to retry
            first, values = step.patch
            row, _ = self._own_row(step.mu)
            row[first : first + len(values)] = values
        self.undo_stack.pop()
        del self.history[step.history_len :]
        if step.appended:
            for name in _PER_MU_FIELDS:
                getattr(self, name).pop()
            return Change([], step.history_len, kept=list(range(self.n_mu)))
        mu = step.mu
        assert step.before is not None
        self.spike_times[mu] = step.before.spike_times
        self.artifact_times[mu] = step.before.artifact_times
        self.flagged[mu] = step.before.flagged
        if step.patch is not None:
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
        self.original_spikes = [s.copy() for s in self.spike_times]
        self.original_rows = list(self.rows)
        self.unsaved = 0
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
            self.signal = None  # its EMG may be mapped from the store
            self.store.close()


def _mapped_from(arr: np.ndarray, path: str) -> bool:
    """Whether ``arr`` is a memory map of the file at ``path``."""
    filename = getattr(arr, "filename", None)
    return filename is not None and Path(filename).resolve() == Path(path).resolve()
