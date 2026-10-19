# 03 — Decomposition Engine

The decomposition engine transforms raw HD-EMG signals into individual motor-unit (MU) spike trains using convolutive FastICA source separation, followed by optional adaptive online post-processing.

---

## Pipeline Overview

```
run_decomposition(filepath, ...)
  │
  ├─ 1. load_step()           → LoadStepOutput
  ├─ 2. preprocess_step()     → PreprocessStepOutput
  ├─ 2b. bids_export_step()   → PreprocessStepOutput (only with bids_root)
  ├─ 3. decompose_step()      → DecomposeStepOutput
  ├─ 4. postprocess_step()    → PostprocessStepOutput
  └─ 5. export_step()         → DecompositionResult
```

### `run_decomposition()` — Full signature

```python
def run_decomposition(
    filepath: str,
    duration: float | None = None,
    manual_roi: bool = False,
    params: DecompositionParameters | None = None,
    save_npz: bool = True,
    progress_cb: Callable[[str, dict[str, Any]], None] | None = None,
    roi: tuple[int, int] | None = None,
    rois: list[tuple[int, int]] | None = None,
    discard_overrides: list[list[int]] | None = None,
    bids_root: str | None = None,
    bids_entities: dict[str, Any] | None = None,
    bids_metadata: dict[str, Any] | None = None,
    file_label: str | None = None,
    include_full_preview: bool = False,
    preloaded_signal: SignalImport | None = None,
    artifact_regions: list[tuple[int, int]] | None = None,
    store: ArrayStore | None = None,
    target_index: int | None = None,
) -> DecompositionResult
```

### `DecompositionResult` (types.py)

| Field | Type | Description |
|---|---|---|
| `fsamp` | `float` | Sampling frequency |
| `grid_names` | `list[str]` | One per grid |
| `pulse_trains` | `FloatArray` | `(n_mu, n_samples)` |
| `spike_times` | `list[IntArray]` | Sorted sample indices per MU |
| `mu_grid_index` | `list[int]` | Grid of each MU |
| `sil` | `list[float]` | SIL of each MU |
| `discard_channels` | `list[IntArray]` | 0/1 per channel, per grid (auto-QC bad channels included) |
| `parameters` | `dict[str, Any]` | `asdict(DecompositionParameters)` |
| `preview` | `dict[str, Any]` | `build_preview_payload()`: what the run page draws |
| `save_path` | `str \| None` | The `.npz` written, when `save_npz` |

---

## `DecompositionParameters` (types.py)

Algorithm hyper-parameters for a single decomposition run.

| Field | Type | Default | Description |
|---|---|---|---|
| `niter` | `int` | `150` | Max ICA iterations per window |
| `nwindows` | `int` | `1` | Number of analysis sub-windows per grid |
| `random_init` | `bool` | `False` | Seed each filter randomly; False seeds it from the most active unconsumed sample |
| `random_seed` | `int` | `0` | RNG seed |
| `peel_off_enabled` | `bool` | `False` | Enable MU waveform subtraction (peel-off) |
| `covfilter` | `bool` | `False` | Filter MUs by ISI coefficient-of-variation |
| `duplicatesbgrids` | `bool` | `True` | Remove duplicate MUs across grids |
| `nbextchan` | `int` | `1000` | Target number of extended channels |
| `edges_sec` | `float` | `0.2` | Edge-trim duration in seconds |
| `contrast_func` | `str` | `"skew"` | FastICA contrast (`"skew"`, `"kurtosis"`, `"logcosh"`) |
| `sil_thr` | `float` | `0.9` | Silhouette threshold for accepting MUs |
| `cov_thr` | `float` | `0.5` | ISI CoV threshold (if `covfilter`) |
| `peel_off_win` | `float` | `0.025` | Peel-off window half-width in seconds |
| `duplicatesthresh` | `float` | `0.3` | Duplicate overlap score threshold |
| `use_adaptive` | `bool` | `False` | Use adaptive batch post-processing |
| `adapt_batch_ms` | `int` | `100` | Adaptive batch duration in ms |
| `adapt_wh` | `bool` | `True` | Adapt whitening matrix online |
| `adapt_sv` | `bool` | `True` | Adapt separation vectors online |
| `adapt_sd` | `bool` | `True` | Adapt spike detection centroids online |
| `adapt_wh_learning_rate` | `float` | `7e-3` | Whitening adaptation learning rate |
| `adapt_sv_learning_rate` | `float` | `3e-3` | Separation vector adaptation learning rate |
| `adapt_cov_alpha` | `float` | `0.1` | EMA coefficient for whitening covariance |
| `adapt_spike_prev_weight` | `int` | `5` | Weight of previous centroid in EMA update |
| `full_trace` | `bool` | `False` | Apply filters over full trace (dewhitened) |
| `auto_mask_artifacts` | `bool` | `False` | Run auto QC to detect artifacts/bad channels |

