# 05 — Editing Operations

The edit stage edits one decomposition at a time in a server-side **edit session**
(`editing/session.py`). The server holds it at full resolution: pulse trains stay memory-mapped
where they were loaded (a `.npz` member or the session store), and discharge times are sorted
int32 arrays, one per MU. The client holds the discharge times, the per-MU fields and the edit
history. It draws pulse trains from `/series/pulse` and sends each edit as one operation. Every
operation can be undone, and every operation is appended to an on-disk log, so unsaved edits can
be replayed after a crash.

The operations themselves are pure functions in `editing/operations.py`, which return updated
spike lists. `EditSession` runs them on a window of the pulse train and keeps the result.

---

## Architecture

```
Frontend (edit-stage.js, editing-service.js)
  ↓ POST /edit/ops/{op}  {token, mu, x_start, …}
API Route (routes/editing.py)
  ↓
Editing Service (services/editing_service.py)   open, apply, recover, save
  ↓
EditSession (editing/session.py)                state, undo, log, pulse-train copies
  ↓                                              ↘ EditLog (editing/edit_log.py)
Editing Operations (editing/operations.py)
  ↓
Signal Primitives (signal/decomp_primitives.py, signal/filters.py)
  ↓
Decomposition Algorithm (decomp/algorithm.py)
```

### Discharge times

Every operation takes discharge times as anything array-like, normalizes them with
`spike_array` (sorted, unique, non-negative int32), and returns a new array; the operations are
vectorized (no per-spike Python loops).

---

## Operation Catalog

Every operation is `POST /edit/ops/{op}` with the op's model in `api.schemas.EDIT_OPS`, dispatched by
`EditSession.apply(op, args)`. Each one records an undo step and, except `undo` itself, a history
entry of the given type.

| `op` | Arguments | Changes | History entry | Core function |
|---|---|---|---|---|
| `update-filter` | `mu, view_start, view_end, use_peeloff, lock_spikes, project, peel_off_win` | pulse train in view + spikes | `update_filter` | `update_motor_unit_filter_window()` |
| `add-spikes` | `mu, x_start, x_end, y_min` | spikes | `add_spikes` | `peaks_in_roi()` |
| `add-artifact` | `mu, x_start, x_end, y_min` | artifacts | `add_artifact` | `peaks_in_roi()` |
| `delete-spikes` | `mu, x_start, x_end, y_min, y_max` | spikes + artifacts | `delete_spikes`, `delete_artifact` | `delete_spikes_in_roi()` (spikes, then artifacts) |
| `remove-outliers` | `mu` | spikes | `remove_outliers` | `remove_discharge_rate_outliers()` |
| `flag` | `mu, flag` | flag | `flag_mu` | — |
| `reset` | `mu` | spikes, artifacts, flag, train back to the file's | `reset_mu` | — |
| `duplicate` | `mu` | appends a copy of the MU | `duplicate_mu` | — |
| `remove-duplicates` | — | drops MUs; clears the undo stack | `remove_duplicates` | `dedup_survivors()` |
| `undo` | — | takes back the last operation | (removes its entries) | — |

Every per-MU edit clears the MU's flag. A history entry names the MU by `mu_uid` and records the
samples it added or removed (`spikes_added`, `spikes_removed`, `artifacts_added`,
`artifacts_removed`) and a `timestamp`.

---

## The Edit Session (`editing/session.py`)

```python
class EditSession:
    def __init__(self, *, store, fsamp, total_samples, spikes, pulse, mu_grid_index, mu_uids,
                 artifact_times=None, history=None, signal=None, bids_grid=None, duplicates=None)
    def apply(self, op: str, args: dict) -> Change
    def replay(self, records: list[dict]) -> int
```

| State | Description |
|---|---|
| `spikes`, `artifacts` | Sorted int32 sample arrays, one per MU |
| `flagged`, `mu_grid_index`, `mu_uids` | Per-MU fields |
| `rows` | Each MU's pulse train as `(key, row)` into `arrays`, or `None`: the file has none, and a binary train is drawn from the discharge times |
| `owned` | Whether the MU's row is a copy only it uses (and may be written in place) |
| `original_spikes`, `original_rows` | The file's state: `reset` returns to it |
| `unsaved` | Edits applied since the file was opened or saved, less the undos, counted as the log counts them while it can be written; `dirty` is `unsaved > 0`, so a flag or an artifact mark counts as well as changed discharge times |
| `versions` | Bumped by every edit of the MU; `/series/pulse` frames are cached by it |
| `history` | The edit history, saved with the file |
| `undo_stack` | Up to `MAX_UNDO` (100) steps |
| `signal` | The `EditSignalContext` the file embeds (raw EMG, grid masks, artifact mask), or `None` |
| `log`, `recovery` | This session's `EditLog`, and the unsaved edits an earlier session left |

