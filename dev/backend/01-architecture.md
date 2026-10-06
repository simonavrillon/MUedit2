# 01 — Backend Architecture

## Project Metadata

| Field | Value |
|---|---|
| Package name | `muedit` |
| Version | `2.1.0` |
| Python | `>=3.11.4` |
| Source root | `python/src/` |
| Config | `pyproject.toml` (repo root) |

### Entry Points (pyproject.toml)

| Script | Function | Purpose |
|---|---|---|
| `muedit-api` | `muedit.cli:serve_api` | Start the server: the API, and the frontend at `/` |
| `muedit-decompose` | `muedit.cli:run_decomposition_cli` | Run decomposition from the command line |

### Dependencies

`numpy`, `scipy`, `xmltodict`, `pydantic`, `PyYAML`, `h5py`, `fastapi`, `starlette`, `uvicorn`, `python-multipart`, `pyedflib`, `platformdirs`

Extras: `dev` (`build`, `twine`, `pytest`, `pytest-cov`, `httpx`, `ruff`, `mypy`, `pre-commit`, `types-PyYAML`), `plot` (`matplotlib`, for the CLI's `--manual-roi` only), `notebook` (`jupyterlab`, `ipykernel`), `research` (`optuna`, `matplotlib`). All versions are pinned in `uv.lock`.

---

## Boot Sequence

### API Server (`muedit api`)

```
cli.serve_api()
  → app_log.log_to_file(<log dir>/muedit.log): rotated, 5 MB × 3; spawned
    workers append to it through MUEDIT_LOG_FILE
  → app_factory.create_app(title="MUedit API", version="2.1.0", allowed_hosts)
      → FastAPI(...)
      → no CORS middleware: the page and the API share one origin, so no other
        origin may call the API
      → TrustedHostMiddleware (localhost, 127.0.0.1), only when bound to loopback:
        rejects DNS-rebound requests, which arrive same-origin
      → errors.register_exception_handlers(app)
      → lifespan: at startup purge_stale_sessions() (store folders of exited
        processes), purge_old_logs() (edit logs > 30 days), BUDGET.start_sweeper();
        at shutdown stop_decompositions(), stop the sweeper, BUDGET.clear()
  → routes.include_routers(app)
      → app.include_router(preview_router)    # /api/v1: health, preview-by-path, qc/auto
      → app.include_router(series_router)     # /api/v1: series/emg, series/overview, series/aux, series/pulse
      → app.include_router(decompose_router)  # /api/v1: decompose_stream, decompose/cancel
      → app.include_router(editing_router)    # /api/v1: edit/session/*, edit/ops/{op}, edit/save
      → app.include_router(dialog_router)   # /api/v1/dialog: open-file
      → app.include_router(memory_router)   # /api/v1: debug/memory, session/close
  → app_factory.mount_frontend(app, paths.frontend_dir())
      → StaticFiles at / (after the routers, which it would otherwise shadow),
        Cache-Control: no-cache so a pulled update is never stale
  → uvicorn.run(app, host, port)
      → host: MUEDIT_HOST env (default 127.0.0.1)
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

The names are imported on first use (module `__getattr__`), so `import muedit.paths`
does not load SciPy.

### Sub-package `__all__`

| Package | Exports |
|---|---|
| `muedit.decomp` | `DecompositionParameters`, `run_decomposition` |
| `muedit.io` | `LoaderFn`, `get_loader`, `load_signal`, `register_loader`, `supported_extensions` |
| `muedit.signal` | `bandpass_signals`, `demean`, `format_hdemg_signal`, `notch_signals`, `run_auto_qc`, `QCPipelineResult` |
| `muedit.editing` | `FilterUpdateResult`, `SpikeTimes`, `add_artifact_in_roi`, `add_spikes_in_roi`, `delete_artifacts_in_roi`, `delete_high_discharge_rate_spikes_in_roi`, `delete_spikes_in_roi`, `remove_discharge_rate_outliers`, `update_motor_unit_filter_window` |
| `muedit.adapt_decomp` | `AdaptiveDecomp`, `Config`, `run_adaptive_decomposition` |
| `muedit.api` | (none — empty, just a package marker) |

---

## Data Models (`models.py`)

All models are `@dataclass`.

### Array types
Named NumPy array types shared by the signal-processing code. They fix the
element type only; shapes are documented by parameter names and docstrings.

| Alias | Definition | Used for |
|---|---|---|
| `FloatArray` | `NDArray[np.floating[Any]]` | EMG samples, pulse trains, filters, whitening matrices, coordinates |
| `IntArray` | `NDArray[np.integer[Any]]` | Discharge times, peaks, spike rasters, 0/1 discard flags |
| `BoolArray` | `NDArray[np.bool_]` | Artifact masks, bad-channel masks |

### `SignalImport`
Raw EMG signal + metadata loaded from a recording file. `load_signal()` returns
it, and it is passed as-is through the pipeline and the upload cache.

| Field | Type | Default |
|---|---|---|
| `data` | `FloatArray` | (required) |
| `fsamp` | `float` | (required) |
| `gridname` | `list[str]` | `[]` |
| `muscle` | `list[str]` | `[]` |
| `auxiliary` | `FloatArray` | `np.zeros((0, 0))` |
| `auxiliaryname` | `list[str]` | `[]` |
| `metadata` | `dict[str, Any]` | `{}` |

Methods: `from_mapping(payload)` (normalizes loader dicts), `clone()`, `to_dict()`, `nbytes` (property)

### `EditSignalContext`
EMG embedded in a decomposition file (`.mat`/`.npz`), built by
`load_decomposition()` and held by the `EditSession` for filter updates and BIDS export.
The editing service passes `load_decomposition()` the session's `SessionStore`, so the EMG is a
float32 memory map in that store (or a `.npz` member mapped in place), which closing the
session deletes.

| Field | Type | Default |
|---|---|---|
| `data` | `FloatArray` | (required); `(0, 0)` when the file holds only a mask |
| `fsamp` | `float` | (required); `0.0` when unknown |
| `grid_names` | `list[str]` | `[]` |
| `emgmask` | `list[IntArray]` | `[]` (per grid, 1 = discarded) |
| `coordinates` | `list[FloatArray]` | `[]` |
| `ied` | `list[float] \| None` | `None` |
| `aux_data` | `FloatArray \| None` | `None` |
| `aux_names` | `list[str]` | `[]` |
| `artifact_mask` | `BoolArray \| None` | `None` |
| `loader_meta` | `dict[str, Any]` | `{}` (the `LOADER_BIDS_META_KEYS` the file recorded) |
| `prefiltered` | `bool` | `False`; `True` for a schema v1 `.npz`, whose EMG is notch- and bandpass-filtered: the filter update skips its bandpass and the BIDS export skips it |

Methods: `readonly_view()` (shares arrays read-only), `nbytes` (property; memory-mapped EMG/aux count 0)

### `LoadedDecomposition`
Decomposition state loaded from `.npz`/`.mat` for editing. Returned by `load_decomposition()`
(or `load_decomposition_file()` without the EMG); `open_edit_session` builds the
`EditSession` from it.

| Field | Type | Default |
|---|---|---|
| `pulse_trains_full` | `FloatArray` `(n_mu, total_samples)` | empty `(0, 0)` |
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
| `app_factory.py` | Construct FastAPI app, Host check, exception handlers; `mount_frontend()` |
| `routes/__init__.py` | Register all routers on the app |
| `routes/preview.py` | File preview, on-demand auto-QC, health check |
| `routes/series.py` | Viewport envelopes of the upload's EMG, grid overview and aux channels, and of an MU's pulse train |
| `routes/decompose.py` | Streaming decomposition, cancel, binary preview fetch |
| `routes/editing.py` | Edit sessions (open, state, operations, recovery, save) and the run save |
| `routes/dialog.py` | Native file-open dialog (macOS AppleScript / tkinter) |
| `routes/memory.py` | `/session/close` (a closing tab) and `/debug/memory` |
| `services/preview_service.py` | Preview building, on-demand auto-QC |
| `services/series_service.py` | One bandpass pass building the QC pyramids; serves `/series/*` frames, pulse trains included |
| `services/decompose_service.py` | One run at a time (409), cancel, worker supervision, NDJSON streaming, binary preview |
| `services/decompose_worker.py` | Run body, executed in a `spawn` worker process |
| `services/editing_service.py` | Opens edit sessions, applies edits, recovers unsaved edits, both saves + BIDS export |
| `services/bids_helpers.py` | BIDS sidecar parsing, entity label resolution |
| `services/edit_helpers.py` | Payload normalization helpers for edits |
| `schemas.py` | Pydantic request models (7 models) |
| `contracts.py` | `success_payload()` response envelope |
| `binary.py` | `pack_frame()` / `unpack_frame()`: the MUB1 wire format |
| `cache.py` | The four session-scoped caches (uploads, previews, runs, edit sessions) |
| `memory.py` | `MemoryBudget` (one byte budget, idle-session sweep) and `BudgetedLRU` (one cache) |
| `common.py` | JSON parsing, param building, serialization, path checks |
| `config.py` | `DATA_ROOT`, `resolve_bids_root()`, `project_of()` |
| `errors.py` | Error envelope, exception handlers |

### Top-level modules

| Component | Responsibility |
|---|---|
| `cli.py` | `muedit api` / `muedit decompose` entry points |
| `paths.py` | Per-user cache and log folders (`platformdirs`), the checkout root, the frontend folder |
| `app_log.py` | The server's rotating log file, shared with its spawned workers |

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
| `decomposition_file.py` | Decomposition file load/save (NPZ schema v2, legacy v1 reader, MAT v5/v7.3) |
| `types.py` | `DecompositionParameters` + step output dataclasses |

### `io/` — File I/O

| Component | Responsibility |
|---|---|
| `factory.py` | Loader registry, dispatch by extension, `load_signal()` |
| `loaders.py` | Thin re-export of all 5 format loaders |
| `bids.py` | BIDS EMG export (EDF/BDF + sidecars + derivatives) |
| `_bids_reader.py` | BIDS EMG reading via pyedflib + channels.tsv |
| `_intan.py` | Intan RHD loader (3 save layouts: traditional, per-channel, per-signal-type) |
| `mat.py` | MATLAB .mat v5 (scipy) + v7.3 (h5py/HDF5) loader |
| `_otb.py` | OT Bioelettronica OTB+ and OTB4 archive loaders |
| `npz.py` | `NpzWriter` (aligned, uncompressed, written row block by row block), `NpzArchive` (one open, members memory-mapped), the restricted unpickler for legacy files |
| `store.py` | `SessionStore`: a folder of memory-mapped `.npy` files per upload, run or edit session; `RamStore` on the heap |

### `signal/` — Signal Processing

| Component | Responsibility |
|---|---|
| `filters.py` | `demean()`, `bandpass_signals()`, `notch_signals()`, and their in-place, row-block versions |
| `downsample.py` | `moving_average_ms()` |
| `pyramid.py` | `MinMaxPyramid` (min/max levels at bins of 16, 64, 256, … samples) and `view()`, the envelope of any window |
| `streaming.py` | `StreamedExtender`: the extended signal read batch by batch instead of whole |
| `decomp_primitives.py` | `extend_signal()`, `signed_square()`, `find_refractory_peaks()`, `split_by_amplitude()`, `isi_cov()` |
| `grid.py` | `GridSpec` catalog, `format_hdemg_signal()`, `get_grid_electrode_metadata()` |
| `channel_qc.py` | Bad-channel detection (6 criteria: flat, saturated, noisy, low-SNR, intermittent, contact-loss) |
| `artifact_mask.py` | Artifact region detection (two-stage robust amplitude detector with local baseline) |
| `qc_pipeline.py` | `run_auto_qc()` — orchestrates channel QC + artifact detection |

### `editing/` — Motor-Unit Editing

| Component | Responsibility |
|---|---|
| `operations.py` | All MU editing operations: filter update, spike add/delete, artifact add/delete, discharge-rate pruning, outlier removal |
| `session.py` | `EditSession`: the decomposition being edited, its undo stack and pulse-train copies |
| `edit_log.py` | `EditLog`: each session's operations on disk, replayed to recover unsaved edits |

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
| `DATA_ROOT` | `default_data_root()` at import | Output folder; each project is a folder in it |

`default_data_root()` takes, in order: `MUEDIT_DATA_ROOT`; `<repo>/data` when running
from a checkout; `MUedit` in the Documents folder.

```python
def resolve_bids_root(project: str | None) -> Path
```
Returns `DATA_ROOT / project` (or `DATA_ROOT / "muedit_out"` if project is empty).
Raises `ValueError` unless `project` is a single folder name (no separator of either
OS, not `.` or `..`, not absolute); `common.bids_root_for()` turns that into a 400 on
the `project` field.

```python
def project_of(bids_root: Path) -> str
```
The first folder of `bids_root` under `DATA_ROOT`, or `""` outside it. The preview
returns it as `project` for a file inside a BIDS dataset, and the edit session uses it
to tell whether the Project field still names the file's own dataset: while it does, a
save goes back into that dataset (`EditSession.bids_root`), wherever it lies.

### Environment Variables

| Variable | Default | Used by |
|---|---|---|
| `MUEDIT_DATA_ROOT` | `<repo>/data` | Output folder |
| `MUEDIT_HOST` | `127.0.0.1` | Server bind host; `0.0.0.0` opens it to the network and drops the Host check |
| `MUEDIT_PORT` | `8000` | Server bind port |
| `MUEDIT_BACKEND_PORT` | `8000` | Fallback port; the launchers copy it into `MUEDIT_PORT` |
| `MUEDIT_OPEN_BROWSER` | `1` | Open the browser once the server answers (launchers) |
| `MUEDIT_LOG_FILE` | set by `serve_api` | The log file its spawned workers append to |
| `MUEDIT_CACHE_DIR` | the per-user cache folder | Session stores, edit logs |
| `MUEDIT_CACHE_BUDGET_MB` | 10% of RAM, 256–1024 | Byte budget of the API caches |
| `MUEDIT_DISK_RESERVE_MB` | `1024` | Free disk a session store leaves; arrays past it stay in RAM |
| `MUEDIT_NO_UV` | `0` | `1` makes the launchers use the active `python` instead of `uv run` |