`__post_init__` raises `ValueError` unless `niter` and `nwindows` are ≥ 1, `peel_off_win`,
`adapt_batch_ms` and both learning rates are > 0, `adapt_cov_alpha` is in (0, 1],
`adapt_spike_prev_weight` is ≥ 0, and, with `use_adaptive`, `adapt_batch_ms` ≤ `edges_sec * 1000`.
The API (422) and the CLI (usage error) both reject through it.

### Post-processing mode constants

```python
POSTPROCESS_MODES = {
    "windowed":   {"use_adaptive": False, "full_trace": False},
    "full-trace": {"use_adaptive": False, "full_trace": True},
    "adaptive":   {"use_adaptive": True,  "full_trace": False},
}
```

---

## Step Output Dataclasses

### `LoadStepOutput`
| Field | Type |
|---|---|
| `full_path` | `str` |
| `signal` | `SignalImport` |

### `PreprocessStepOutput`
| Field | Type |
|---|---|
| `signal` | `SignalImport` |
| `data` | `FloatArray` |
| `fsamp` | `float` |
| `grid_names` | `list[str]` |
| `coordinates` | `list[np.ndarray]` |
| `ied` | `list[float]` |
| `discard_channels` | `list[np.ndarray]` |
| `muscles` | `list[str]` |
| `loader_meta` | `dict[str, Any]` |
| `rois` | `list[tuple[int, int]]` |
| `coordinates_plateau` | `list[int]` |
| `artifact_mask` | `np.ndarray \| None` |

### `DecomposeStepOutput`
| Field | Type |
|---|---|
| `mu_filters_by_window` | `dict[int, np.ndarray]` |
| `whiten_mat_by_window` | `dict[int, np.ndarray]` |
| `coordinates_plateau` | `list[int]` |
| `sil_by_window` | `dict[int, list[float]]` |
| `mu_grid_index` | `list[int]` |
| `win_mean_by_window` | `dict[int, np.ndarray]` |

### `PostprocessStepOutput`
| Field | Type |
|---|---|
| `pulse_trains` | `np.ndarray` |
| `spike_times` | `list[np.ndarray]` |
| `mu_grid_index` | `list[int]` |
| `sil_by_window` | `dict[int, list[float]]` |
| `sil` | `list[float]` |

---

## Stage 1: Load (`preprocess.py: load_step`)

```python
def load_step(filepath, file_label, preloaded_signal, progress_cb) -> LoadStepOutput
```
Loads input signal from disk via `load_signal(filepath)` or copies a preloaded `SignalImport` via `.clone()`.

---

## Stage 2: Preprocess (`preprocess.py: preprocess_step`)

```python
def preprocess_step(loaded, duration, manual_roi, roi, rois, params, discard_overrides,
                    artifact_regions=None, store=None, target_index=None) -> PreprocessStepOutput
```

Steps in order:

1. Copy data to `float64`
2. Resolve grid names, coordinates, IED, discard channels via `format_hdemg_signal()`
3. Validate channel count
4. Apply per-grid notch filters (`notch_inplace()`)
5. Apply per-grid bandpass filters (`bandpass_inplace()`) with grid-specific EMG type
6. Resolve ROI list (explicit `rois`, single `roi`, interactive `manual_roi`, `duration`, or full signal). If `nwindows > 1` and single ROI resolved, split into `nwindows` equal sub-windows.
7. Build `coordinates_plateau` (legacy flat format: `[s0, e0, s1, e1, ...]`)
8. If `auto_mask_artifacts`: run `run_auto_qc()` to detect artifact mask and bad channels — OR bad channels into `discard_channels` (`declared_discard_channels` keeps them as they were before). It runs on the notched and bandpassed EMG of steps 4-5, unlike `/qc/auto`, which bandpasses only (API doc, `/qc/auto`), so the two can disagree on the same recording. A grid left with no kept channel stops the run with a `ValueError`. User-drawn `artifact_regions` (if provided) are rasterized via `build_manual_artifact_mask` and OR'd into the artifact mask.

