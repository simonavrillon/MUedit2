# 03 — Decomposition Engine

The decomposition engine transforms raw HD-EMG signals into individual motor-unit (MU) spike trains using convolutive FastICA source separation, followed by optional adaptive online post-processing.

---

## Pipeline Overview

```
run_decomposition(filepath, ...)
  │
  ├─ 1. load_step()           → LoadStepOutput
  ├─ 2. preprocess_step()     → PreprocessStepOutput
  ├─ 3. decompose_step()      → DecomposeStepOutput
  ├─ 4. postprocess_step()    → PostprocessStepOutput
  └─ 5. export_step()         → (result_dict, save_path)
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
) -> tuple[dict[str, Any], str]
```

---

## `DecompositionParameters` (types.py)

Algorithm hyper-parameters for a single decomposition run.

| Field | Type | Default | Description |
|---|---|---|---|
| `niter` | `int` | `150` | Max ICA iterations per window |
| `nwindows` | `int` | `1` | Number of analysis sub-windows per grid |
| `initialization` | `bool` | `False` | False = activity-based init, True = random init |
| `random_seed` | `int` | `0` | RNG seed |
| `peel_off_enabled` | `bool` | `False` | Enable MU waveform subtraction (peel-off) |
| `covfilter` | `bool` | `False` | Filter MUs by ISI coefficient-of-variation |
| `duplicatesbgrids` | `bool` | `False` | Remove duplicate MUs across grids |
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
| `filename` | `str` |
| `signal` | `SignalImport` |
| `data` | `FloatArray` |
| `fsamp` | `float` |

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
| `roi_list` | `list[tuple[int, int]]` |
| `ngrid` | `int` |
| `coordinates_plateau` | `list[int]` |
| `artifact_mask` | `np.ndarray \| None` |
| `bad_channel_masks` | `list[np.ndarray] \| None` |

### `DecomposeStepOutput`
| Field | Type |
|---|---|
| `mu_filters` | `dict[int, np.ndarray]` |
| `whiten_mat` | `dict[int, np.ndarray]` |
| `coordinates_plateau` | `list[int]` |
| `sil_by_window` | `dict[int, list[float]]` |
| `mu_grid_index` | `list[int]` |
| `win_means` | `dict[int, np.ndarray]` |

### `PostprocessStepOutput`
| Field | Type |
|---|---|
| `pulse_t` | `np.ndarray` |
| `distime` | `list[np.ndarray]` |
| `mu_grid_index` | `list[int]` |
| `sil_by_window` | `dict[int, list[float]]` |
| `sil` | `list[float]` |
| `adaptive_losses` | `dict[int, Any]` |

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
                    bids_root, bids_entities, bids_metadata, artifact_regions=None) -> PreprocessStepOutput
