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

### Type Aliases

```python
SpikeTimes: TypeAlias = list[int]
FilterUpdateResult: TypeAlias = tuple[np.ndarray | None, SpikeTimes]
```

---

## Operation Catalog

Every operation is `POST /edit/ops/{op}` with an `EditOpPayload`, dispatched by
`EditSession.apply(op, args)`. Each one records an undo step and, except `undo` itself, a history
entry of the given type.

| `op` | Arguments | Changes | History entry | Core function |
|---|---|---|---|---|
| `update-filter` | `mu, view_start, view_end, use_peeloff, lock_spikes, project, nbextchan, peel_off_win` | pulse train in view + spikes | `update_filter` | `update_motor_unit_filter_window()` |
| `add-spikes` | `mu, x_start, x_end, y_min` | spikes | `add_spikes` | `add_spikes_in_roi()` |
| `add-artifact` | `mu, x_start, x_end, y_min` | artifacts | `add_artifact` | `add_artifact_in_roi()` |
| `delete-spikes` | `mu, x_start, x_end, y_min, y_max` | spikes + artifacts | `delete_spikes`, `delete_artifact` | `delete_spikes_in_roi()` + `delete_artifacts_in_roi()` |
| `delete-dr` | `mu, x_start, x_end, y_min` (Hz) | spikes | `delete_dr` | `delete_high_discharge_rate_spikes_in_roi()` |
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
                 artifacts=None, history=None, signal=None, bids_grid=None, duplicates=None)
    def apply(self, op: str, args: dict) -> Change
    def replay(self, records: list[dict]) -> int
```

| State | Description |
|---|---|
| `spikes`, `artifacts` | Sorted int32 sample arrays, one per MU |
| `flagged`, `mu_grid_index`, `mu_uids` | Per-MU fields |
| `rows` | Each MU's pulse train as `(key, row)` into `arrays`, or `None`: the file has none, and a binary train is drawn from the discharge times |
| `owned` | Whether the MU's row is a copy only it uses (and may be written in place) |
| `original_spikes`, `original_rows` | The file's state: `dirty` compares against it, `reset` returns to it |
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
its previous row, and cuts the history to where it was. `remove-duplicates` and a save clear the
undo stack.

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
    emg: np.ndarray,              # (n_channels, n_samples) EMG
    emg_mask: np.ndarray,         # (n_channels,) bad-channel mask (nonzero = discard)
    spike_times: SpikeTimes,      # existing spike times for this MU
    fsamp: float,
    start: int,                   # window start sample
    end: int,                     # window end sample
    nbextchan: int = DEFAULT_NBEXTCHAN,    # target extended channels (1000)
    peeloff_spike_times: list[SpikeTimes] | None = None,  # other MUs' spikes for peel-off
    peeloff_win: float = DEFAULT_PEEL_OFF_WIN_SEC,       # 0.025 s
    emg_offset: int = 0,          # offset of emg array within full signal
    use_peeloff: bool = False,    # enable peel-off of other MUs
    artifact_times: SpikeTimes | None = None,  # artifact spike times to subtract
    lock_spikes: bool = False,    # snap existing spikes to nearest peak
    artifact_mask: np.ndarray | None = None,  # (n_samples,) artifact region mask
    bandpass: bool = True,        # False for EMG that is already filtered
    compute_dtype: ComputeDtype = DEFAULT_COMPUTE_DTYPE,  # float32 by default
) -> FilterUpdateResult  # (pulse_train | None, updated_spike_times)
```

### Internal pipeline (`_recompute_spikes_in_window`)

```
1. Slice EMG to [start, end)
2. Bandpass filter (bandpass_signals), unless bandpass=False (the edit session passes filtered EMG)
3. Delay-embed (extend_signal) to nbextchan extended channels
4. PCA on the columns spikes are taken from: [edge, L - edge), past the zero-padded
   extension ends and the bandpass transients, minus artifact columns (pca_extended_signal)
5. Whiten (whiten_extended_signal)
6. Optional peel-off: subtract other MUs' waveforms (subtract_mu_waveforms)
   + subtract artifacts
7. Build MU filter from existing spikes in window
8. Project to get pulse train
9. Signed-square nonlinearity (signed_square)
10. Zero artifact regions
11. Refractory peak detection (find_refractory_peaks)
12. K-means amplitude split (split_by_amplitude) -> high/low clusters
13. Drop spikes inside artifact regions
14. Optional lock_spikes: snap existing spikes to nearest peak (+/- 10 samples),
    then merge with new detections
15. Amplitude ceiling: pt[spike] <= 3 * centroid_high
16. Merge with spikes outside the window
17. Return (pulse_train | None, updated_spike_times)
```