## Stage 2b: BIDS export (`preprocess.py: bids_export_step`)

```python
def bids_export_step(loaded, prep, bids_root, bids_entities, bids_metadata) -> PreprocessStepOutput
```

Runs only with `bids_root`, after preprocessing and before any decomposition: its errors stop the
run before the costly steps, and `start_decomposition` refuses the form fields it would refuse
(`check_run_export`) with a 400 before the run starts. Writes the raw (unfiltered) EMG, its
sidecars and `events.tsv`, and the participant row. Its metadata fields come from
`io.bids.bids_emg_fields`, as the edit save's do. `channels.tsv` marks
`prep.declared_discard_channels` bad ("manually excluded") and the channels auto-QC added
("auto-detected"): the mask filter updates read back from the dataset. The signal file is
encoded beside its destination and renamed over it, so a failed export leaves none. Returns `prep` with
`bids_entity_label` and `bids_emg_path` in a new `loader_meta` (the caller's signal metadata is not
changed), which `export_step` reads to save the decomposition in the dataset's derivatives.

### Helper functions (private)

| Function | Description |
|---|---|
| `select_roi_interactively(data, fsamp) -> (start, end)` | Matplotlib ginput ROI selection |
| `_resolve_rois(data, fsamp, duration, manual_roi, roi, rois, nwindows)` | Resolves analysis ROI from various input methods |
| `_build_coordinates_plateau(ngrid, rois)` | Flattens the ROI bounds of every grid into `[s0, e0, s1, e1, ...]` |
| `_filter_grids(data, fsamp, discard_channels, emg_types)` | Per-grid notch then bandpass filtering (in place), for each grid's EMG type |
| `build_manual_artifact_mask(artifact_regions, n_samples)` | Rasterize user-drawn `(start, end)` ranges into a boolean mask |

---

## Stage 3: Decompose (`core.py: decompose_step`)

```python
def decompose_step(prep, params, rng, progress_cb) -> DecomposeStepOutput
```

Runs the full ICA decomposition loop over every grid x ROI window. For each window:

1. Demean signal
2. Delay-embed (`extend_signal()`) to `nbextchan` extended channels
3. PCA eigendecomposition (`pca_extended_signal()`) on clean columns (artifact-mask-aware)
4. Whiten (`whiten_extended_signal()`)
5. Run `niter` FastICA iterations (`fixed_point_alg()`) with:
   - Optional activity-based initialization
   - Gram-Schmidt orthogonalization against already-found MUs
   - Optional peel-off (`subtract_mu_waveforms()`) between MU extractions
6. Extract spikes (`get_spikes()`) and refine (`minimize_isi_covariance()`)
7. Compute silhouette score (`compute_silhouette()`)
8. Filter MUs by `sil_thr` (and optionally `cov_thr` if `covfilter`)

Returns per-window MU filters, whitening matrices, SIL scores, and grid assignments.

Steps 1-2 and the edge trim are `extended_window(prep, params, win_global) -> (e_sig, win_mean,
ex_factor, trim)`, which `postprocess_step` also calls to rebuild a window before whitening it with
the stored matrix. Each grid's kept rows come from `signal.grid.kept_rows(discard_channels)`.

---

## Algorithm (`algorithm.py`)

### Constants

| Constant | Value | Description |
|---|---|---|
| `FIXED_POINT_MAXITER` | `500` | Max FastICA fixed-point iterations |
| `_FIXED_POINT_TOL` | `1e-4` | Convergence tolerance |
| `KMEANS_ITER` | `10` | K-means iterations for amplitude split |
| `DEDUP_MAXLAG_RATIO` | `40` | Max lag = fsamp / this ratio |
| `DEDUP_JITTER` | `0.00025` | Jitter tolerance for dedup (seconds) |
| `DEDUP_LAG_GATE` | `0.2` | Overlap above which dedup aligns a pair at its best lag |

