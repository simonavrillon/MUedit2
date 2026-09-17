# 04 — I/O, Signal Processing, and QC

Covers the file loading layer, signal filtering, grid geometry inference, and the automatic quality-check pipeline.

---

## File Loading (`io/`)

### Loader Output

All built-in loaders return a `SignalImport` (see `models.py`), built with
`SignalImport.build(...)`, which applies the shared coercion rules:

```python
SignalImport.build(
    data=...,           # -> FloatArray (n_channels, n_samples); 1-D input is one channel
    fsamp=...,          # -> float; None becomes 0.0
    gridname=...,       # -> list[str]; a single name becomes a one-item list
    muscle=...,         # -> list[str]
    auxiliary=...,      # -> FloatArray (n_aux, n_samples), padded/truncated to data
    auxiliaryname=...,  # -> list[str]
    metadata=...,       # -> dict[str, Any] (device, filters, coordinates, IEDs, ...)
)
```

Loaders registered with `register_loader` may still return a plain dict with these
keys; `load_signal` converts it with `SignalImport.from_mapping`, which applies the
same rules.

### Loader Registry (`factory.py`)

| Extension | Loader | Source Module |
|---|---|---|
| `.mat` | `load_mat` | `mat.py` |
| `.otb+` | `load_otb_plus` | `_otb.py` |
| `.otb4` | `load_otb4` | `_otb.py` |
| `.bdf` | `load_bids_signal` | `_bids_reader.py` |
| `.edf` | `load_bids_signal` | `_bids_reader.py` |
| `.rhd` | `load_intan` | `_intan.py` |

#### Dispatch logic (`get_loader`)

- If `filepath` is a **directory**:
  - Globs for `*_emg.bdf` / `*_emg.edf` -> returns `load_bids_signal`
  - Globs for `*.rhd` -> returns `load_intan`
  - Otherwise raises `ValueError`
- If a **file**: looks up `path.suffix.lower()` in `_LOADERS`; raises `ValueError` if not found.

#### Public functions

| Function | Signature | Description |
|---|---|---|
| `register_loader` | `(ext, loader, *, overwrite=False) -> None` | Register a custom loader |
| `supported_extensions` | `() -> tuple[str, ...]` | List supported extensions |
| `get_loader` | `(filepath) -> LoaderFn` | Resolve loader for a path |
| `load_signal` | `(filepath) -> SignalImport` | Load a signal and normalize loader output to `SignalImport` |

### MATLAB `.mat` (`mat.py`)

```python
def load_mat(filepath: str) -> SignalImport
```
Tries `scipy.io.loadmat` (MAT v5) first; if HDF5 (v7.3), falls back to `_load_mat73_signal`. Expects a `signal` struct with fields: `data`, `fsamp`, `gridname`, `muscle`, `auxiliary`, `auxiliaryname`, `device_name`. Detects and rejects decomposition files (pulse train + discharge time fields) loaded as raw signals.

Key helpers: `_parse_text`, `parse_text_list` (public), `_parse_numeric_array`, `mat73_read` (recursive HDF5, public), `_align_to_n_samples`, `_has_decomposition_markers`, `_load_mat73_signal`.

### Intan RHD (`_intan.py`)

```python
def load_intan(filepath: str, grid_names=None, muscles=None) -> SignalImport
```
Accepts a recording directory, its `info.rhd`, or a single-file `.rhd`. Supports three save layouts:
- **traditional**: `.rhd` file contains header + data blocks
- **per_channel**: header-only `info.rhd` + one `.dat` per channel (e.g. `amp-A-000.dat`)
- **per_signal_type**: header-only + one `.dat` per signal type (e.g. `amplifier.dat`)

Scales amplifier data to millivolts (0.195 uV/bit x amplifier gain 192), scales auxiliary streams to physical units, groups amplifier channels into one grid per port, resolves grid model names (from args -> `muedit_grids.json` sidecar -> channel-count default), calls `format_hdemg_signal` for coordinates/IEDs.

