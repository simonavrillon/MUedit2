# 01 — Backend Architecture

## Project Metadata

| Field | Value |
|---|---|
| Package name | `muedit` |
| Version | `2.1.0` |
| Python | `>=3.11` |
| Source root | `python/src/` |
| Config | `pyproject.toml` (repo root) |

### Entry Points (pyproject.toml)

| Script | Function | Purpose |
|---|---|---|
| `muedit-api` | `muedit.cli:serve_api` | Start the FastAPI backend server |
| `muedit-decompose` | `muedit.cli:run_decomposition_cli` | Run decomposition from the command line |

### Dependencies

`numpy`, `scipy`, `matplotlib`, `xmltodict`, `pydantic`, `PyYAML`, `h5py`, `tk`, `fastapi`, `uvicorn`, `python-multipart`, `pyedflib`, `jupyterlab`, `ipykernel`

Dev: `build`, `twine`, `pytest`, `ruff`, `optuna`

---

## Boot Sequence

### API Server (`muedit api`)

```
cli.serve_api()
  → app_factory.create_app(title="MUedit API", version="2.1.0")
      → FastAPI(...)
      → CORSMiddleware (allow_origins=["*"], methods=["*"], headers=["*"])
      → errors.register_exception_handlers(app)
  → routes.include_routers(app)
      → app.include_router(preview_router)    # /api/v1: health, preview, qc
      → app.include_router(decompose_router)  # /api/v1: decompose, stream
      → app.include_router(editing_router)    # /api/v1: config, edit/*
      → app.include_router(dialog_router)   # /api/v1/dialog: open-file
  → uvicorn.run(app, host, port)
      → host: MUEDIT_HOST env (default 0.0.0.0)
      → port: MUEDIT_PORT or MUEDIT_BACKEND_PORT env (default 8000)
      → log_level: warning, access_log: off
```

### CLI Decomposition (`muedit decompose`)

```
cli.run_decomposition_cli(argv)
  → argparse: filepath, --duration, --roi, --rois, --niter, --sil-thr,
              --postprocess (windowed|full-trace|adaptive),
              --bids-root, --subject, --task, --session, --run, etc.
  → decomp.pipeline.run_decomposition(filepath, ...)
  → prints summary + save path
```

---

## Package Exports

### `muedit.__init__`

```python
__all__ = [
    "DecompositionParameters",   # decomp.types
    "load_signal",               # io.factory
    "register_loader",           # io.factory
    "run_decomposition",         # decomp.pipeline
]
```

### Sub-package `__all__`

| Package | Exports |
|---|---|
| `muedit.decomp` | `DecompositionParameters`, `run_decomposition` |
| `muedit.io` | `LoaderFn`, `clone_signal`, `get_loader`, `load_signal`, `register_loader`, `supported_extensions` |
| `muedit.signal` | `bandpass_signals`, `demean`, `format_hdemg_signal`, `notch_signals`, `run_auto_qc`, `QCPipelineResult` |
| `muedit.editing` | `FilterUpdateResult`, `SpikeTimes`, `add_artifact_in_roi`, `add_spikes_in_roi`, `delete_artifacts_in_roi`, `delete_high_discharge_rate_spikes_in_roi`, `delete_spikes_in_roi`, `remove_discharge_rate_outliers`, `update_motor_unit_filter_window` |
| `muedit.adapt_decomp` | `AdaptiveDecomp`, `Config`, `run_adaptive_decomposition` |
| `muedit.api` | (none — empty, just a package marker) |

---

## Data Models (`models.py`)

All models are `@dataclass`.

### `SignalImport`
Raw EMG signal + metadata loaded from a recording file.

| Field | Type | Default |
|---|---|---|
| `data` | `np.ndarray` | (required) |
| `fsamp` | `float` | (required) |
| `gridname` | `list[str]` | `[]` |
| `muscle` | `list[str]` | `[]` |
| `auxiliary` | `np.ndarray` | `np.zeros((0, 0))` |
| `auxiliaryname` | `list[str]` | `[]` |
| `metadata` | `dict[str, Any]` | `{}` |

Methods: `from_mapping(payload)`, `clone()`, `to_dict()`

### `LoadedDecomposition`
Decomposition state loaded from `.npz`/`.mat` for editing.

| Field | Type | Default |
|---|---|---|
| `pulse_trains_full` | `list[list[float]]` | `[]` |
| `distime_all` | `list[list[int]]` | `[]` |
| `fsamp` | `float | None` | `None` |
| `grid_names` | `list[str]` | `[]` |
| `total_samples` | `int` | `0` |
| `mu_grid_index` | `list[int]` | `[]` |
| `rois` | `list[tuple[int, int]]` | `[]` |
| `parameters` | `dict[str, Any]` | `{}` |
| `muscle` | `list[str]` | `[]` |
| `sil` | `list[float]` | `[]` |

Methods: `to_dict()`

### `DecompositionSignalExport`
EMG signal paired with decomposition output (MATLAB-compatible keys).

| Field | Type |
|---|---|
| `data` | `np.ndarray` |
| `fsamp` | `float` |
| `pulse_t` | `np.ndarray` |
| `discharge_times` | `list[np.ndarray]` |

Methods: `to_dict()` — emits keys `"PulseT"`, `"Dischargetimes"` for MATLAB compatibility.

### `DecompositionExport`
Full decomposition output with SIL scores and frontend preview.