```

Steps in order:

1. Copy data to `float64`
2. Resolve grid names, coordinates, IED, discard channels via `format_hdemg_signal()`
3. Validate channel count
4. Apply per-grid notch filters (`notch_signals()`)
5. Apply per-grid bandpass filters (`bandpass_signals()`) with grid-specific EMG type
6. Export raw EMG to BIDS (if `bids_root` provided)
7. Resolve ROI list (explicit `rois`, single `roi`, interactive `manual_roi`, `duration`, or full signal). If `nwindows > 1` and single ROI resolved, split into `nwindows` equal sub-windows.
8. Build `coordinates_plateau` (legacy flat format: `[s0, e0, s1, e1, ...]`)
9. If `auto_mask_artifacts`: run `run_auto_qc()` to detect artifact mask and bad channels — OR bad channels into `discard_channels`. User-drawn `artifact_regions` (if provided) are rasterized via `build_manual_artifact_mask` and OR'd into the artifact mask.

### Helper functions (private)

| Function | Description |
|---|---|
| `select_roi_interactively(data, fsamp) -> (start, end)` | Matplotlib ginput ROI selection |
| `_resolve_roi_list(data, fsamp, duration, manual_roi, roi, rois, nwindows)` | Resolves analysis ROI from various input methods |
| `_build_coordinates_plateau(ngrid, roi_list)` | Flattens per-grid ROI into legacy format |
| `_apply_grid_notch_filters(data, fsamp, grid_names, coordinates)` | Per-grid notch filtering (in-place) |
| `_apply_grid_bandpass_filters(data, fsamp, grid_names, coordinates, emg_type)` | Per-grid bandpass filtering (in-place) |
| `_export_raw_emg_bids(...)` | Exports raw unfiltered EMG + sidecars to BIDS layout |
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

### Functions

```python
def pca_extended_signal(signal) -> (eigenvectors, eigenvalues_diag)
```
PCA eigendecomposition of extended signal covariance. Selects components above rank-tolerance threshold.

```python
def whiten_extended_signal(signal, eigenvectors, eigenvalues_diag) -> (whitened, whiten_mat)
```
Whitens: `eigenvectors @ inv(sqrt(eigenvalues_diag)) @ eigenvectors.T`

```python
def fixed_point_alg(w, x, basis, maxiter, contrast_func) -> w
```
One-unit FastICA fixed-point iteration with Gram-Schmidt orthogonalization against `basis`. Supports `"skew"`, `"kurtosis"`, `"logcosh"` contrast functions. Converges when `delta < 1e-4` or `maxiter` reached.

```python
def get_spikes(w, x, fsamp) -> (icasig, spikes)
```
Computes pulse train, detects peaks, k-means amplitude split, removes outliers above `mean + 3*std`.

```python
def minimize_isi_covariance(w, x, cov, fsamp) -> (best_w, best_spikes, best_cov)
```
Iteratively refines separation vector by re-detecting spikes and re-summing `x[:, spikes]`, tracking the best (lowest) ISI coefficient-of-variation.

```python
def compute_silhouette(x, w, fsamp) -> (icasig, spikes2, sil)
```
Silhouette-like separability score using k-means cluster centroids: `sil = (between - within) / max(within, between)`.

```python
def subtract_mu_waveforms(x, spikes, fsamp, win) -> ndarray
```
Subtracts averaged MU waveform (estimated from spikes within `+/- win*fsamp` samples) from the multichannel signal. This is the peel-off step.

```python
def batch_process_filters(mu_filters_by_window, whitened_windows, coordinates, ltime, fsamp,
                           whiten_mat_by_window, build_full_extended, window_to_grid,
                           win_means_by_window, artifact_mask) -> (pulse_t, distime)
```
Applies MU filters across all windows to reconstruct pulse trains and spike times. In full-trace mode, dewhitens filters and projects through full extended signal with mean-correction. In windowed mode, projects through per-window whitened signal. Zeros pulse trains at artifact-masked samples, then detects spikes via `find_refractory_peaks` + `split_by_amplitude`.

```python
def rem_duplicates(pulse_t, distime, distime_ref, maxlag, jitter, tol, fsamp)
    -> (kept_pulses, kept_distimes, kept_indices)
```
Removes duplicated MUs using lag-aware spike-train overlap. For each pair: expand jittered spike times, find best cross-correlation lag, compute overlap score, group duplicates if `score >= tol`. Within each group, keeps the MU with lowest ISI CoV.

---

## Stage 4: Postprocess (`postprocess.py: postprocess_step`)

```python
def postprocess_step(prep, decomposed, params, progress_cb) -> PostprocessStepOutput
```

Steps:

1. **Filter application**:
   - If `use_adaptive`: extract per-grid raw data and call `adaptive_batch_process()` with all adapt params + artifact mask
   - Else: call `batch_process_filters()` (full-trace dewhitened or windowed mode)
2. **Deduplication** via `_remove_duplicates_by_grid()` (calls `rem_duplicates()` with `maxlag = fsamp/40`, `jitter = 0.00025s`)
3. **SIL remapping** from deduplicated global indices
4. Return `PostprocessStepOutput`

### Private helpers

| Function | Description |
|---|---|
| `_remove_duplicates_by_grid(pulse_t, distime, mu_grid_index, ngrid, params, fsamp)` | Within-grid (and optionally cross-grid) dedup |
| `_reconstruct_window_signal(prep, params, win_global, whiten_mat) -> (win_data, w_sig)` | Recompute window data for filter application |
| `_make_window_reconstructors(prep, params, decomposed) -> (get_win_data, get_w_sig)` | Returns cached callable reconstructors |

---

## Stage 5: Export (`postprocess.py: export_step`)

```python
def export_step(loaded, prep, post, params, include_full_preview, save_npz, save_emg_data,
               progress_cb) -> (result_dict, save_path)