Key structures: `_IntanChannel`, `_IntanHeader` (with `enabled_channels()`, `samples_per_block`), `_Recording`. Magic: `_RHD_MAGIC = 0xC6912702`. Default grids: `{64: "INTAN64-1305"}`.

### OT Bioelettronica (`_otb.py`)

```python
def load_otb_plus(filepath: str) -> SignalImport
```
Loads OTB+ archives (`.otb+` tar or `.zip`). Extracts to temp dir, parses XML metadata (device name, sample frequency, AD bits, adapter/channel tree), reads `.sig` binary (int16 or int32), applies device-specific ADC-to-mV scaling for: `QUATTROCENTO`, `QUATTRO`, `DUE`, `DUE+`, `QUATTRO+`, `SESSANTAQUATTRO`, `SESSANTAQUATTRO+`, `SYNCSTATION`, and a generic fallback.

```python
def load_otb4(filepath: str) -> SignalImport
```
Loads OTB4 archives (`.zip` or `.tar`). Extracts, finds `Tracks_000.xml`, parses `ArrayOfTrackInfo`, dispatches to `_parse_otb4_novecento` or `_parse_otb4_generic` based on device field. Refines grid names from `OriginalSensor` in `StringsDescriptions`. Sanitizes arrays (NaN/Inf replacement).

### BIDS EMG Reading (`_bids_reader.py`)

```python
def load_bids_signal(filepath: str) -> SignalImport
```
Accepts a `.bdf`/`.edf` file path or directory containing one. Reads:
- `*_emg.json` sidecar -> fsamp, device_name, hardware_filters, round-trip metadata
- `*_channels.tsv` -> per-grid channel grouping, bad-channel masks, filter/gain values, auxiliary channels
- EDF/BDF signal data via `pyedflib.EdfReader`

Groups channels by BIDS `group` column (e.g. `Grid1`, `Grid2`), reads all signals, vstacks EMG grids, extracts auxiliary (non-EMG) channels, calls `format_hdemg_signal(grid_names)` for coordinates/IEDs/discard-vectors.

Key functions: `resolve_bids_emg_path`, `resolve_bids_channels_tsv`, `select_grid_channels`, `load_bids_emg_grid(root, entity_label, grid_index, read_start, read_n) -> (data, fsamp, bad_mask)`.

### BIDS EMG Export (`bids.py`)

#### `export_bids_emg(data, fsamp, grid_names, coordinates, discard_channels, bids_root, ...) -> dict[str, Path]`

Writes a full BIDS EMG dataset:
- EDF/BDF signal file
- `_emg.json` sidecar
- `_channels.tsv`
- `_electrodes.tsv`
- Per-space `_coordsystem.json` files

Supports extensive parameters: subject, task, run, session, acquisition, recording, IED, powerline_freq, placement_scheme, units, target_muscle, file_format (bdf/edf), hardware_filters, gain, low/high cutoff, aux data/names, manufacturer, model name, task description, notch, software versions, recording type.

#### `write_bids_dataset_description(bids_root, *, subject, age, sex, handedness) -> None`
Creates/updates `dataset_description.json`, `participants.tsv`, `participants.json`, `.bidsignore`. Idempotent.

#### `export_bids_mu_derivatives(distimes, fsamp, bids_root, entities, pipeline_name, desc, mu_uids) -> dict[str, Path]`
Writes motor unit spike times as BIDS derivatives `events.tsv` + `events.json` under `derivatives/<pipeline_name>/`. Each spike -> one row with `onset`, `duration`, `sample`, `unit_id`, `description`.

#### `build_entities(subject, task, run, session, acquisition, recording) -> str`
Builds BIDS entity prefix string (e.g. `sub-01_ses-01_task-force_run-01`).

---

## Signal Processing (`signal/`)

### Filters (`filters.py`)

```python
def demean(signal: np.ndarray) -> np.ndarray
```
Removes per-channel DC offset (subtracts mean along `axis=1`).

