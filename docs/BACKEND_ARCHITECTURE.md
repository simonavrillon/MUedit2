# Backend Architecture

## Scope

Backend runtime code lives in `python/src/muedit` and is organized as:

- FastAPI API layer (`muedit/api`)
- Application services (`muedit/services`)
- Decomposition pipeline (`muedit/decomp`)
- Edit and signal utilities (`muedit/editing.py`, `muedit/signal`, `muedit/io`, `muedit/export`)

`python/src/adapt_decomp` is present as auxiliary/legacy decomposition support used by adaptive processing paths.

## Runtime Entrypoints

- `muedit.cli:serve_api`
  - Creates FastAPI app (`api/app_factory.py`)
  - Attaches routers (`api/routes/__init__.py`)
  - Starts Uvicorn

- `muedit.cli:run_decomposition_cli`
  - Runs decomposition pipeline directly from CLI args
  - Supports ROI selection, app-aligned decomposition parameter flags, and optional BIDS export/session flags

Console scripts (from `pyproject.toml`):

- `muedit-api`
- `muedit-decompose`

## Layer Map

- `muedit/api/`
  - HTTP interface only: routers, request schemas, parsing helpers, cache, error contracts.
  - Main modules:
    - `api/routes/*.py`
    - `api/schemas.py`
    - `api/common.py`
    - `api/cache.py`
    - `api/contracts.py`
    - `api/errors.py`

- `muedit/services/`
  - Route-facing orchestration for preview, decomposition, and edit workflows.
  - Handles transport shaping (JSON vs binary), input resolution, and cleanup policy.

- `muedit/decomp/`
  - Pipeline implementation:
    - `pipeline.py`: top-level stage orchestration
    - `preprocess.py`: load formatting, filtering, ROI resolution, optional raw BIDS export
    - `core.py`: ICA/decomposition loop
    - `postprocess.py`: batch pulse extraction, deduplication, preview build, optional decomp export

- `muedit/io/`, `muedit/export/`, `muedit/signal/`, `muedit/models.py`, `muedit/utils.py`
  - Shared IO, BIDS read/write, filtering, typed models, and signal helpers.

- `muedit/editing.py`
  - Pure edit operations applied by editing services (add/delete spikes, outliers, filter-window updates).

## Dependency Direction

Expected direction:

1. `api/routes/*` depends on `api/schemas`, `api/contracts`, and `services/*`.
2. `services/*` may depend on `api/common`, `api/cache`, and domain modules (`decomp`, `editing`, `export`, `io`).
3. `decomp/*` depends on lower-level signal/IO/export utilities, not on API routes.
4. `editing.py`, `signal/*`, `io/*`, `export/*` stay reusable and API-agnostic.

Boundary rule: keep HTTP concerns (request/response objects, status codes) in `api/*` and service entrypoints, not in decomposition/edit primitives.

## API Surface

Routers mount under `/api/v1`:

- Preview and QC:
  - `GET /health`
  - `POST /preview`
  - `POST /preview-by-path`
  - `POST /qc/window`

- Decomposition:
  - `POST /decompose`
  - `POST /decompose_stream` (NDJSON stream)
  - `GET /decompose_preview/{token}` (binary preview token fetch)

- Editing:
  - `POST /edit/load`
  - `POST /edit/load-by-path`
  - `POST /edit/save`
  - `POST /edit/update-filter`
  - `POST /edit/add-spikes`
  - `POST /edit/delete-spikes`
  - `POST /edit/delete-dr`
  - `POST /edit/remove-outliers`
  - `POST /edit/flag-mu`

- Dialog:
  - `GET /dialog/open-file`

## Pipeline Stage Responsibilities

- `load_step`
  - Load signal from file path or preloaded upload-token cache.

- `preprocess_step`
  - Format grid/channel structure.
  - Apply notch + bandpass filters.
  - Resolve ROI list (explicit ROIs, interactive, duration, or full signal).
  - Optionally export preprocessed raw EMG into BIDS.

- `decompose_step`
  - Run iterative separator discovery per grid and per ROI window.
  - Compute spikes and SIL metrics.
  - Emit periodic progress events (for stream mode).

- `postprocess_step`
  - Batch-reconstruct pulse trains and discharge times.
  - Remove duplicates (within-grid, optionally cross-grid).
  - Optionally persist decomposition output in BIDS tree.

- `export_step`
  - Build response payload (summary + preview) and optional persisted output path.

## Transport And Cache Model

- Default response envelope:
  - Success: `{ "data": ..., "meta": { "api_version": "v1" } }`
  - Error: canonical `error` envelope from `api/errors.py`

- Binary payloads are supported for large arrays:
  - QC raw windows: `qc-raw-f32-v1` (`MQCR` header)
  - Edit load: `edit-load-f32-v1` (`MELD` header)
  - Streamed decompose preview token fetch: `decompose-preview-f32-v1` (`MDPV` header)

- Short-lived in-memory caches (`api/cache.py`):
  - Upload signal cache (tokenized source signal snapshots)
  - QC signal cache (filtered/channel-indexed arrays)
  - Binary decompose preview cache (tokenized payload blobs)
  - TTL + bounded-item eviction policy is enforced centrally.

## Practical Maintenance Notes

1. Keep endpoint logic thin; route handlers should delegate to services.
2. Keep request parsing/validation concerns in `api/schemas.py` and `api/common.py`.
3. Add orchestration in `services/*`, not inside `decomp/*` algorithm modules.
4. Preserve stage boundaries (`load -> preprocess -> decompose -> postprocess -> export`) instead of introducing cross-stage side effects.
5. Update this document at module/responsibility level rather than maintaining function-by-function inventories.