### Functions

```python
def pca_extended_signal(signal) -> (eigenvectors, eigenvalues_diag)
```
PCA eigendecomposition of extended signal covariance. Selects components above rank-tolerance threshold.

```python
def whiten_extended_signal(signal, eigenvectors, eigenvalues_diag) -> (whitened, whiten_mat)
```
Whitens `signal` in place (`whitened` is `signal`): `eigenvectors @ inv(sqrt(eigenvalues_diag)) @ eigenvectors.T`

```python
def fixed_point_alg(w, x, basis, maxiter, contrast_func) -> w
```
One-unit FastICA fixed-point iteration with Gram-Schmidt orthogonalization against `basis`. Supports `"skew"`, `"kurtosis"`, `"logcosh"` contrast functions. Converges when `delta < 1e-4` or `maxiter` reached.

```python
def get_spikes(w, x, fsamp) -> spikes
```
Computes pulse train, detects peaks, k-means amplitude split, removes outliers above `mean + 3*std`.

```python
def minimize_isi_covariance(w, x, cov, fsamp) -> (best_w, best_spikes, best_cov)
```
Iteratively refines separation vector by re-detecting spikes and re-summing `x[:, spikes]`, tracking the best (lowest) ISI coefficient-of-variation.

```python
def compute_silhouette(x, w, fsamp) -> sil
```
Silhouette-like separability score using k-means cluster centroids: `sil = (between - within) / max(within, between)`.

```python
def subtract_mu_waveforms(x, spikes, fsamp, win) -> ndarray
```
Subtracts averaged MU waveform (estimated from spikes within `+/- win*fsamp` samples) from the multichannel signal. This is the peel-off step.

```python
def batch_process_filters(mu_filters_by_window, whitened_window, coordinates_plateau, total_samples, fsamp,
                           grid_data, window_to_grid, whiten_mat_by_window=None,
                           win_mean_by_window=None, artifact_mask=None, store=None,
                           work_dtype=np.float64) -> (pulse_trains, spike_times)
```
Applies MU filters across all windows to reconstruct pulse trains and spike times. `whitened_window(win_global)` rebuilds a window's whitened signal. In full-trace mode (given `whiten_mat_by_window`), dewhitens filters and projects through full extended signal with mean-correction. In windowed mode, projects through per-window whitened signal. Zeros pulse trains at artifact-masked samples, then detects spikes via `find_refractory_peaks` + `split_by_amplitude`.

```python
def unique_mu_indices(spike_times, maxlag, jitter, tol, fsamp, total_samples) -> list[int]  # indices of the MUs kept
```
Removes duplicated MUs using lag-aware spike-train overlap. For each pair: expand jittered spike times, find best cross-correlation lag, compute overlap score, group duplicates if `score >= tol`. Within each group, keeps the MU with lowest ISI CoV.

---

## Stage 4: Postprocess (`postprocess.py: postprocess_step`)

```python
def postprocess_step(prep, decomposed, params, progress_cb) -> PostprocessStepOutput
```

Steps:

1. **Filter application**:
   - If `use_adaptive`: extract per-grid raw data and call `adaptive_batch_process()` with `params` + artifact mask
   - Else: call `batch_process_filters()` (full-trace dewhitened or windowed mode)
2. **Deduplication** via `remove_duplicates_by_grid()` (calls `unique_mu_indices()` with `maxlag = fsamp/40`, `jitter = 0.00025s`)
3. **SIL remapping** from deduplicated global indices
4. Return `PostprocessStepOutput`

### Private helpers