```python
def bandpass_signals(signal: np.ndarray, fsamp: float, emg_type: int = 1) -> np.ndarray
```
Zero-phase Butterworth bandpass via `filtfilt`:
- `emg_type=1` -> 20-500 Hz (order 2, requires fs > 1000) — surface HD-EMG
- `emg_type=2` -> 100-4400 Hz (order 3, requires fs > 8800) — intramuscular

```python
def notch_signals(signal: np.ndarray, fsamp: float) -> np.ndarray
```
FFT-based notch suppressing mains harmonics without knowing the line frequency. Per-channel: compute FFT, scan frequency bins in `NOTCH_WINDOW_HZ` (50 Hz) segments, flag bins exceeding median+5sigma, remove interference band, reconstruct with Hermitian symmetry.

### Downsample (`downsample.py`)

| Constant | Value |
|---|---|
| `PREVIEW_MOVING_AVG_MS` | `25.0` |

```python
def raw_series_at_fs(series, source_fs, target_fs) -> list[float]
```
Downsamples 1-D series using `scipy.signal.decimate` (FIR anti-alias, zero-phase).

```python
def moving_average_ms(series, fsamp, window_ms) -> np.ndarray
```
Moving average with window size in ms (convolution, `"same"` mode).

### Decomposition Primitives (`decomp_primitives.py`)

| Constant | Value | Description |
|---|---|---|
| `KMEANS_ITER` | `10` | K-means iterations for amplitude split |
| `DECOMP_MIN_ISI_SEC` | `0.02` | Min ISI for decomposition peak picking (20 ms) |
| `POSTPROC_MIN_ISI_SEC` | `0.005` | Min ISI for postprocessing peak picking (5 ms) |

```python
def extend_signal(signal, exfactor, samples_first=False) -> np.ndarray
```
Delay-embedding channel extension for convolutive source separation. Hankel-style block embedding (or column tiling if `samples_first=True`).

```python
def signed_square(x) -> np.ndarray
```
Returns `x * |x|` — signed-squared nonlinearity for building pulse trains.

```python
def find_refractory_peaks(signal, fsamp, min_isi_sec=DECOMP_MIN_ISI_SEC, **kwargs) -> np.ndarray
```
Peak picking with refractory-distance constraint via `scipy.signal.find_peaks`.

```python
def enforce_refractory(signal, fsamp, min_isi_sec=POSTPROC_MIN_ISI_SEC) -> np.ndarray
```
Removes peaks that violate the minimum inter-spike interval. Used during post-processing to clean up spike trains after filter application.

```python
def split_by_amplitude(values, peaks, kmeans_iter=KMEANS_ITER, missing="raise", seed=0)
    -> (high_indices, centroids, labels)
```
K-means (2 clusters, `++` init) on peak amplitudes. Falls back to single centroid on `ClusterError`.

```python
def isi_cov(spikes, fsamp, fallback=np.nan) -> float
```
Coefficient of variation of inter-spike intervals.

### Grid Geometry (`grid.py`)

#### `GridSpec` dataclass

| Field | Type | Description |
|---|---|---|
| `channel_map` | `np.ndarray` | 2-D layout; 0 = no electrode |
| `nbelectrodes` | `int` | Total active electrodes |
| `ied` | `float` | Inter-electrode distance (mm) |
| `emg_type` | `int` | 1 = surface HD-EMG, 2 = intramuscular |
| `manufacturer` | `str` | BIDS ElectrodeManufacturer |
| `electrode_type` | `str` | BIDS ElectrodeType |
| `electrode_material` | `str` | BIDS ElectrodeMaterial |

#### `_GRID_CATALOG` — Known grid models (substring-matched against `grid_name`)

| Catalog Key | Grid | Channels |
|---|---|---|
| `GR04MM1305`, `HD04MM1305` | OTBioelettronica 13x5, 4 mm | 64 |
| `GR08MM1305`, `HD08MM1305` | OTBioelettronica 13x5, 8 mm | 64 |
| `GR10MM0808`, `HD10MM0808` | Neuromotion/OTBioelettronica 8x8, 10 mm | 64 |
| `GR08MM0808` | OTBioelettronica 8x8, 8 mm | 64 |
| `GR10MM0804`, `HD10MM0804` | OTBioelettronica 8x4, 10 mm | 32 |
| `MYOMRF-4x8`, `MYOMNP-1x32` | camber/Emory intramuscular arrays | 32 |
| `INTAN64-1305` | OTBioelettronica GR08MM1305 via Intan RHD2164 adapter | 64 |