**Pulse-train copies.** A file's trains are never written. The first `update-filter` of an MU
copies its train into the session store (`_own_row`) and writes the refit window there. A
duplicated MU shares its source's train until either one is refit.

**Undo.** `_begin` snapshots the MU (spikes, artifacts, flag, row) before an edit. A refit that
wrote an MU's own copy in place also keeps the overwritten window (`patch`): on the heap up to
1 MB, else in the store. `undo` restores the snapshot, writes the patch back or points the MU at
its previous row, and cuts the history to where it was; one that fails leaves its step in place. An
operation that raises is rolled back the same way, so it changes nothing and is not counted. A
step pushed past `MAX_UNDO` forgets the oldest, and the stored trains only it went back to are
deleted. `remove-duplicates` and a save clear the undo stack. A closed session refuses every
operation with the "Edit session expired" 400.

**`Change`**, returned by every operation, names the MUs whose spikes, flag or train changed
(`changed`), where the client should cut its history (`history_start`), the kept MUs in their new
order when some were removed (`kept`), and extras (`removed_count`, `fsamp`, `undone`).

**Memory.** `nbytes` counts only heap arrays: discharge times, undo patches and the signal
context. Trains, filtered grids and large patches live in the session store, which `close()`
deletes.

---

## 1. Filter Update (`update_motor_unit_filter_window`)

The most complex operation. Recomputes a motor unit's pulse train and spike train within a visible time window by re-running the full ICA pipeline on the windowed EMG segment.

```python
def update_motor_unit_filter_window(
    emg: np.ndarray,              # (n_channels, n_samples) filtered EMG of the grid
    discard_mask: np.ndarray,     # (n_channels,) 1 = discarded channel
    spike_times: ArrayLike,       # existing spike times for this MU
    fsamp: float,
    start: int,                   # window start sample
    end: int,                     # window end sample
    nbextchan: int = DEFAULT_NBEXTCHAN,    # target extended channels (1000)
    peel_off_spike_times: Sequence[ArrayLike] | None = None,  # other MUs' spikes to peel off
    peel_off_win: float = DEFAULT_PEEL_OFF_WIN_SEC,       # 0.025 s
    artifact_times: ArrayLike | None = None,  # artifact spike times to subtract
    lock_spikes: bool = False,    # snap existing spikes to nearest peak
    artifact_mask: np.ndarray | None = None,  # (n_samples,) artifact region mask
    compute_dtype: ComputeDtype = DEFAULT_COMPUTE_DTYPE,  # float32 by default
) -> tuple[np.ndarray | None, IntArray]  # (pulse_train | None, updated_spike_times)
```

### Pipeline

```
1. Slice the kept channels of the EMG to [start, end)
2. Delay-embed (extend_signal) to nbextchan extended channels
3. PCA on the columns spikes are taken from: [trim, L - trim), trim = window_trim with
   EDGE_SEC (0.1 s), past the zero-padded extension ends and the bandpass transients,
   minus artifact columns (pca_extended_signal)
4. Whiten (whiten_extended_signal)
5. Optional peel-off: subtract other MUs' waveforms (subtract_mu_waveforms)
   + subtract artifacts
6. Build MU filter from existing spikes in window
7. Project to get pulse train
8. Signed-square nonlinearity (signed_square)
9. Zero artifact regions
10. Refractory peak detection (find_refractory_peaks)
11. K-means amplitude split (split_by_amplitude) -> high/low clusters
12. Drop spikes inside artifact regions
13. Optional lock_spikes: snap existing spikes to nearest peak (+/- 10 samples),
    then merge with new detections
14. Amplitude ceiling: pulse_train[spike] <= 3 * centroid_high
15. Merge with spikes outside the window
16. Return (pulse_train | None, updated_spike_times)
```

### In the session (`EditSession.update_filter`)