| Function | Description |
|---|---|
| `dedup_survivors(spike_times, mu_grid_index, ngrid, params, fsamp, total_samples) -> (kept, grids)` | Indices of the MUs duplicate removal keeps, within grids then across, in ascending order (grid-major for the run, whose MUs come grid by grid); works on spike times only (also the edit stage's dedup). A grid index outside `[0, ngrid)` raises `ValueError` |
| `remove_duplicates_by_grid(pulse_trains, spike_times, mu_grid_index, ngrid, params, fsamp)` | `dedup_survivors`, then the kept pulse rows indexed once |
| `get_w_sig(win_global)` (inside `postprocess_step`) | A window's whitened extended signal for filter application: `extended_window` whitened in place with the stored matrix |

---

## Stage 5: Export (`postprocess.py: export_step`)

```python
def export_step(loaded, prep, post, params, include_full_preview, save_npz, emg_data,
               progress_cb) -> DecompositionResult
```

Steps:

1. Build preview payload via `build_preview_payload()`
2. If `save_npz`: resolve the save path (`io.bids.run_decomp_path`: the MUedit derivatives of the BIDS dataset the recording belongs to, whether loaded from it or exported into it by this run, else beside the input file) and save the schema v2 NPZ with sil, sil_by_window, rois, the artifact mask and the target segments; with `emg_data` (the pipeline passes it only for a file saved outside a dataset), also the raw EMG, discard_channels, coordinates and loader_meta. A save into a dataset also writes `derivatives/muedit/dataset_description.json`
3. Return the `DecompositionResult` (with the `rois` and `target_segments` the run save writes); `save_path` is set only when the file was written

---

## Preview (`preview.py`)

```python
def build_preview_payload(data, fsamp, pulse_trains, spike_times, grid_names, rois,
                           discard_channels, coordinates, mu_grid_index, loader_meta,
                           muscles, include_full_preview) -> dict[str, Any]
```
Builds the preview of a finished run. The series drawn for the recording (overview, aux,
per-channel traces) come from the upload's `/series/*` endpoints, not from here.

| Key | Content |
|---|---|
| `pulse_trains_full` | The run's pulse trains (the stored array itself) when `include_full_preview`, else `None`. The server keeps them for `/series/pulse` and the run save; no preview encoding sends them |
| `spike_times` | Per-MU discharge times, int32 arrays (CSR in the MUB1 frame, lists in the JSON fallback) |
| `channel_means` | Per-channel mean of the filtered \|EMG\| (including discarded channels) |
| `fsamp`, `total_samples`, `rois`, `grid_names`, `mu_grid_index`, `metadata`, `muscle`, `coordinates` | Metadata |

---

## Decomposition Files (`decomp/decomposition_file.py`)

Owns the app `.npz` schema (v2, described in `docs/saved-files.md`):
`save_decomposition_npz(out_path, pulse_trains, spike_times, fsamp, grid_names, mu_grid_index,
muscles, parameters, total_samples, *, sil, sil_by_window, rois,
artifact_mask, emg_data, discard_channels, coordinates, loader_meta)` writes it through
`io.npz.NpzWriter` (uncompressed, 64-byte-aligned members, no pickle, written to a temporary
file and moved over the destination). Discharge times are CSR (`spike_times` int32 +
`spike_offsets` int64, `pack_csr` / `unpack_csr`); names and parameters are JSON
strings; the artifact mask is stored as `[start, end)` intervals. `pulse_trains=None`
writes spike times only. `pulse_trains` can also be an `io.npz.RowSource` (shape + a
`read(first, stop)` of rows), which the writer reads a block of rows at a time: the saves
write the kept MUs' trains that way, without stacking them. Reading goes through
`load_decomposition`, whose embedded EMG (`emg_data`) is mapped in place from a v2 file.

Reading goes through `io.npz.NpzArchive`, which opens the file once, memory-maps
uncompressed members in place and rebuilds v1 object arrays with a restricted unpickler
(arrays, dtypes, numpy scalars and plain Python values only; anything else raises
`LegacyPickleError`).

### Main entry points

```python
def load_decomposition(filepath: str, store: ArrayStore | None = None, *, with_signal: bool = True) -> tuple[LoadedDecomposition, EditSignalContext | None]
```
Reads a `.npz` (v1 or v2) or `.mat` (v5 or v7.3) file once and returns the normalized
`LoadedDecomposition` plus the embedded EMG context, whose EMG and aux arrays are written
into `store` as float32 block by block (a `RamStore` when none is given). v2 pulse trains
stay memory-mapped from the file; a file without them gets binary float32 trains built from
its spike times. v7.3 EMG is read by slice. Handles the 1-based MATLAB discharge-time
convention (shifts by -1). A v1 `.npz` embeds filtered EMG, so its context has
`prefiltered=True`.

### Key helper functions

| Function | Description |
|---|---|
| `normalize_spike_times(raw) -> list[IntArray]` | One `spike_array` per MU (sorted, deduplicated, non-negative int32) |
| `build_pulse_trains_from_spike_times(spike_times, total_samples) -> np.ndarray` | Binary float32 `(n_mu, total_samples)` pulse matrix from discharge indices |
| `save_editlog(path, mu_uids, edit_history, artifact_times)` | Write JSON editlog sidecar; `artifact_times` only when an MU has markers (the edit save writes it to a temporary file and renames it with the `.npz`) |
| `_npz_v1_decomp(archive)` / `_npz_v2_decomp(archive) -> DecompositionLoad` | Read the MUedit NPZ schemas |
| `_mat5_decomp(mat)` / `_mat73_decomp(h5f) -> DecompositionLoad` | Read MATLAB MAT (scipy v5 or HDF5 v7.3, without the EMG samples) |
| `_parse_mu_grid_index(raw) -> list[int]` | Normalize MU-to-grid assignment |
| `_first_float(raw) -> float \| None` | First value of a scalar or array payload (`fsamp`) |
| `_spike_times_from_pulse_matrix(matrix) -> list[IntArray]` | Derive discharge times from pulse matrix non-zero entries |
| `_shift_spike_times(values, shift, limit) -> list[IntArray]` | Shift all discharge times by constant, clip to `[0, limit)` |

### MATLAB-style payloads (`decomp/_mat_layout.py`)

Normalizers for the cell arrays, matrices of either orientation and case-insensitive struct
fields that `.mat` and schema v1 `.npz` files hold. `decomposition_file.py` imports them (and
re-exports `normalize_spike_times` for the editing service); they import nothing from it.

| Function | Description |
|---|---|
| `unpack_gridwise_decomposition(pulse_trains, spike_times_raw, mu_grid_index) -> (pulse_trains, spike_times, mu_grid_index)` | Stack per-grid blocks |
| `coerce_pulse_matrix(value)` / `normalize_pulse_matrix(pulse)` / `infer_total_samples_from_pulse(pulse)` | 2-D `(n_mu, n_samples)` pulse matrices and their length |
| `parse_discard_channels(raw)` / `parse_signal_coordinates(raw)` / `parse_signal_ied(raw)` | Per-grid channel masks, `(n_channels, 2)` coordinates, IED |
| `get_case_insensitive(mapping, *names)` / `first_non_none(*values)` / `unwrap_parameters(raw)` / `samples_in(shape)` | Struct field lookup and small payload helpers |

### `DecompositionLoad` (NamedTuple)

| Field | Type |
|---|---|
| `pulse_trains` | `Any` |
| `spike_times_raw` | `Any` |
| `fsamp` | `float \| None` |
| `total_samples` | `int \| None` |
| `grid_names` | `list[str]` |
| `mu_grid_index` | `list[int]` |
| `parameters` | `dict[str, Any]` |
| `rois` | `list[tuple[int, int]]` |
| `muscles` | `list[str]` |
| `sil` | `list[float]` |
| `artifact_mask` | `np.ndarray \| None` |

### BIDS metadata keys (LOADER_BIDS_META_KEYS)

`manufacturer`, `device_name`, `powerline_freq`, `gains`, `emg_hpf`, `emg_lpf`, `aux_gains`, `aux_hpf`, `aux_lpf`, `aux_units`, `hardware_filters`, `units`, `recording_type`, `software_filters`, `software_versions`

---

## Adaptive Post-Processing (`adaptive_batch.py`)

Instead of applying static ICA filters to the full signal, the adaptive engine runs `AdaptiveDecomp` in a bidirectional, batch-by-batch manner, allowing the whitening matrix, separation vectors, and spike detection centroids to adapt to signal non-stationarity.

### `adaptive_batch_process()` — Main entry

```python
def adaptive_batch_process(
    mu_filters_by_window, whiten_mat_by_window, grid_data, coordinates_plateau, total_samples, fsamp,
    nwindows, win_mean_by_window, params,
    artifact_mask=None, store=None,
) -> (pulse_trains, spike_times)
```

`adaptive_batch_process` maps them into each pass's `Config` (`adapt_batch_ms`, `adapt_wh`,
`adapt_sv`, `adapt_sd`, `adapt_wh_learning_rate`, `adapt_sv_learning_rate`, `adapt_cov_alpha`,
`adapt_spike_prev_weight`, and `work_dtype` from `compute_dtype`); the batch size is
`int(adapt_batch_ms * fsamp / 1000)`.