#### Functions

```python
def format_hdemg_signal(grid_names, discard_overrides=None)
    -> (coordinates, ied, discard_channels_vec, emg_type)
```
Infers grid geometry. Returns one entry per grid. `coordinates[i]` is `(nbelectrodes, 2)` row/col positions. Raises `ValueError` on unknown grid or mismatched `discard_overrides`.

```python
def get_grid_electrode_metadata(grid_name: str) -> dict
```
Returns BIDS electrode metadata dict for the grid model. Unknown grids return `"n/a"` placeholders.

---

## Automatic QC Pipeline (`signal/qc_pipeline.py`)

Orchestrates bad-channel detection and artifact masking in three stages, where each stage benefits from the previous:

```
Stage 0: preliminary bad channels (robust criteria only)
    -> _select_kept_channels (drop bad channels)
Stage 1: detect_artifact_masks (kept channels only)
Stage 2: detect_bad_channels_per_grid on _exclude_samples(data, artifact_mask)
    -> OR preliminary + final masks
```

**Artifact-aware bad-channel detection:** Stage 2 excludes artifact-contaminated samples (`_exclude_samples`) before running the full bad-channel criteria, so a rest-time artifact doesn't inflate the peak/median ratio and create a false intermittent-contact flag.

### `QCPipelineResult` dataclass

| Field | Type | Shape | Description |
|---|---|---|---|
| `artifact_mask` | `np.ndarray` | `(n_samples,)` bool | True at artifact samples |
| `bad_channel_masks` | `list[np.ndarray]` | per-grid `(n_ch,)` bool | True for bad channels |

```python
def run_auto_qc(data, fsamp, grid_channel_counts, grid_coordinates=None,
                artifact_config=None, channel_qc_config=None) -> QCPipelineResult
```
Top-level orchestrator. Stage 0 uses a `dc_replace`'d `ChannelQCConfig` with intermittent/contact-loss/noisy/SNR criteria disabled (only flat/saturated/quantized run).

---

## Bad-Channel Detection (`signal/channel_qc.py`)

Identifies defective channels across 7 criteria: flat, saturated, quantized, noisy, low-SNR, intermittent-contact, sustained contact-loss. This is **spatial** (which channels); artifact masking is **temporal** (when).

### `ChannelQCConfig`

| Field | Type | Default | Purpose |
|---|---|---|---|
| `flat_rms_ratio` | `float` | `0.10` | Flat if RMS < this x grid median RMS |
| `flat_abs_floor` | `float` | `1e-8` | Skip flat detection if grid median RMS below this |
| `sat_extreme_frac` | `float` | `0.005` | Saturated if > this fraction at min/max extreme |
| `sat_tol_frac` | `float` | `0.001` | Tolerance for extreme test (fraction of p-p range) |
| `quant_max_unique_frac` | `float` | `0.80` | Quantized if unique values < this x n_samples |
| `quant_min_samples` | `int` | `1000` | Min n_samples for quantization check |
| `neighbor_dist` | `float` | `1.5` | Max grid distance for "neighbour" |
| `noisy_corr_threshold` | `float` | `0.10` | Noisy if mean neighbour corr below this |
| `noisy_abs_floor` | `float` | `1e-7` | Skip noisy check if grid RMS below this |
| `snr_win_ms` | `int` | `500` | SNR window (ms) |
| `snr_thr` | `float` | `5.0` | Low-SNR if SNR (dB) below this |
| `low_snr_corr_thr` | `float` | `0.50` | Low-SNR also requires corr below this |
| `snr_min_windows` | `int` | `4` | Min windows for SNR check |
| `instability_win_ms` | `int` | `50` | Envelope window for contact-instability |
| `intermittent_amp_ratio` | `float` | `30.0` | Intermittent if peak/median envelope > this |
| `contact_loss_frac` | `float` | `0.10` | Contact-loss if envelope < this x grid median |
| `contact_loss_min_run_ms` | `int` | `1000` | Min duration (ms) of contact-loss run |
| `contact_loss_active_frac` | `float` | `0.30` | Channel must have been this active to be flagged |
| `max_bad_fraction` | `float` | `0.50` | Warn threshold (mask returned unchanged) |