| Field | Type |
|---|---|
| `signal` | `DecompositionSignalExport` |
| `parameters` | `dict[str, Any]` |
| `grid_names` | `list[str]` |
| `sil` | `list[float]` |
| `discard_channels` | `list[np.ndarray]` |
| `coordinates` | `list[np.ndarray]` |
| `mu_grid_index` | `list[int]` |
| `preview` | `dict[str, Any]` |

Methods: `to_dict()`

---

## Module Responsibilities

### `api/` — HTTP API Layer

| Component | Responsibility |
|---|---|
| `app_factory.py` | Construct FastAPI app, configure CORS, register exception handlers |
| `routes/__init__.py` | Register all routers on the app |
| `routes/preview.py` | File preview, QC window, on-demand auto-QC, health check |
| `routes/decompose.py` | Synchronous + streaming decomposition |
| `routes/editing.py` | All edit endpoints (load, save, filter update, spike/artifact ops) |
| `routes/dialog.py` | Native file-open dialog (macOS AppleScript / tkinter) |
| `services/preview_service.py` | Preview building, QC window binary encoding, on-demand auto-QC |
| `services/decompose_service.py` | Decomposition orchestration, NDJSON streaming, binary preview |
| `services/editing_service.py` | Edit operation dispatch, BIDS save, MAT signal context management |
| `services/bids_helpers.py` | BIDS sidecar parsing, entity label resolution |
| `services/edit_helpers.py` | Payload normalization helpers for edits |
| `schemas.py` | Pydantic request models (9 models) |
| `contracts.py` | `success_payload()` response envelope |
| `binary.py` | `pack_json_f32_payload()` binary wire format packer |
| `cache.py` | In-memory TTL cache (4 caches, thread-safe, budget-based eviction) |
| `common.py` | JSON parsing, param building, serialization, temp file management |
| `config.py` | `DATA_ROOT`, `resolve_bids_root()` |
| `errors.py` | Error envelope, exception handlers |

### `decomp/` — Decomposition Engine

| Component | Responsibility |
|---|---|
| `pipeline.py` | `run_decomposition()` — orchestrates 5 stages |
| `core.py` | `decompose_step()` — ICA loop over grids/windows |
| `algorithm.py` | FastICA, whitening, spike extraction, silhouette, dedup, peel-off |
| `preprocess.py` | Load, filter, ROI resolution, BIDS export, QC integration |
| `postprocess.py` | Filter application, dedup, export, NPZ save |
| `adaptive_batch.py` | Online adaptive post-processing (bidirectional) |
| `preview.py` | Downsampled preview payload builder |
| `io.py` | Decomposition file load/save (NPZ + MAT v5/v7.3) |
| `types.py` | `DecompositionParameters` + step output dataclasses |

### `io/` — File I/O

| Component | Responsibility |
|---|---|
| `factory.py` | Loader registry, dispatch by extension, `load_signal()`, `clone_signal()` |
| `loaders.py` | Thin re-export of all 5 format loaders |
| `bids.py` | BIDS EMG export (EDF/BDF + sidecars + derivatives) |
| `_bids_reader.py` | BIDS EMG reading via pyedflib + channels.tsv |
| `_intan.py` | Intan RHD loader (3 save layouts: traditional, per-channel, per-signal-type) |
| `_mat.py` | MATLAB .mat v5 (scipy) + v7.3 (h5py/HDF5) loader |
| `_otb.py` | OT Bioelettronica OTB+ and OTB4 archive loaders |

### `signal/` — Signal Processing

| Component | Responsibility |
|---|---|
| `filters.py` | `demean()`, `bandpass_signals()`, `notch_signals()` |
| `downsample.py` | `raw_series_at_fs()`, `moving_average_ms()` |
| `decomp_primitives.py` | `extend_signal()`, `signed_square()`, `find_refractory_peaks()`, `split_by_amplitude()`, `isi_cov()` |
| `grid.py` | `GridSpec` catalog, `format_hdemg_signal()`, `get_grid_electrode_metadata()` |
| `channel_qc.py` | Bad-channel detection (7 criteria: flat, saturated, quantized, noisy, low-SNR, intermittent, contact-loss) |
| `artifact_mask.py` | Artifact region detection (two-stage robust amplitude detector with local baseline) |
| `qc_pipeline.py` | `run_auto_qc()` — orchestrates channel QC + artifact detection |

### `editing/` — Motor-Unit Editing

| Component | Responsibility |
|---|---|
| `operations.py` | All MU editing operations: filter update, spike add/delete, artifact add/delete, discharge-rate pruning, outlier removal |

### `adapt_decomp/` — Adaptive Online Decomposition

| Component | Responsibility |
|---|---|
| `config.py` | `Config` dataclass with adaptation hyper-parameters |
| `adaptation.py` | `AdaptiveDecomp` class — online whitening + separation vector + spike centroid adaptation |

---

## Config

### `api/config.py`

| Constant | Source | Description |
|---|---|---|
| `DATA_ROOT` | `MUEDIT_DATA_ROOT` env or `<repo_root>/data` | Root directory for BIDS output |
| `_REPO_ROOT` | 4 levels up from `config.py` | Computed repo root |

```python
def resolve_bids_root(project: str | None) -> Path
```
Returns `DATA_ROOT / project` (or `DATA_ROOT / "muedit_out"` if project is empty).

### Environment Variables

| Variable | Default | Used by |
|---|---|---|
| `MUEDIT_DATA_ROOT` | `<repo>/data` | BIDS output root |
| `MUEDIT_HOST` | `0.0.0.0` | API server bind host |
| `MUEDIT_PORT` | `8000` | API server bind port |
| `MUEDIT_BACKEND_PORT` | `8000` | Fallback API port |
