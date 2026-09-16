# 05 — Editing Operations

The editing module is the most complex part of MUedit2. It provides interactive motor-unit spike editing: filter recomputation, spike add/delete, artifact marking, discharge-rate pruning, outlier removal, and deduplication. All operations are pure functions that return updated spike/artifact lists; persistence is handled by the API service layer.

---

## Architecture

```
Frontend (edit-stage.js)
  ↓ API call
API Route (routes/editing.py)
  ↓
Editing Service (services/editing_service.py)
  ↓
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

| Operation | API Endpoint | Target | ROI? | Amplitude Band? | Service Function | Core Function |
|---|---|---|---|---|---|---|
| Filter update | `/edit/update-filter` | pulse train + spikes | yes (window) | k-means split | `update_filter()` | `update_motor_unit_filter_window()` |
| Add spikes | `/edit/add-spikes` | spikes | yes | `>= y_min` | `add_spikes()` | `add_spikes_in_roi()` |
| Add artifact | `/edit/add-artifact` | artifacts | yes | `>= y_min` | `add_artifact()` | `add_artifact_in_roi()` |
| Delete spikes | `/edit/delete-spikes` | spikes + artifacts | yes | `[y_min, y_max]` | `delete_spikes()` | `delete_spikes_in_roi()` + `delete_artifacts_in_roi()` |
| Delete high DR | `/edit/delete-dr` | spikes | yes | rate > `y_min` | `delete_dr()` | `delete_high_discharge_rate_spikes_in_roi()` |
| Remove outliers | `/edit/remove-outliers` | spikes | no | rate > mean + z*sigma | `remove_outliers()` | `remove_discharge_rate_outliers()` |
| Remove duplicates | `/edit/remove-duplicates` | all MUs | no | lag overlap | `remove_duplicates_service()` | `_dedup()` -> `rem_duplicates()` |
| Flag MU | `/edit/flag-mu` | metadata | no | — | `flag_mu()` | — |
| Load decomposition | `/edit/load-by-path` | — | no | — | `load_decomposition_from_path()` | `load_decomposition_file()` |
| Save edits | `/edit/save` | — | no | — | `save_edits()` | NPZ save + BIDS export |

---

## 1. Filter Update (`update_motor_unit_filter_window`)

The most complex operation. Recomputes a motor unit's pulse train and spike train within a visible time window by re-running the full ICA pipeline on the windowed EMG segment.

```python
def update_motor_unit_filter_window(
    emg: np.ndarray,              # (n_channels, n_samples) raw EMG
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
) -> FilterUpdateResult  # (pulse_train | None, updated_spike_times)
```

### Internal pipeline (`_recompute_spikes_in_window`)

```
1. Slice EMG to [start, end)
2. Bandpass filter (bandpass_signals)
3. Delay-embed (extend_signal) to nbextchan extended channels
4. PCA on clean columns (artifact-mask-aware) (pca_extended_signal)
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

### User-facing parameters (from `EditFilterPayload`)

| Parameter | Controls | Default |
|---|---|---|
| `view_start` / `view_end` | Window boundaries | `0` / `0` |
| `nbextchan` | Extended channel count | `1000` |
| `peel_off_win` | Peel-off window half-width (s) | `0.025` |
| `use_peeloff` | Enable peel-off of other MUs | `False` |
| `lock_spikes` | Snap existing spikes to nearest peaks | `False` |
| `artifact_times` | Artifact spike times to subtract | `None` |
| `flagged` | Flagged MU indices (excluded from peel-off) | `None` |

### Service layer (`editing_service.py: update_filter`)

Resolves the EMG source:
- **BIDS path**: loads grid via `_load_bids_grid(bids_root, entity_label, grid_index, view_start, view_end)`
- **MAT context**: loads cached signal context via `_get_edit_signal_context(edit_signal_token)` or `_get_edit_signal_context_by_label(file_label)`

The temporal artifact mask is retrieved from the cached signal context (if available) so the filter update excludes artifact-contaminated samples.

Then calls `update_motor_unit_filter_window()` and splices the updated pulse segment back into the full pulse train.

Returns: `{fsamp, distimes, pulse_train}`

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

### User-facing parameters (from `EditRoiPayload`)

| Parameter | Description |
|---|---|
| `x_start` / `x_end` | ROI horizontal boundaries (samples) |
| `y_min` | Minimum pulse amplitude to accept a spike |

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

### Service layer (`delete_spikes`)