### Functions

```python
def _detect_bad_channels(data, fsamp, coordinates=None, config=None) -> np.ndarray
```
Returns `(n_channels,)` bool mask. Thin wrapper around `_channel_qc_diagnostics(...).mask`.

```python
def _channel_qc_diagnostics(data, fsamp, coordinates=None, config=None) -> ChannelQCMetrics
```
Full-featured detector returning metrics + reasons. Computes all 7 criteria and OR-combines into the mask.

### `ChannelQCMetrics`

| Field | Type | Shape |
|---|---|---|
| `mask` | `np.ndarray` | `(n_channels,)` bool |
| `rms` | `np.ndarray` | `(n_channels,)` |
| `snr` | `np.ndarray` | `(n_channels,)` dB |
| `sat_frac` | `np.ndarray` | `(n_channels,)` |
| `n_unique` | `np.ndarray` | `(n_channels,)` int |
| `mean_neighbor_corr` | `np.ndarray` | `(n_channels,)` |
| `max_win_ratio` | `np.ndarray` | `(n_channels,)` |
| `max_loss_run` | `np.ndarray` | `(n_channels,)` int |
| `reasons` | `list[str]` | per-channel reason strings |

```python
def detect_bad_channels_per_grid(data, fsamp, grid_channel_counts, grid_coordinates=None, config=None) -> list[np.ndarray]
```
Runs `_detect_bad_channels` per grid; returns list of per-grid masks.

---

## Artifact Masking (`signal/artifact_mask.py`)

Detects artifact-contaminated sample regions (transient noise) from filtered multi-channel EMG, per grid, using a two-stage robust amplitude detector. Output is a boolean mask (`True` = artifact) used by the decomposition pipeline to skip state updates on contaminated batches.

**Algorithm:** Stage 1 computes per-channel windowed |amplitude|, max-aggregates across channels (top-k by `min_channels`), builds a robust baseline (global median + MAD, optionally a local rolling median via `local_baseline_s`), flags candidate windows via dual criterion (robust z-score OR amplitude-over-median ratio). Stage 2 computes per-channel z-scores and requires a minimum quorum of channels exceeding a per-channel z-threshold (a real artifact excites many channels; a MU burst only spikes a few). Morphological closing + dilation cleans up the mask. Polarity-agnostic.

### `ArtifactMaskConfig`

| Field | Type | Default | Purpose |
|---|---|---|---|
| `win_ms` | `int` | `20` | Sliding-window length (ms) |
| `z_thr` | `float` | `9.0` | Stage-1 robust z-score threshold |
| `amp_ratio` | `float` | `8.0` | Stage-1 amplitude-over-median ratio |
| `ch_z_thr` | `float` | `3.0` | Stage-2 per-channel z threshold |
| `min_channels` | `int` | `5` | Stage-2 quorum count (top-k aggregation) |
| `local_baseline_s` | `float \| None` | `2.0` | Local rolling baseline window (s); `None` to disable |
| `pad_ms` | `int` | `25` | Dilation radius (ms) |
| `min_gap_ms` | `int` | `20` | Closing bridge length (ms) |

```python
def _detect_artifact_mask(data, fsamp, config=None) -> np.ndarray
```
One-grid detection. Returns `(n_samples,)` bool.

```python
def detect_artifact_masks(data, fsamp, grid_channel_counts, config=None)
    -> (per_grid_masks, global_mask)
```
Runs per grid. `global_mask` is the logical OR union across all grids.