For each window: a samples-first `StreamedExtender` in the run's `compute_dtype` over the grid's kept channels (window mean subtracted per batch), a bidirectional adaptive pass writing the `signed_square` of each batch's pulse trains into the store, artifact regions zeroed, discharge times collected per batch.

### Bidirectional pass

```python
def _run_bidirectional(source, whiten_mat, mu_filters, calib_start, calib_end, config, sink)
```
Forward from `calib_start` (warming up on the batch before it), then, if the window does not start
the recording, backward over `[0, calib_start)`: batches in reverse order, each read with its own
look-back, so nothing is reversed or concatenated.

### Calibration

The forward pass's `AdaptiveDecomp` calibrates on the window in two passes over it. The first,
in chunks of 4096 samples, sums the whitening covariance in float64 (never holding more than one
chunk of the extended window) and projects each chunk through the filters; the base/spike
centroids come from those squared projections (`adaptation.calibration_centroids`: refractory
peaks, k-means amplitude split). The second, in whole batches, blends each batch's whitened
covariance into the running one (an EMA with `cov_alpha`), so the pass starts from the window's
covariance smoothed over its batches. The covariance is fitted either way; `adapt_wh` only
controls whether `run` keeps updating it. The backward pass reuses the forward pass's
`Calibration`, fitted centroids included, so it starts from the same state and does not read the
window again.