| Argument | Controls | Default |
|---|---|---|
| `view_start` / `view_end` | Window boundaries (required; 400 when empty or past the EMG) | — |
| `peel_off_win` | Peel-off window half-width (s) | the run's, else `0.025` |
| `use_peeloff` | Peel off the other unflagged MUs of the same grid | `False` |
| `lock_spikes` | Snap existing spikes to nearest peaks | `False` |
| `project` | Where to read the BIDS EMG (see `_dataset_root` below) | `None` |

The extended channel count (`nbextchan`, else `1000`) and the type of the extended and whitened
window (`compute_dtype`, else `float32`) are always the run's.

"The run's" is the file's saved `parameters` (`edit.meta["parameters"]`), so a refit extends and
whitens the window as the decomposition did. The frontend sends none of the three.

The MU's own artifacts are subtracted, and the file's temporal artifact mask (from the signal
context) excludes contaminated samples.

The refit runs on the grid's EMG filtered as the decomposition filtered it: notch, then the grid's
own bandpass (`signal.filters.emg_filter_inplace`), over the whole recording, since the FFT notch
cannot be applied to a view. The filtered grid is built once, in the session store, and reused
(`EditSession._grid_emg`). The page asks for it ahead of the first refit
(`/edit/session/prepare-grid` → `EditSession.prepare_grid`) when a file opens, for the grid on
screen, and when the user picks a grid; it runs outside the session lock, so edits go on, and a
refit sent meanwhile waits for the same filtering under the grid's own lock. The raw
EMG comes from:
- **BIDS**: the whole grid read channel by channel into the store (`io.bids.read_bids_emg_grid`)
- **the decomposition file**: its embedded EMG (a v2 `.npz` member mapped in place, or `.mat`);
  a `prefiltered` context (schema v1 `.npz`) went through these filters before it was saved and
  is used as is

With neither, the refit is refused (400). The new train is written into the MU's own copy over
`[view_start + edge, view_end − edge)`, `edge` = `edge_samples(fsamp)` (0.1 s), where the
bandpass transients have died down.

---

## 2. Add Spikes in ROI (`peaks_in_roi`)

```python
def peaks_in_roi(
    pulse_train: np.ndarray,  # pulse train for this MU
    fsamp: float,
    x_start: int,             # ROI start sample
    x_end: int,               # ROI end sample
    y_min: float,             # minimum pulse amplitude threshold
) -> IntArray
```
Zeros everything outside the ROI `[x_start, x_end]` and returns the refractory peaks above the
`y_min` amplitude threshold.

The session passes the ROI's window of the train plus one sample on each side (so peak picking
matches the whole train) and merges the peaks it finds into the MU's spikes. `y_min` omitted means no peak qualifies.
`add-artifact` runs the same function on the MU's artifact times.

---

## 3. Delete Spikes in ROI (`delete_spikes_in_roi`)

```python
def delete_spikes_in_roi(
    pulse_train: np.ndarray,
    spike_times: ArrayLike,
    x_start: int,
    x_end: int,
    y_min: float,
    y_max: float,
) -> IntArray
```
Deletes spikes inside the ROI `[x_start, x_end]` whose pulse amplitude falls within `[min(y_min, y_max), max(y_min, y_max) + 1]`. Spikes outside the ROI or outside the amplitude band are kept.

`EditSession.delete_spikes` also removes the MU's artifacts in the same box
(`delete_spikes_in_roi` on the artifact times), with a `delete_artifact` history entry when any went.

---

## 4. Remove Discharge-Rate Outliers (`remove_discharge_rate_outliers`)

```python
def remove_discharge_rate_outliers(
    pulse_train: FloatArray,
    spike_times: ArrayLike,
    fsamp: float,
) -> IntArray
```
Global discharge-rate outlier removal (no ROI). Computes per-pair discharge rates, sets threshold = `mean + 3 * std` (`_RATE_Z_FACTOR`), and for each pair exceeding the threshold deletes the lower-amplitude spike. Requires >= 3 spikes. Returns the pruned spikes; the change carries `removed_count`.

The factor is a module constant; the API does not expose it.

---

## 5. Remove Duplicates

`EditSession.remove_duplicates` asks the `duplicates` callback the service gave it, which runs
`editing_service._dedup()`: the same duplicate removal as the decomposition pipeline, so
interactive and save-time dedup match what `postprocess_step` produced.