### In the session (`EditSession.update_filter`)

| Argument | Controls | Default |
|---|---|---|
| `view_start` / `view_end` | Window boundaries (required; 400 when empty or past the EMG) | — |
| `nbextchan` | Extended channel count | `1000` |
| `peel_off_win` | Peel-off window half-width (s) | `0.025` |
| `use_peeloff` | Peel off the other unflagged MUs of the same grid | `False` |
| `lock_spikes` | Snap existing spikes to nearest peaks | `False` |
| `project` | Where to read the BIDS EMG (see `_dataset_root` below) | `None` |

The MU's own artifacts are subtracted, and the file's temporal artifact mask (from the signal
context) excludes contaminated samples.

The refit runs on the grid's EMG filtered as the decomposition filtered it: notch, then the grid's
own bandpass (`signal.filters.emg_filter_inplace`), over the whole recording, since the FFT notch
cannot be applied to a view. The filtered grid is built once, in the session store, and reused
(`EditSession._grid_emg`). The page asks for it ahead of the first refit
(`/edit/session/prepare-grid` → `EditSession.prepare_grid`) when a file opens, for the grid on
screen, and when the user picks a grid; it runs outside the session lock, so edits go on, and a
refit sent meanwhile waits for the same filtering under the grid's own lock. The refit then runs
with `bandpass=False`. The raw
EMG comes from:
- **BIDS**: the whole grid read channel by channel into the store (`_read_bids_grid` →
  `io.bids.read_bids_emg_grid`)
- **the decomposition file**: its embedded EMG (a v2 `.npz` member mapped in place, or `.mat`);
  a `prefiltered` context (schema v1 `.npz`) went through these filters before it was saved and
  is used as is

With neither, the refit is refused (400). The new train is written into the MU's own copy over
`[view_start + edge, view_end − edge)`, `edge` = 0.1 s, where the bandpass transients have died
down.

---

## 2. Add Spikes in ROI (`add_spikes_in_roi`)

```python
def add_spikes_in_roi(
    pulse: np.ndarray,        # pulse train for this MU
    spike_times: SpikeTimes,  # existing spike times
    fsamp: float,
    x_start: int,            # ROI start sample
    x_end: int,              # ROI end sample
    y_min: float,            # minimum pulse amplitude threshold
) -> SpikeTimes
```
Zeros everything outside the ROI `[x_start, x_end]`, finds refractory peaks above `y_min` amplitude threshold, unions with existing spikes. Returns sorted unique spike list.

The session passes the ROI's window of the train plus one sample on each side (so peak picking
matches the whole train) and adds the peaks it finds. `y_min` omitted means no peak qualifies.

---

## 3. Add Artifact in ROI (`add_artifact_in_roi`)

```python
def add_artifact_in_roi(
    pulse: np.ndarray,
    artifact_times: SpikeTimes,  # existing artifact times
    fsamp: float,
    x_start: int,
    x_end: int,
    y_min: float,
) -> SpikeTimes
```
Mirror of `add_spikes_in_roi` but operates on `artifact_times` instead of `spike_times`. Same ROI logic: zeros outside ROI, finds peaks above `y_min`, unions with existing artifacts.

---

## 4. Delete Spikes in ROI (`delete_spikes_in_roi`)

```python
def delete_spikes_in_roi(
    pulse: np.ndarray,
    spike_times: SpikeTimes,
    x_start: int,
    x_end: int,
    y_min: float,
    y_max: float,
) -> SpikeTimes
```
Deletes spikes inside the ROI `[x_start, x_end]` whose pulse amplitude falls within `[min(y_min, y_max), max(y_min, y_max) + 1]`. Spikes outside the ROI or outside the amplitude band are kept.

`EditSession.delete_spikes` also removes the MU's artifacts in the same box
(`delete_artifacts_in_roi`), with a `delete_artifact` history entry when any went.

---

## 5. Delete Artifacts in ROI (`delete_artifacts_in_roi`)

```python
def delete_artifacts_in_roi(
    pulse: np.ndarray,
    artifact_times: SpikeTimes,
    x_start: int,
    x_end: int,
    y_min: float,
    y_max: float,
) -> SpikeTimes
```
Same logic as `delete_spikes_in_roi` but for `artifact_times`.

---

## 6. Delete High Discharge-Rate Spikes in ROI (`delete_high_discharge_rate_spikes_in_roi`)