---

## `adaptation.py` — Adaptive Online Learning Engine

### Settings

The adaptation takes the `adapt_*` fields and the work dtype of `DecompositionParameters` and the
recording's `fsamp`. Peaks are searched with the post-processing refractory period (`POSTPROC_MIN_ISI_SEC`) and between
a third of the baseline centroid and three times the spike centroid (`_SPIKE_HEIGHT_MULT`).

### `AdaptiveDecomp` class

Stateful online learning engine that processes EMG in fixed-size batches, adapting three components:

1. **Whitening matrix** — gradient descent toward identity covariance
2. **Separation vectors** — gradient ascent on logcosh contrast with Gram-Schmidt deflation
3. **Spike detection centroids** — EMA update of base/spike amplitude centroids

```python
class AdaptiveDecomp:
    def __init__(self, source, whiten_mat, mu_filters, calibration, config) -> None
```
`source` is the window's `StreamedExtender` (it carries the artifact mask), `whiten_mat` is the
`(n_extended, n_extended)` whitening matrix, `mu_filters` is `(n_extended, n_mu)` as the
decomposition stores it, and `calibration` is either the `(start, stop)` range to fit on or the
`Calibration` of the forward pass.

```python
    def run(self, start=0, stop=None, reverse=False, sink=None) -> None  # outputs via sink
```
**Main processing loop.** For each batch:
- If artifact-contaminated: no state updates, zero spikes in the artifact region
- If clean: whiten -> separate -> detect spikes (with one flanking sample each side) -> update separation vectors
- The trailing partial batch keeps the whitening but still adapts the centroids and, without an artifact, the separation vectors

### Key methods

| Method | Description |
|---|---|
| `_whiten(emg_batch)` | Apply whitening; with `adapt_wh`, update the EMA covariance and step toward identity |
| `_update_covariance(whitened_signal)` | EMA of the whitened batch covariance, called by `_whiten` and `_calibrate` |
| `_separate(whitened_signal)` | Project through the MU filters |
| `_detect_spikes(pulse_trains_batch, edges, update_centroids=True)` | Amplitude-bounded peak finding with batch-edge context, midpoint threshold, EMA centroid update |
| `_update_separation_vectors(whitened_signal, pulse_trains, spikes)` | Gradient ascent on logcosh, Gram-Schmidt deflation, unit-norm renorm |

### Relationship to main decomp

The static ICA filters from `decompose_step` serve as initialization. The adaptive engine then refines them online, processing the signal in batches and adapting to non-stationarity. The main pipeline calls `adaptive_batch_process()` from `postprocess_step` when `use_adaptive=True`.