Also calls `delete_artifacts_in_roi()` to clean up artifacts in the same region. Returns: `{distimes, artifact_times?}`.

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
    pulse: np.ndarray,
    spike_times: SpikeTimes,
    fsamp: float,
    x_start: int,
    x_end: int,
    y_min: float,    # max discharge rate (Hz)
) -> SpikeTimes
```
For each consecutive spike pair whose midpoint falls in the ROI and whose discharge rate (`fsamp / ISI`) exceeds `y_min`, deletes the lower-amplitude spike of the pair. Returns the pruned spike list.

### User-facing parameters

| Parameter | Description |
|---|---|
| `x_start` / `x_end` | ROI horizontal boundaries |
| `y_min` | Maximum discharge rate threshold (Hz) — pairs faster than this get pruned |

---

## 7. Remove Discharge-Rate Outliers (`remove_discharge_rate_outliers`)

```python
def remove_discharge_rate_outliers(
    pulse: np.ndarray,
    spike_times: SpikeTimes,
    fsamp: float,
    z_factor: float = 3.0,
) -> SpikeTimes
```
Global discharge-rate outlier removal (no ROI). Computes per-pair discharge rates, sets threshold = `mean + z_factor * std`, and for each pair exceeding the threshold deletes the lower-amplitude spike. Requires >= 3 spikes. Returns the pruned list.

### User-facing parameters (from `EditOutliersPayload`)

| Parameter | Description |
|---|---|
| `mu_index` | Which MU to process |
| `pulse_train` | Pulse train data |
| `fsamp` | Sampling frequency |

Note: `z_factor` is hardcoded to `3.0` in the core function; the API does not expose it.

---

## 8. Remove Duplicates (`remove_duplicates_service`)

Service-level operation that calls `rem_duplicates()` from `decomp/algorithm.py` with standard parameters.

```python
# Service layer
def remove_duplicates_service(payload: EditDeduplicatePayload) -> dict[str, Any]
```
Builds pulse trains from distimes via `build_pulse_trains_from_distimes()`, then calls `_dedup()`:

```python
def _dedup(pulse_trains, distimes, dup_tol, fsamp) -> (pulse_trains, distimes, kept_indices)
```
Which calls `rem_duplicates(pulse_t, distime, None, maxlag, jitter, tol, fsamp)` with:
- `maxlag = fsamp / 40`
- `jitter = 0.00025` seconds
- `tol = dup_tol` (from `duplicatesthresh` parameter, default `0.3`)

Returns: `{kept_indices, distimes, removed_count}`

---

## 9. Flag MU (`flag_mu`)

```python
def flag_mu(payload: EditFlagPayload) -> dict[str, Any]
```
Validates MU index and returns requested flag status without mutating spike times. Returns: `{flagged: bool}`. This is a metadata operation; the flag is used during save to optionally remove flagged MUs.

---

## 10. Save Edits (`save_edits`)

```python
def save_edits(payload: EditSavePayload) -> dict[str, Any]
```

Full save pipeline:

1. Normalize distimes via `normalize_distimes()`
2. Normalize muscle names via `_normalize_muscle_names()`
3. Normalize grid names via `_pad_grid_names()`
4. Generate MU UIDs via `_generate_mu_uids()`
5. Optional: remove flagged MUs if `remove_flagged=True`
6. Optional: deduplicate if `remove_duplicates=True` via `_dedup()`
7. Build pulse trains from distimes via `build_pulse_trains_from_distimes()`
8. Build artifact mask from `payload.artifact_regions` via `build_manual_artifact_mask()`; fall back to cached signal context mask if no manual regions
9. Save NPZ via `decomposition_file.save_decomposition_npz()` (includes `artifact_mask` extra) to BIDS derivatives layout
10. Write editlog JSON via `save_editlog()` (mu_uids, edit_history, artifact_times)
11. Write participants.tsv via `write_bids_dataset_description()`
12. Export BIDS MU derivatives via `export_bids_mu_derivatives()`
13. Best-effort BIDS EMG export from MAT context via `_export_bids_from_mat_context()`

Returns: `{saved: bool, path: str, bids_emg_paths?: dict, bids_deriv_paths?: dict}`

---

## 11. Load Decomposition (`load_decomposition_from_path`)

```python
def load_decomposition_from_path(filepath: str) -> dict[str, Any]
```

Load pipeline:
1. Use the server-side path directly (there is no upload variant)
2. Call `_init_loaded_decomp(filepath, file_label)`:
   - `load_decomposition_file(filepath)` -> normalized dict
   - `load_decomposition_signal_context(filepath)` -> raw EMG context
   - Store signal context in cache via `_store_edit_signal_context()` -> `edit_signal_token`
3. Enrich with BIDS sidecar metadata (grid names, muscles, fsamp from channels.tsv; participant + hardware metadata from sidecars; editlog JSON with mu_uids, edit_history, artifact_times)
4. Return JSON-safe dict via `make_json_safe()`

Binary variant (`load_decomposition_binary_from_path`): encodes as MELD f32 binary if 2-D `pulse_trains_full` exists, falls back to JSON.

---

## Service Helpers (`services/edit_helpers.py`)

| Function | Description |
|---|---|
| `_expected_grid_count(loaded)` | Derives expected grid count from loaded decomposition |
| `_pad_grid_names(names, expected_count, fallback)` | Pads/truncates grid names to expected count |
| `_normalize_muscle_names(raw)` | Normalizes muscle-name payload to clean list of non-empty strings |
| `_normalize_flagged(raw, nmu)` | Coerces flagged list to `nmu` length, padding with `False` |
| `_generate_mu_uids(mu_grid_index)` | Generates per-grid MU UIDs like `"g0_mu0"`, `"g0_mu1"`, `"g1_mu0"` |
| `_normalize_mu_grid_index(raw, nmu)` | Coerces mu_grid_index to `nmu` length, padding with 0 |
| `_coerce_dup_tol(raw, default=0.3)` | Coerces `duplicatesthresh` to float |

---

## Service Helpers (`services/bids_helpers.py`)

| Function | Description |
|---|---|
| `_load_bids_grid(bids_root, entity_label, grid_index, view_start, view_end)` | Loads BIDS EMG grid for a specific sample window; returns `(emg, fsamp, emg_mask)` |
| `_parse_all_bids_entities(entity_label)` | Extracts BIDS key-value pairs (sub, ses, task, acq, run, recording) |
| `_parse_subject_session_from_entity_label(entity_label)` | Extracts subject + optional session |
| `_infer_bids_root_from_decomp_path(filepath)` | Infers BIDS root from decomposition file path (derivatives/muedit/, sub-X/, muedit_out) |
| `read_bids_sidecar_meta(bids_root, entity_label)` | Reads participant + hardware metadata from BIDS sidecars |