```python
def delete_high_discharge_rate_spikes_in_roi(
    pulse: PulseValues,
    spike_times: SpikeTimes,
    fsamp: float,
    x_start: int,
    x_end: int,
    y_min: float,    # max discharge rate (Hz)
) -> SpikeTimes
```
For each consecutive spike pair whose midpoint falls in the ROI and whose discharge rate (`fsamp / ISI`) exceeds `y_min`, deletes the lower-amplitude spike of the pair. Returns the pruned spike list.

`PulseValues` is anything indexable by sample: the session passes `_PulseLookup`, which reads
single samples of the stored train instead of loading it.

---

## 7. Remove Discharge-Rate Outliers (`remove_discharge_rate_outliers`)

```python
def remove_discharge_rate_outliers(
    pulse: PulseValues,
    spike_times: SpikeTimes,
    fsamp: float,
    z_factor: float = 3.0,
) -> SpikeTimes
```
Global discharge-rate outlier removal (no ROI). Computes per-pair discharge rates, sets threshold = `mean + z_factor * std`, and for each pair exceeding the threshold deletes the lower-amplitude spike. Requires >= 3 spikes. Returns the pruned list; the change carries `removed_count`.

`z_factor` stays at `3.0`; the API does not expose it.

---

## 8. Remove Duplicates

`EditSession.remove_duplicates` asks the `duplicates` callback the service gave it, which runs
`editing_service._dedup()`: the same duplicate removal as the decomposition pipeline, so
interactive and save-time dedup match what `postprocess_step` produced.

```python
def _dedup(distimes, mu_grid_index, parameters, fsamp, total_samples) -> list[int]  # kept indices, ascending
```

It calls `decomp.postprocess.dedup_survivors()` (within each grid, then across grids) on the
discharge times, with a `DecompositionParameters` carrying:
- `duplicatesthresh` from the file's `parameters` via `_coerce_dup_tol()` (default `0.3`)
- `duplicatesbgrids` from `parameters` via `_coerce_bool_param()` (default `True`; accepts MATLAB 0/1, nested arrays, strings)

`maxlag = fsamp / 40` and `jitter = 0.00025` s are applied inside `dedup_survivors()`. The
session keeps the survivors (`keep`), which clears the undo stack and deletes the stored trains
no MU refers to any more. The change carries `kept` and `removed_count`.

---

## 9. Flag, Reset, Duplicate, Undo

- **`flag`** sets the MU's flag (`flag` omitted = true). Flagged MUs are dropped on save unless
  `remove_flagged` is false, and are left out of a refit's peel-off.
- **`reset`** brings the MU back to the file's discharge times and train, and clears its
  artifacts and flag.
- **`duplicate`** appends a copy of the MU on the same grid, with a new uid `g<grid>_mu<n>` above
  every uid the history has named on that grid. Its history entry names the `source_mu_uid`.