```

Steps:

1. Resolve save path (BIDS derivatives layout or sibling to input file)
2. Build preview payload via `build_preview_payload()`
3. Construct `DecompositionExport` model and call `.to_dict()`
4. Attach `adaptive_losses` to result dict
5. If `save_npz`: save NPZ with extras (sil, sil_by_window, rois, emg_data, discard_channels, coordinates, artifact_mask)
6. Emit `"done"` progress callback with summary + preview

---

## Preview (`preview.py`)

```python
def downsample_vector(vector, source_fs, target_fs=1000.0) -> list[float]
```
Decimates 1-D array by integer slicing (`step = round(source_fs / target_fs)`).

```python
def build_preview_payload(signal, data, fsamp, pulse_t, distime, grid_names, roi_list,
                           discard_channels, coordinates, mu_grid_index, loader_meta,
                           muscles, include_full_preview) -> dict[str, Any]
```
Builds the preview payload dict sent to the frontend:

| Key | Content |
|---|---|
| `mean_abs` | Downsampled mean absolute amplitude across all channels |
| `pulse_trains` | First 3 MU pulse trains (downsampled, if not full preview) |
| `pulse_trains_all` | All MU pulse trains (downsampled, if not full preview) |
| `pulse_trains_full` | Full-resolution float32 pulse trains (if `include_full_preview`) |
| `distime` / `distime_all` | Per-MU discharge time lists |
| `grid_mean_abs` | Per-grid downsampled mean-abs (excluding discarded channels) |
| `channel_means` | Per-channel mean-abs (including discarded) |
| `rois`, `grid_names`, `mu_grid_index`, `metadata`, `muscle`, `coordinates` | Metadata |
| `auxiliary` / `auxiliaryname` | Downsampled auxiliary signals |

---

## Decomposition Files (`decomp/decomposition_file.py`)

Owns the app `.npz` schema: `save_decomposition_npz(out_path, pulse_trains, distimes, fsamp, grid_names, mu_grid_index, muscles, parameters, total_samples, extras)` writes it (used by the pipeline and the edit-stage save), and `_load_npz_decomp` reads it. `pack_object_array(items)` builds the 1-D object arrays the schema uses.

### Main entry points

```python
def load_decomposition_file(filepath: str) -> LoadedDecomposition
```
Loads a `.npz` or `.mat` decomposition file into a normalized `LoadedDecomposition`. Handles 1-based MATLAB discharge-time convention (shifts by -1).

```python
def load_decomposition_signal_context(filepath: str) -> EditSignalContext | None
```
Best-effort extraction of raw EMG context embedded in decomposition files. Returns dict with `data`, `fsamp`, `grid_names`, `emgmask`, `artifact_mask`, `coordinates`, `ied`, `aux_data`, `aux_names`, and BIDS metadata keys.

### Key helper functions

| Function | Description |
|---|---|
| `normalize_distimes(raw) -> list[list[int]]` | Normalize discharge-time payloads (sorted, deduplicated, non-negative) |
| `build_pulse_trains_from_distimes(distimes, total_samples) -> np.ndarray` | Binary `(n_mu, total_samples)` pulse matrix from discharge indices |
| `save_editlog(path, mu_uids, edit_history, artifact_times)` | Write JSON editlog sidecar |
| `first_non_none(*values)` | Return first non-None argument |
| `_load_npz_decomp(filepath) -> DecompositionLoad` | Load MUedit NPZ format |
| `_load_mat_decomp(filepath) -> DecompositionLoad` | Load MATLAB MAT (dispatches v7.3 HDF5 or legacy scipy) |
| `_load_mat73_decomp(filepath) -> DecompositionLoad` | Load MATLAB v7.3 (HDF5) |
| `_parse_mu_grid_index(raw) -> list[int]` | Normalize MU-to-grid assignment |
| `_unpack_gridwise_decomposition(pulse_trains, distime_raw, mu_grid_index) -> (pulse, distime, mu_grid_index)` | Stack per-grid blocks |
| `_distimes_from_pulse_matrix(matrix) -> list[list[int]]` | Derive discharge times from pulse matrix non-zero entries |
| `_shift_distimes(values, shift, limit) -> list[list[int]]` | Shift all discharge times by constant, clip to `[0, limit)` |

### `DecompositionLoad` (NamedTuple)

| Field | Type |
|---|---|
| `pulse_trains` | `Any` |
| `distime_raw` | `Any` |
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
    mu_filters_by_window, w_sig_by_window, win_data, whiten_mats,
    grid_data, coordinates, ltime, fsamp, nwindows_per_grid,
    win_means_by_window=None, batch_ms=100,
    adapt_wh=True, adapt_sv=True, adapt_sd=True,
    wh_learning_rate=7e-3, sv_learning_rate=3e-3,
    cov_alpha=0.1, spike_prev_weight=5,
    compute_loss=False, artifact_mask=None,
) -> (pulse_t, distime, all_losses)
```