```python
def _dedup(spike_times, mu_grid_index, parameters, fsamp, total_samples) -> list[int]  # kept indices, ascending
```

It calls `decomp.postprocess.dedup_survivors()` (within each grid, then across grids) on the
discharge times, with a `DecompositionParameters` carrying:
- `duplicatesthresh` from the file's `parameters` via `_coerce_dup_tol()` (default `0.3`)
- `duplicatesbgrids` from `parameters` via `_coerce_bool_param()` (default `True`; accepts MATLAB 0/1, nested arrays, strings)

`maxlag = fsamp / 40` and `jitter = 0.00025` s are applied inside `dedup_survivors()`. The
session keeps the survivors (`keep`), which clears the undo stack and deletes the stored trains
no MU refers to any more. The change carries `kept` and `removed_count`.

---

## 6. Flag, Reset, Duplicate, Undo

- **`flag`** sets the MU's flag (`flag` omitted = true). Flagged MUs are dropped on save unless
  `remove_flagged` is false, and are left out of a refit's peel-off.
- **`reset`** brings the MU back to the file's discharge times and train, and clears its
  artifacts and flag.
- **`duplicate`** appends a copy of the MU on the same grid, with a new uid `g<grid>_mu<n>` above
  every uid the history has named on that grid. Its history entry names the `source_mu_uid`.