- **`undo`** takes back the last operation (see [The Edit Session](#the-edit-session-editingsessionpy)).

---

## 10. Opening a Decomposition (`open_edit_session`)

```python
def open_edit_session(filepath: str, session: str = DEFAULT_SESSION) -> Response
```

1. Refuse anything but an existing `.npz` or `.mat` (400 on `path`)
2. Close the tab's previous edit session (`_release_edit_sessions`)
3. `load_decomposition(filepath, store, binary_trains=False)` reads the file once into a new edit
   `SessionStore`: the `LoadedDecomposition` and its `EditSignalContext`
4. `_file_extras` enriches it from the files around it. BIDS `channels.tsv` sets grid names,
   muscles and fsamp; the participant and hardware sidecars go into the session's `meta`. The
   `.json` edit log saved next to the file sets `mu_uids`, the history and the artifact times.
5. The pulse trains go to the session as float32 outside the heap (mapped from the file, or copied
   into the store); a file without them gets trains drawn from the discharge times
6. The session is cached for the tab (`_store_edit_session`), offered the unsaved edits an
   earlier session left for this file (`find_recoverable`), and given its own `EditLog`
7. The response is the MUB1 state frame (see [02-api-surface.md](02-api-surface.md#editing-router-routeseditingpy))

---

## 11. Recovering Unsaved Edits (`editing/edit_log.py`)

Each session appends every operation it applies to `<cache dir>/edit-logs/<file key>-<token>.jsonl`:
a header naming the file (resolved path, size, mtime), then one `{"op", "args", "t"}` line per
operation, flushed at once, so the log survives a crash of the app.

- A session that closes with unsaved edits (`log.net > 0`: operations left after the undos)
  keeps its log; otherwise the log is deleted. A save starts a new log.
- Opening the same file later finds the newest such log that no open session is writing
  (`find_recoverable`). Logs written against another version of the file are deleted.
  `recoverable_edits` in the state frame counts its net edits.
- `/edit/session/recover` with `apply: true` replays them (`EditSession.replay`): an operation the
  session refuses is skipped, and any other failure stops the replay there. Either way, the old
  log is then deleted.
- Logs older than 30 days are deleted at startup (`purge_old_logs`).

---

## 12. Saving

Both saves build a `_SaveRequest` and run `editing_service._save()`:

1. Remove flagged MUs unless `remove_flagged=False`; append a `remove_flagged` entry (`on_save: true`) naming the dropped uids
2. Deduplicate unless `remove_duplicates=False` via `_dedup()`; append a `remove_duplicates` entry (`on_save: true`) if any were dropped
3. Resolve the BIDS root (below) and write `<root>/derivatives/muedit/sub-X[/ses-Y]/decomp/<entity>_edited.npz`, schema v2, via `save_decomposition_npz()`. Pulse trains are read a few rows at a time as the file is written (`RowSource`), never copied whole; a save without trains stores spike times only, and the loader draws binary trains from them
4. Write the `.json` edit log next to it via `save_editlog()` (`mu_uids`, `history`, `artifact_times`)
5. Write `participants.tsv` via `write_bids_dataset_description()`
6. Export BIDS MU derivatives via `export_bids_mu_derivatives()` (best effort)
7. Export the raw EMG the file embeds as BIDS EMG via `_export_bids_emg()` (best effort; skipped for a `prefiltered` context, which holds no raw EMG)

Returns `{saved, path, kept_indices, mu_uids, edit_history, bids_emg_paths?, bids_deriv_paths?}`.
`kept_indices` indexes the saved MUs in the request's order.

**`save_edit_session`** saves the session's state: its spikes, flags, uids, history, artifacts,
train rows (`EditSession.pulse_rows`) and signal context. On Windows the file being replaced may be
memory-mapped by the session, so `detach` first copies what is mapped from it into the store.
Afterwards the saved MUs become the session's baseline (`EditSession.saved`), and the undo stack
and log start over.

**`save_edits`** is the run save, from the run stage. It takes the run held under
`run_result_token`: its discharge times (unless the request sends `distimes`) and its pulse
trains. It generates the uids and writes an empty history. The artifact mask comes from the run's
`artifact_regions` (`build_manual_artifact_mask`). A token the server no longer holds, with no
discharge times sent, is a 400.

---

## Service Helpers (`services/edit_helpers.py`)

| Function | Description |
|---|---|
| `_expected_grid_count(decomp: LoadedDecomposition)` | Grid count implied by names, muscles and MU-to-grid indices |
| `_pad_grid_names(names, expected_count, fallback)` | Pads/truncates grid names to expected count |
| `_normalize_muscle_names(raw)` | Normalizes muscle-name payload to clean list of non-empty strings |
| `_normalize_flagged(raw, nmu)` | Coerces flagged list to `nmu` length, padding with `False` |
| `_generate_mu_uids(mu_grid_index)` | Generates per-grid MU UIDs like `"g0_mu0"`, `"g0_mu1"`, `"g1_mu0"` |
| `_normalize_mu_grid_index(raw, nmu)` | Coerces mu_grid_index to `nmu` length, padding with 0 |
| `_coerce_dup_tol(raw, default=0.3)` | Coerces `duplicatesthresh` to float |
| `_coerce_bool_param(raw)` | Coerces `duplicatesbgrids` (MATLAB 0/1, nested arrays, strings) to bool |

---

## Service Helpers (`services/bids_helpers.py`)

| Function | Description |
|---|---|
| `_read_bids_grid(bids_root, entity_label, grid_index, store)` | Reads a BIDS EMG grid over the whole recording into `store` (float32, writable); returns `(emg, fsamp, emg_mask)` |
| `_parse_all_bids_entities(entity_label)` | Extracts BIDS key-value pairs (sub, ses, task, acq, run, recording) |
| `_parse_subject_session_from_entity_label(entity_label)` | Extracts subject + optional session |
| `_infer_bids_root_from_decomp_path(filepath)` | Infers BIDS root from decomposition file path (derivatives/muedit/, sub-X/, muedit_out) |
| `read_bids_sidecar_meta(bids_root, entity_label)` | Reads participant + hardware metadata from BIDS sidecars |

The edit session keeps the BIDS root it inferred from the opened file (`EditSession.bids_root`)
and the project that root lies in under `DATA_ROOT` (`meta["project"]`, `""` outside it).
`editing_service._dataset_root()` resolves where a save writes and where the grid EMG for a
filter update is read: the file's own dataset while the request's `project` equals the one the
file opened with, else `resolve_bids_root(project)`. A decomposition opened from a dataset
outside the output folder therefore saves back into it; typing a project sends it there instead.
