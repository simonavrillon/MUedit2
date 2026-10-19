# 04 — I/O, Signal Processing, and QC

Covers the file loading layer, signal filtering, grid geometry inference, and the automatic quality-check pipeline.

---

## File Loading (`io/`)

### Loader Output

Every loader is `load_<format>(filepath, store=None) -> SignalImport` (see `models.py`):
it writes the full-length EMG and auxiliary arrays into `store` block by block, and builds
its result with `SignalImport.build(...)`, which applies the shared coercion rules:

```python
SignalImport.build(
    data=...,           # -> FloatArray (n_channels, n_samples); 1-D input is one channel
    fsamp=...,          # -> float; None becomes 0.0
    grid_names=...,     # -> list[str]; a single name becomes a one-item list
    muscle_names=...,   # -> list[str]
    aux_data=...,       # -> FloatArray (n_aux, n_samples), padded/truncated to data
    aux_names=...,      # -> list[str]
    metadata=...,       # -> dict[str, Any]: device, filters, gains, start date, ...
)
```

Each loader module has the same shape: a recording dataclass named for its module
(`_IntanRecording`, `_OpenEphysRecording`, `_BidsRecording`; or the format's parsed header),
`_resolve_<format>_recording`/`_resolve_<format>_grids` helpers, then a short `load_<format>`
that allocates
into the store and ends with `SignalImport.build`. Every loader's metadata carries the same
keys, in the same order: `acquisition_date`, `manufacturer`, `device_name`,
`software_versions`, `hardware_filters`, `powerline_freq`, `units`, `gains`, `emg_hpf`,
`emg_lpf`, `aux_units`, `aux_gains`, `aux_hpf`, `aux_lpf`, `aux_kinds` (when digital lines
are known), `recording_type`, `software_filters`; Intan adds `electrode_impedances` (kOhm),
the BIDS reader adds what a round trip needs (`placement_*`, `ground_location`,
`reference_location`, `bad_channels_per_grid`, `grid_labels`, `segments`, `bids_*`).
Grid geometry is not metadata: callers compute it with `format_hdemg_signal(grid_names)`.

### Loader Registry (`factory.py`)

| Extension | Loader | Source Module |
|---|---|---|
| `.mat` | `load_mat` | `_mat.py` |
| `.otb+` | `load_otb_plus` | `_otb.py` |
| `.otb4` | `load_otb4` | `_otb.py` |
| `.bdf` | `load_bids_signal` | `_bids_reader.py` |
| `.edf` | `load_bids_signal` | `_bids_reader.py` |
| `.rhd` | `load_intan` | `_intan.py` |
| `.oebin` | `load_openephys` | `_openephys.py` |

#### Dispatch logic (`get_loader`)

- If `filepath` is a **directory**:
  - Globs for `*_emg.bdf` / `*_emg.edf` -> returns `load_bids_signal`
  - Globs for `*.rhd` -> returns `load_intan`
  - Holds `structure.oebin` -> returns `load_openephys`
  - Otherwise raises `SignalLoadError`
- If a **file**: looks up `path.suffix.lower()` in `_LOADERS`; raises `SignalLoadError` if not found.

#### Public functions

| Function | Signature | Description |
|---|---|---|
| `register_loader` | `(ext, loader, *, overwrite=False) -> None` | Register a custom loader |
| `supported_extensions` | `() -> tuple[str, ...]` | List supported extensions |
| `get_loader` | `(filepath) -> LoaderFn` | Resolve loader for a path |
| `load_signal` | `(filepath, store=None) -> SignalImport` | Load a signal with the loader of its extension |

### MATLAB `.mat` (`_mat.py`)

```python
def load_mat(filepath: str, store=None) -> SignalImport
```
Reads a v7.3 (HDF5) file with `_load_mat73` (numeric EMG in slices straight into the store), any other with `_load_mat5` (`scipy.io.loadmat`). Both feed one builder, `_signal_import`, from a `signal` struct with fields: `data`, `fsamp`, `gridname`, `muscle`, `auxiliary`, `auxiliaryname`, `device_name`. Rejects a decomposition (`signal.Pulsetrain` + `signal.Dischargetimes`) with a `SignalLoadError`.

Helpers shared with `decomp/decomposition_file.py`: `parse_text_list`, `parse_name_list`, `mat73_read` (recursive HDF5).

### Intan RHD (`_intan.py`)

```python
def load_intan(filepath: str, grid_names=None, muscle_names=None, store=None) -> SignalImport
```
Accepts a recording directory, its `info.rhd`, or a single-file `.rhd`. Supports three save layouts:
- **traditional**: `.rhd` file contains header + data blocks
- **per_channel**: header-only `info.rhd` + one `.dat` per channel (e.g. `amp-A-000.dat`)
- **per_signal_type**: header-only + one `.dat` per signal type (e.g. `amplifier.dat`)

Scales amplifier data to millivolts (0.195 uV/bit x amplifier gain 192), scales auxiliary streams to physical units, groups amplifier channels into one grid per port, resolves grid model names (from args -> `muedit_grids.json` sidecar -> channel-count default). Each amplifier channel's measured impedance becomes `electrode_impedances` (kOhm, `None` when never measured), which the BIDS export writes to `electrodes.tsv`. The RHD header records no start date.

Key structures: `_IntanChannel`, `_IntanHeader` (with `enabled_channels()`, `samples_per_block`), `_IntanRecording`. Magic: `_RHD_MAGIC = 0xC6912702`. Default grids: `{64: "INTAN64-1305"}`.

### Open Ephys (`_openephys.py`)

```python
def load_openephys(filepath: str, grid_names=None, muscle_names=None, store=None) -> SignalImport
```
Accepts `structure.oebin` or the recording directory holding it (GUI binary format). Picks the continuous stream with the most ephys (`type` 0) channels and reads its interleaved int16 `continuous.dat` in blocks. Ephys channels are scaled by their `bit_volts` to millivolts; the stream's other channels (ADC, AUX) are scaled to volts, and TTL lines with edges (`events/<stream>/TTL`) become 0/1 auxiliary rows `TTL<n>`. Grid names come from args -> the nearest `muedit_grids.json` (recording, experiment, then Record Node folder) -> one default per headstage that `settings.xml` lists (`HSOPTIONS` slots flagged `full_channels`, 32 channels each -> `MYOMNP-1x32`), else `INTAN64-1305` per 64 channels; a single name is repeated, and the grids must cover every ephys channel. Filter cutoffs come from the source processor's `EDITOR` in the Record Node's `settings.xml` (`settings_<n>.xml` for `experiment<n>`); the acquisition date comes from `sync_messages.txt` (UTC) and becomes the EDF start time on export.

### OT Bioelettronica (`_otb.py`)

```python
def load_otb_plus(filepath: str, store=None) -> SignalImport
```
Loads OTB+ archives (tar files) in one pass over the adapters: `_channel_kind` sorts each channel into grid EMG (32/64-channel inputs), auxiliary, or dropped (bipolar, 16-channel), and `_grid_position` assigns it to a grid. The start date is the device's `@Date`, else the `YYYYMMDDhhmmss` the `.sig` file is named after. Extracts to temp dir, parses XML metadata (device name, sample frequency, AD bits, adapter/channel tree), reads the `.sig` binary (int16 or int32; an archive with several loads the first by name and logs a warning naming the others; its `.xml` of the same name is required), applies device-specific ADC-to-mV scaling for: `QUATTROCENTO`, `QUATTRO`, `DUE`, `DUE+`, `QUATTRO+`, `SESSANTAQUATTRO`, `SESSANTAQUATTRO+`, `SYNCSTATION`, and a generic fallback.

```python
def load_otb4(filepath: str, store=None) -> SignalImport
```
Loads OTB4 archives (tar files). Finds `Tracks_000.xml`, parses `ArrayOfTrackInfo`, and turns it into a list of `_Track`s with `_novecento_tracks` (Novecento+: int32 files per stream) or `_generic_tracks` (one int16 file); one writer then stacks the EMG and auxiliary tracks and the feedback traces. An archive with no EMG track is refused. Grid names `IN…`/`Channel…` take the first track's `OriginalSensor`. The start date is the first track's `StringsDescriptions/StartDate` that is a date.

### BIDS EMG Reading (`_bids_reader.py`)

```python
def load_bids_signal(filepath: str) -> SignalImport
```
Accepts a `.bdf`/`.edf` file path or directory containing one. Reads:
- `*_emg.json` sidecar -> fsamp, device_name, hardware_filters, round-trip metadata
- `*_channels.tsv` -> per-grid channel grouping, bad-channel masks, filter/gain values, auxiliary channels
- EDF/BDF signal data via `pyedflib.EdfReader`

Groups channels by BIDS `group` column (e.g. `Grid1`, `Grid2`), reads all signals, vstacks EMG grids, extracts auxiliary (non-EMG) channels, and reads the start date from the EDF header. A grid's model comes from `grid_name`, else `ElectrodeManufacturersModelName`, and must be in `_GRID_CATALOG`; otherwise the load fails naming the group (no layout is built from `electrodes.tsv`).

Key functions: `resolve_bids_emg_path`, `resolve_bids_channels_tsv(emg_dir, entity_label)`, `read_bids_emg_grid(root, entity_label, grid_index, store) -> (data, fsamp, bad_mask)`, `read_channels_summary(channels_tsv) -> (grid_names, muscle_names, fsamp)` and `read_sidecar_meta(root, entity_label)` (the participant and hardware fields the session form shows).

### BIDS Layout (`_bids_layout.py`)

The one place that knows where files sit in a dataset. `BidsEntities.parse(label)` reads a label or file stem (a label without `sub-<label>` raises `ValueError`); `.label` writes it back in BIDS order; `.emg_dir(root)` is `sub-X[/ses-Y]/emg`; `.derivative_dir(root, datatype, pipeline="muedit")` is `derivatives/<pipeline>/sub-X[/ses-Y]/<datatype>`. `dataset_root(path)` is the parent of a file's nearest `sub-` folder, stepping over `derivatives/<pipeline>`, or `None` outside a dataset.

### BIDS EMG Export (`bids.py`)

#### `export_bids_emg(data, fsamp, grid_names, coordinates, discard_channels, bids_root, ...) -> dict[str, Path]`

Writes a full BIDS EMG dataset:
- EDF/BDF signal file
- `_emg.json` sidecar
- `_channels.tsv`
- `_electrodes.tsv`
- Per-space `_coordsystem.json` files

Supports extensive parameters: subject, task, run, session, acquisition, recording, IED, powerline_freq, placement_scheme, units, target_muscle, file_format (bdf/edf), hardware_filters, gain, low/high cutoff, aux data/names, manufacturer, model name, task description, software versions, recording type, start_time (the EDF start date, from the loader's `acquisition_date`), impedance (kOhm per electrode, from `electrode_impedances`).

#### `write_bids_dataset_description(bids_root, *, subject, age, sex, handedness) -> None`
Creates/updates `dataset_description.json`, `participants.tsv`, `participants.json`, `.bidsignore`. Idempotent.

#### `export_bids_mu_derivatives(spike_times, fsamp, bids_root, entity_label, pipeline_name, desc, mu_uids, mu_grid_index, grid_labels) -> dict[str, Path]`
Writes motor unit discharge times as BIDS derivatives `events.tsv` + `events.json` under `derivatives/<pipeline_name>/`. Each discharge -> one row with `onset`, `duration`, `sample`, `unit_id`, `event_type` (`motor-unit-spike`), `group` (the unit's grid, as in the raw `channels.tsv`), sorted by onset. Also writes or merges the derivative `dataset_description.json` (`GeneratedBy` with the MUedit version, `DatasetLinks` `raw` → `../../`, `SourceDatasets` `bids:raw:`) and the `desc-<desc>` row of `descriptions.tsv`.

File names and folders come from `BidsEntities` (see BIDS Layout above).

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
FFT-based notch suppressing mains harmonics without knowing the line frequency. Per-channel: compute the one-sided FFT (`scipy.fft.rfft`, which releases the GIL so the filter threads run in parallel), scan frequency bins in `NOTCH_WINDOW_HZ` (50 Hz) segments, flag bins exceeding median+5sigma, remove the interference band, reconstruct with `irfft`.

### Downsample (`downsample.py`)

| Constant | Value |
|---|---|
| `PREVIEW_MOVING_AVG_MS` | `25.0` |

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

`extension_factor(nbextchan, n_channels)` is the one place the number of delays is chosen
(`round(nbextchan / n_channels)`, at least 1). `extend_signal_mask(mask, exfactor)` marks the
`n + exfactor - 1` channels-first columns that read a masked sample; `streaming.extend_mask` is
the same rule over the first `n` columns, for samples-first streams.

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
Stage 0: preliminary bad channels (structural_only: flat/saturated)
    -> _kept_channel_rows (drop bad channels)
Stage 1: detect_artifact_masks (kept channels only)
Stage 2: detect_bad_channels_per_grid(..., keep=~artifact_mask)
    -> OR preliminary + final masks
```

**Artifact-aware bad-channel detection:** Stage 2 passes `keep=~artifact_mask`, so each grid's criteria run on its non-artifact samples only (one grid copied at a time, never the whole array), so a rest-time artifact doesn't inflate the peak/median ratio and create a false intermittent-contact flag.

### `QCPipelineResult` dataclass

| Field | Type | Shape | Description |
|---|---|---|---|
| `artifact_mask` | `np.ndarray` | `(n_samples,)` bool | True at artifact samples |
| `bad_channel_masks` | `list[np.ndarray]` | per-grid `(n_ch,)` bool | True for bad channels |

```python
def run_auto_qc(data, fsamp, grid_channel_counts, grid_coordinates=None,
                artifact_config=None, channel_qc_config=None) -> QCPipelineResult
```
Top-level orchestrator. Stage 0 runs with `structural_only=True`: only the flat and saturated metrics are computed.

---

## Bad-Channel Detection (`signal/channel_qc.py`)

Identifies defective channels across 6 criteria: flat, saturated, noisy, low-SNR, intermittent-contact, sustained contact-loss. This is **spatial** (which channels); artifact masking is **temporal** (when).

### `ChannelQCConfig`

| Field | Type | Default | Purpose |
|---|---|---|---|
| `flat_rms_ratio` | `float` | `0.20` | Flat if RMS < this x grid median RMS |
| `flat_abs_floor` | `float` | `1e-8` | Skip flat detection if grid median RMS below this |
| `sat_extreme_frac` | `float` | `0.005` | Saturated if > this fraction at min/max extreme |
| `sat_tol_frac` | `float` | `0.001` | Tolerance for extreme test (fraction of p-p range) |
| `neighbor_dist` | `float` | `1.5` | Max grid distance for "neighbour" |
| `noisy_corr_threshold` | `float` | `0.30` | Noisy if mean neighbour corr below this |
| `noisy_abs_floor` | `float` | `1e-7` | Skip noisy check if grid RMS below this |
| `snr_win_ms` | `int` | `500` | SNR window (ms) |
| `snr_thr` | `float` | `7.0` | Low-SNR if SNR (dB) below this |
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
def _channel_qc_diagnostics(data, fsamp, coordinates=None, config=None, *, structural_only=False) -> ChannelQCMetrics
```
Full-featured detector returning metrics + reasons. Computes all 6 criteria (only flat and saturated when `structural_only`) and OR-combines them into the mask. Works in float32 with float64 accumulators; the neighbour correlation is accumulated over sample blocks.

### `ChannelQCMetrics`

| Field | Type | Shape |
|---|---|---|
| `mask` | `np.ndarray` | `(n_channels,)` bool |
| `snr` | `np.ndarray` | `(n_channels,)` dB; NaN when not checked (no coordinates, fewer than `snr_min_windows` windows) |
| `mean_neighbor_corr` | `np.ndarray` | `(n_channels,)`; 1 when not checked |
| `reasons` | `list[str]` | per-channel reason strings |

```python
def detect_bad_channels_per_grid(data, fsamp, grid_channel_counts, grid_coordinates=None, config=None, *, keep=None, structural_only=False) -> list[np.ndarray]
```
Runs `_channel_qc_diagnostics` per grid on the samples `keep` marks (all when it is `None`, all-True or all-False); returns list of per-grid masks.

---

## Artifact Masking (`signal/artifact_mask.py`)

Detects artifact-contaminated sample regions (transient noise) from filtered multi-channel EMG, per grid, using a two-stage robust amplitude detector. Output is a boolean mask (`True` = artifact) used by the decomposition pipeline to skip state updates on contaminated batches.

**Algorithm:** Stage 1 computes per-channel windowed |amplitude|, max-aggregates across channels (top-k by `min_channels`), builds a robust baseline (global median + MAD, optionally a local rolling median via `local_baseline_s`), flags candidate windows via dual criterion (robust z-score OR amplitude-over-median ratio). Stage 2 computes per-channel z-scores and requires a minimum quorum of channels exceeding a per-channel z-threshold (a real artifact excites many channels; a MU burst only spikes a few). Morphological closing bridges short gaps; each run is then extended over the artifact it starts or ends (`_extend_runs`: while the top-k level stays anomalous against the global baseline, above `extend_ratio` x its level beside the run and above `extend_hold_frac` of its level inside the run, by `max_extend_s` at most each way), so an artifact longer than the local baseline is masked whole rather than at its edges; dilation pads the result. Polarity-agnostic.

**Cost:** the top-k level is a running top-k over the rows (a partition for `min_channels` > 16), and the local baseline of the per-channel windows is filtered only over spans around the candidate samples (`_moving_median_and_mad_at`), matching the whole-row filter exactly.

**Limitation:** an artifact on an abrupt (instantaneous, roughly 5x or more) step of EMG can extend over the step, up to `max_extend_s`; ramped contractions are not affected. Set `max_extend_s=0` to disable the extension.

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
| `extend_ratio` | `float` | `3.0` | A run extends while the level stays above this x its level beside the run |
| `extend_hold_frac` | `float` | `0.3` | ...and above this fraction of its level inside the run |
| `max_extend_s` | `float` | `5.0` | Maximum extension each way (s); `0` disables |

```python
def _detect_artifact_mask(data, fsamp, config=None) -> np.ndarray
```
One-grid detection. Returns `(n_samples,)` bool.

```python
def detect_artifact_masks(data, fsamp, grid_channel_counts, config=None)
    -> (per_grid_masks, global_mask)
```
Runs per grid. `global_mask` is the logical OR union across all grids.

---

## Target Segmentation (`signal/segmentation.py`)

Splits the force target a participant followed (an auxiliary channel) into the stretches the QC
page offers as decomposition windows. Levels are fractions of the target's range (p1 to p99).

**Algorithm:** the target is block-averaged to `rate_hz`, smoothed over `smooth_s`, and its slope
taken as a central difference across the smoothing width (a one-sample gradient turns noise into
slope). Each sample is labelled rest (flat and below `rest_frac`), plateau (|slope| below
`plateau_slope`), or ramp up/down. Turning points are the peaks and troughs of prominence
`turn_prominence`; a chain of them with no rest longer than `min_duration_s` and no plateau longer
than `min_plateau_s` between them, spanning at least `min_cycles` cycles, is a dynamic block,
extended by up to a quarter period over the moving samples on either side. (A rest shorter than
`min_duration_s` does not break a chain — it is folded away later anyway, and the troughs of an
oscillation that reaches the target's minimum read as one-sample rests.) A block is a `sinusoid`
when its periods (CV < `period_cv`) and swings (CV < `amplitude_cv`) are regular and a sine fits it
(R² ≥ `sine_r2`; a regular triangle wave passes), otherwise `irregular`. The fit's frequency is the
better of two estimates: the median period (robust to a stray turn, but quantized to whole samples)
and the chain's span, which covers exactly one half period per pair of turns. The fit runs over the
block, not the segment, so a short ramp folded into the segment cannot drag its R² down. Outside
the blocks, runs shorter than `min_duration_s` are folded into their longer neighbour. Rest is
dropped. Corners blur over the smoothing width, so boundaries land within about 0.1 s of the
target's.

### `SegmentationConfig`

| Field | Default | Purpose |
|---|---|---|
| `rate_hz` | `100.0` | Rate the target is analysed at |
| `smooth_s` | `0.1` | Moving-average width (s) |
| `rest_frac` | `0.05` | Level below which a flat target is rest |
| `plateau_slope` | `0.02` | Slope (range/s) below which the target is flat |
| `min_duration_s` | `0.5` | Shorter ramps and plateaus fold into a neighbour |
| `min_plateau_s` | `1.0` | A flat stretch this long ends a dynamic block |
| `turn_prominence` | `0.02` | Prominence of a turning point |
| `min_cycles` | `2.0` | Cycles a chain of reversals needs to be a block |
| `period_cv`, `amplitude_cv`, `sine_r2` | `0.15`, `0.2`, `0.9` | Sinusoid tests |

```python
def segment_target(target, fsamp, config=None) -> list[TargetSegment]
```
`TargetSegment`: `start`, `end` (EMG samples), `kind`, `direction` (ramps), `level_start`,
`level_end`, `level_min`, `level_max` (target units); sinusoids add `frequency_hz`, `amplitude`,
`offset`, `n_cycles`; both dynamic kinds carry `cycles` (the troughs). An empty, flat or
non-finite target gives `[]`.

```python
def find_target_channel(names) -> int | None
```
The first name matching `target`, `reference`, `requested` or `original` (OTB4 names the target
`<track> - Original Path`), unless it also says `performed` or `actual` (the participant's own trace).