For each window: demean data, compute calibration stats from the window, run bidirectional adaptive decomposition, extract per-MU pulse trains (`signed_square` of ipts), zero artifact regions, collect discharge times.

### Bidirectional pass

```python
def _run_one_pass(grid_data_g, whiten_mat, mu_filters, base_centr, spikes_centr,
                  config, artifact_mask, reverse=False)
    -> (ipts, spikes, losses)
```
Single-direction adaptive decomposition over one grid. When `reverse=True`, the signal is reversed in blocks, processed, then output reversed back. Handles artifact mask slicing.

```python
def _run_adapt_decomp_bidirectional(grid_data_g, win_data_g, whiten_mat, mu_filters,
    base_centr, spikes_centr, w_sig, calib_start, config, artifact_mask)
    -> (ipts, spikes, losses)
```
Runs adaptive decomposition forward from `calib_start` and, if needed, backward over the pre-calibration segment. The backward pass reverses the signal in blocks, runs adaptive decomp, then reverses the output back. Handles artifact mask slicing/reversal.

### Calibration

```python
def _compute_calibration_stats(w_sig, mu_filters, fsamp) -> (base_centr, spikes_centr)
```
Projects the whitened calibration signal through MU filters, detects spikes, derives base/spike centroids via k-means amplitude split.

---

## `adapt_decomp/` — Adaptive Online Learning Engine

### `Config` (config.py)

```python
@dataclass
class Config:
    fsamp: int = 2048
    ex_factor: int = 10
    batch_ms: int = 100
    adapt_wh: bool = True
    adapt_sv: bool = True
    adapt_sd: bool = True
    wh_learning_rate: float = 7e-3
    sv_learning_rate: float = 3e-3
    cov_alpha: float = 0.1
    compute_loss: bool = False
    spike_height_mult: int = 3
    spike_prev_weight: int = 5
    spike_dist_ms: int = 5
    batch_size: int  # computed: batch_ms * fsamp / 1000
```

### `AdaptiveDecomp` class (adaptation.py)

Stateful online learning engine that processes EMG in fixed-size batches, adapting three components:

1. **Whitening matrix** — gradient descent toward identity covariance
2. **Separation vectors** — gradient ascent on logcosh contrast with Gram-Schmidt deflation
3. **Spike detection centroids** — EMA update of base/spike amplitude centroids

```python
class AdaptiveDecomp:
    def __init__(self, emg, whitening, sep_vectors, base_centr, spikes_centr,
                 emg_calib, config, artifact_mask=None) -> None
```
Constructor: extends EMG signal, aligns artifact mask, runs whitening calibration (EMA covariance + KL divergence stats), and optionally contrast calibration.

```python
    def run(self) -> (ipts_output, spikes_output, losses)
```
**Main processing loop.** For each batch:
- If artifact-contaminated: forward-only pass, no state updates, zero spikes in artifact region
- If clean: whiten -> separate -> signed-square -> detect spikes -> compute losses -> update separation vectors
- Handles remainder samples after the last full batch

### Key methods

| Method | Description |
|---|---|
| `_whiten(emg_batch)` | Apply whitening, update EMA covariance, gradient descent if `adapt_wh` |
| `_separate(whitened_signal)` | Project through separation vectors |
| `_detect_spikes(ipts_squared, update_centroids=True)` | Amplitude-bounded peak finding, midpoint threshold, EMA centroid update |
| `_kl_divergence()` | KL divergence between estimated whitened covariance and identity |
| `_wh_loss(kl_div)` | Normalized whitening loss (squared z-score vs calibration) |
| `_contrast_value(ipts_batch, spikes_batch)` | Mean logcosh contrast at spike positions, per MU |
| `_sv_loss(contrast)` | Normalized separation vector loss |
| `_update_separation_vectors(whitened_signal, ipts, spikes)` | Gradient ascent on logcosh, Gram-Schmidt deflation, unit-norm renorm |

### Module-level function

```python
def run_adaptive_decomposition(emg, whitening, sep_vectors, base_centr, spikes_centr,
                                emg_calib, config, artifact_mask=None)
    -> (ipts, spikes, losses)
```
Constructs `AdaptiveDecomp` and calls `.run()`.

### Relationship to main decomp

The static ICA filters from `decompose_step` serve as initialization. The adaptive engine then refines them online, processing the signal in batches and adapting to non-stationarity. The main pipeline calls `adaptive_batch_process()` from `postprocess_step` when `use_adaptive=True`.