- **`undo`** takes back the last operation (see [The Edit Session](#the-edit-session-editingsessionpy)).

---

## 7. Opening a Decomposition (`open_edit_session`)

```python
def open_edit_session(filepath: str, session: str = DEFAULT_SESSION) -> Response
```

1. Refuse anything but an existing `.npz` or `.mat` (400 on `path`)
2. `load_decomposition(filepath, store, binary_trains=False)` reads the file once into a new edit
   `SessionStore`: the `LoadedDecomposition` and its `EditSignalContext`
3. `_file_extras` enriches it from the files around it. BIDS `channels.tsv` sets grid names,
   muscles and fsamp; the participant and hardware sidecars go into the session's `meta`. The
   `.json` edit log saved next to the file sets `mu_uids`, the history and the artifact times.
4. The pulse trains go to the session as float32 outside the heap (mapped from the file, or copied
   into the store); a file without them gets trains drawn from the discharge times
5. The session is cached for the tab (`store_edit_session`), in place of the tab's previous one,
   which closes only now: a file that fails to open leaves it open. It is offered the unsaved edits an
   earlier session left for this file (`find_recoverable`), and given its own `EditLog`
6. The response is the MUB1 state frame (see [02-api-surface.md](02-api-surface.md#editing-router-routeseditingpy))

---

## 8. Recovering Unsaved Edits (`editing/edit_log.py`)

Each session appends every operation it applies to `<cache dir>/edit-logs/<file key>-<token>.jsonl`:
a header naming the file (resolved path, size, mtime), then one `{"op", "args", "t"}` line per
operation, flushed at once, so the log survives a crash of the app.

- A session that closes with unsaved edits (`log.net > 0`: operations left after the undos)
  keeps its log; otherwise the log is deleted. A save starts a new log.
- Opening the same file later finds the newest such log that no open session is writing
  (`find_recoverable`). Logs written against another version of the file are deleted; those
  without net edits are left to `purge_old_logs`, since another tab may be opening one.
- A log that cannot be written (a full disk) is closed, keeping the lines it holds if they have
  net edits; the session goes on without one, so its later edits are not recoverable.
  `recoverable_edits` in the state frame counts its net edits.
- `/edit/session/recover` with `apply: true` replays them (`EditSession.replay`): an operation the
  session refuses is skipped, and any other failure stops the replay there. Either way, the old
  log is then deleted.
- Logs older than 30 days are deleted at startup (`purge_old_logs`).

---

## 9. Saving

Both saves build a `_SaveRequest` and run `editing_service._save()`:

1. Remove flagged MUs unless `remove_flagged=False`; append a `remove_flagged` entry (`on_save: true`) naming the dropped uids
2. Deduplicate unless `remove_duplicates=False` via `_dedup()`; append a `remove_duplicates` entry (`on_save: true`) if any were dropped
3. Resolve the BIDS root (below) and the entity label (`_save_target`); a label without `sub-<label>`, or one whose subject differs from the opened file's while saving back into the file's own dataset, is a 400 on `entity_label`
4. Write `<root>/derivatives/muedit/sub-X[/ses-Y]/decomp/<entity>_edited.npz`, schema v2, and its `.json` edit log as a pair (`_write_decomposition`): both are written to temporary files beside the destination, then renamed over the previous save, so a failure leaves the previous pair whole. Pulse trains are read a few rows at a time as the file is written (`RowSource`), never copied whole; a save without trains stores spike times only, and the loader draws binary trains from them. Besides the core keys the file carries the SIL (when every kept MU has one: SIL follows the uid, so an MU made by `duplicate` has none), the `rois`, the `target_segments`, and `loader_meta` with the form's fields over it (`form_over_loader_meta`). It does not embed the EMG: the BIDS dataset holds it
5. Write `participants.tsv` via `write_participant()`; only the fields given replace a listed participant's
6. Export the raw EMG the file embeds as BIDS EMG via `_export_bids_emg()` (skipped for a `prefiltered` context, which holds no raw EMG); its fields come from `io.bids.bids_emg_fields`, as the run's export's do. The `events.tsv` is exported on its own: its failure leaves the EMG export standing
7. Export BIDS MU derivatives via `export_bids_mu_derivatives()`, the unit groups read from the raw `channels.tsv` (`read_channel_groups`), else the loader's grid labels

Steps 1–4 fail the save. Steps 5–7 are best effort: a field BIDS refuses or a failed EMG export is
`bids_emg_error`, any other failure a line of `bids_warnings`; the page shows both.

Returns `{saved, path, kept_indices, mu_uids, edit_history, bids_emg_paths?, bids_emg_error?, bids_deriv_paths?, bids_warnings?}`.
`kept_indices` indexes the saved MUs in the request's order.

**`save_edit_session`** saves the session's state: its spikes, flags, uids, history, artifacts,
train rows (`EditSession.pulse_rows`) and signal context. On Windows the file being replaced may be
memory-mapped by the session, so `detach` first copies what is mapped from it into the store.
A form that names no muscle saves the file's (`meta["muscle_names"]`). Afterwards the saved MUs become
the session's baseline (`EditSession.saved`), the session's `file_label` and `source_path` name
the saved file, and the undo stack and log start over. The new log is keyed to the saved file, so
reopening that file is what offers unsaved edits back after a crash.

**`save_edits`** is the run save, from the run stage. It takes the run held under
`run_result_token`: its discharge times and pulse trains, and the SIL, windows and target segments
the run kept with them (`RunExtras`). It generates the uids and writes an empty history. The
artifact mask comes from the run's `artifact_regions` (`build_manual_artifact_mask`). A token the
server no longer holds is a 400. The request model refuses a non-positive `fsamp`, negative grid
indices, a `powerline_freq` outside `(0, 1000]` Hz and an age that is not a
whole number of years (422).

---

## Service Helpers (in `services/editing_service.py`)

| Function | Description |
|---|---|
| `_expected_grid_count(decomp: LoadedDecomposition)` | Grid count implied by names, muscles and MU-to-grid indices |
| `_pad_grid_names(names, expected_count, fallback)` | Pads grid names to the expected count |
| `_coerce_dup_tol(raw, default=0.3)` | Coerces `duplicatesthresh` to float |
| `_coerce_bool_param(raw)` | Coerces `duplicatesbgrids` (MATLAB 0/1, nested arrays, strings) to bool |

---

## BIDS Files of a Session

`api.common.dataset_root_of(filepath)` finds the dataset an opened file sits in
(`io.bids.dataset_root`, else the default project folder above it). Folders come from
`io.bids.BidsEntities`; a save whose `entity_label` has no `sub-<label>` is a 400 on
`entity_label`. The session form's participant and hardware fields come from
`io.bids.read_sidecar_meta`, and the grid models and muscles from `read_channels_summary`: when the
recording's `channels.tsv` names them, they replace the `.npz`'s at open, as does its sampling rate.

The edit session keeps the BIDS root it inferred from the opened file (`EditSession.bids_root`)
and the project that root lies in under `DATA_ROOT` (`meta["project"]`, `""` outside it).
`editing_service._dataset_root()` resolves where a save writes and where the grid EMG for a
filter update is read: the file's own dataset while the request's `project` equals the one the
file opened with, else `resolve_bids_root(project)`. A decomposition opened from a dataset
outside the output folder therefore saves back into it; typing a project sends it there instead.
