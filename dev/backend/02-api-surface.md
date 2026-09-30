# 02 — API Surface

All routers use prefix `/api/v1` (dialog uses `/api/v1/dialog`). All JSON responses are wrapped in the canonical v1 envelope: `{"data": <data>, "meta": {"api_version": "v1"}}`. Errors use `{"error": {"code": ..., "message": ..., "detail": ...}}`.

The same server serves `frontend/` at `/` (after the routers), so the page and the API share one
origin and no CORS headers are sent. In the desktop app, every `/api/*` request except `/health`
must carry the app's token in `X-MUedit-Token`; without it the answer is 401 with code
`unauthorized`. A `project` that is not a single folder name is refused with a 400 on the
`project` field (`/decompose_stream` with `bids_export`, `/edit/save`, `/edit/session/save`).

---

## Routes

The non-streaming `POST /decompose`, `GET /config`, and the multipart upload routes `POST /preview` and `POST /edit/load` were removed as unreachable from the frontend (structural audit F4). Signals and decompositions are always opened by server path; `/decompose_stream` only accepts an `upload_token` minted by `/preview-by-path`.

### Preview Router (`routes/preview.py`)

| Method | Path | Accepts | Returns | Service |
|---|---|---|---|---|
| GET | `/health` | — | `{status: "ok"}` | — |
| POST | `/preview-by-path` | JSON: `PathPayload` | JSON: `{upload_token, grid_names, total_samples, fsamp, channel_means, coordinates, metadata, muscle, auxiliary_names}` + BIDS sidecar metadata and `project` (`config.project_of` of the file's BIDS root) when the file lies in a BIDS dataset | `build_preview_from_path(path)` |
| POST | `/qc/auto` | JSON: `QcAutoPayload` | JSON: `{bad_channels_per_grid, artifact_regions, artifact_samples, total_samples, fsamp, grid_names}` | `run_auto_qc_on_token(payload)` |

The preview bandpasses the grid channels once, a few rows at a time, and keeps in the upload's
session store what the QC stage draws. That is min/max pyramids of the bandpassed EMG, of each
grid's smoothed mean |EMG| (the overview) and of the aux channels, with levels at bins of 16, 64,
256, … samples. The bandpassed EMG itself is not stored. `/qc/auto` bandpasses it again into a
temporary store file.

### Series Router (`routes/series.py`)

| Method | Path | Accepts | Returns | Service |
|---|---|---|---|---|
| GET | `/series/emg` | query: `upload_token`, `grid`, `start`, `end` (0 = end), `bins` (1–8192, default 1024) | MUB1 frame of one grid's bandpassed EMG | `series_frame("emg", …)` |
| GET | `/series/overview` | query: `upload_token`, `start`, `end`, `bins` | MUB1 frame, one row per grid | `series_frame("overview", …)` |
| GET | `/series/aux` | query: `upload_token`, `start`, `end`, `bins` | MUB1 frame, one row per aux channel | `series_frame("aux", …)` |

A series frame holds `min` and `max` `f4[rows, bins]`, served from the coarsest pyramid level
with at least one level bin per output bin. When the window has no more samples than bins, it
holds `samples` `f4[rows, end − start]` instead (for the EMG, bandpassed on demand with 1 s of
padding). Its meta has `kind` (`envelope` | `samples`), `start`, `end`, `bins`, `factor`
(samples per source bin), `total_samples`, `fsamp` and `names`. An output bin holds every sample
of its range plus at most one level bin of its neighbours.

### Decompose Router (`routes/decompose.py`)

| Method | Path | Accepts | Returns | Service |
|---|---|---|---|---|
| POST | `/decompose_stream` | form fields: `upload_token` (required; 400 with `field: upload_token` when missing or expired), `params`, `duration`, `persist_output`, `roi_start: int`, `roi_end: int`, `rois` (JSON str), `discard_channels`, `bids_export: bool`, `project: str`, `bids_entities` (JSON str), `bids_metadata` (JSON str), `full_preview`, `artifact_regions` (JSON str) | `StreamingResponse` NDJSON (`application/x-ndjson`); 409 while another run is active | `start_decomposition()` + `decomposition_event_stream()` |
| POST | `/decompose/cancel` | — (the session header names the run) | JSON: `{cancelled: bool}`; false when this session has no run | `cancel_decomposition(session)` |
| GET | `/decompose_preview/{token}` | path param `token: str` | MUB1 frame (`x-muedit-format: mub1`); served once, then 404 | `fetch_decompose_preview_binary(token)` |

The server runs one decomposition at a time, in a worker process started with `spawn`
(`decompose_worker.child_main`). The worker opens the upload's memory-mapped files and writes the
run's arrays into a run store the server created, so only file locations cross between the
processes. Progress comes back over a pipe, and the NDJSON events are the same as they were with
the old in-process thread. The stream ends with `done`, `error` (also when the worker process dies),
or `cancelled`. A run is cancelled by `/decompose/cancel`, by the client disconnecting, by
`/session/close` for its session, and at server shutdown. `MUEDIT_DECOMPOSE_WORKER=thread` runs the
decomposition on a thread instead. In that mode a cancel takes effect at the next progress event.

Header `x-muedit-binary` (default `"1"`) controls binary vs JSON preview encoding in stream mode:
the MUB1 frame carries the discharge times as CSR, the JSON fallback as lists. Neither carries
pulse trains. When the run has a full-length pulse matrix (`full_preview`), the `done` event's
preview carries `run_result_token`: the server keeps the matrix and the discharge times (the
session's latest run, until the session closes) for `/series/pulse`, `/spikes` and the run save,
which sends the token instead of the data. A save naming a token the server no longer holds, and
no discharge times, is refused with 400 (`field: run_result_token`).

### Editing Router (`routes/editing.py`)

| Method | Path | Accepts | Returns | Service |
|---|---|---|---|---|
| POST | `/edit/load-by-path` | JSON: `PathPayload`; header `x-muedit-binary` | JSON or MUB1 frame (`x-muedit-format: mub1`) | `load_decomposition_binary_from_path(path)` / `load_decomposition_from_path(path)` |
| POST | `/edit/save` | JSON `EditSavePayload`, or a MUB1 frame (`application/x-muedit-frame`) whose meta is that JSON and whose `pulse_trains` array is f4 `(n_mu, total_samples)` | JSON: `{saved, path, kept_indices, mu_uids, edit_history, bids_emg_paths?, bids_deriv_paths?}` | `save_edits(payload, pulse_trains)` |
| POST | `/edit/update-filter` | JSON: `EditFilterPayload` | JSON: `{fsamp, distimes, pulse_train}` | `update_filter(payload)` |
| POST | `/edit/add-spikes` | JSON: `EditRoiPayload` | JSON: `{distimes}` | `add_spikes(payload)` |
| POST | `/edit/add-artifact` | JSON: `EditRoiPayload` | JSON: `{artifact_times}` | `add_artifact(payload)` |
| POST | `/edit/delete-spikes` | JSON: `EditRoiPayload` | JSON: `{distimes, artifact_times?}` | `delete_spikes(payload)` |
| POST | `/edit/delete-dr` | JSON: `EditRoiPayload` | JSON: `{distimes}` | `delete_dr(payload)` |
| POST | `/edit/remove-outliers` | JSON: `EditOutliersPayload` | JSON: `{distimes, removed_count}` | `remove_outliers(payload)` |
| POST | `/edit/remove-duplicates` | JSON: `EditDeduplicatePayload` | JSON: `{kept_indices, distimes, removed_count}` | `remove_duplicates_service(payload)` |
| POST | `/edit/flag-mu` | JSON: `EditFlagPayload` | JSON: `{flagged: bool}` | `flag_mu(payload)` |

### Dialog Router (`routes/dialog.py`)

| Method | Path | Accepts | Returns | Service |
|---|---|---|---|---|
| GET | `/open-file` | — | JSON: `{path: str|None, name: str|None}` | `_open_dialog_macos()` (AppleScript) or `_open_dialog_tkinter()` (subprocess) |

Accepted extensions: `mat, otb+, otb4, npz, bdf, edf, rhd`. Returns 408 on timeout, 500 on failure.

---

## Request Schemas (`schemas.py`)

All models are Pydantic `BaseModel`.

### `PathPayload`
| Field | Type |
|---|---|
| `path` | `str` |

### `QcAutoPayload`
| Field | Type | Default |
|---|---|---|
| `upload_token` | `str` | (required) |

### `EditSavePayload`
| Field | Type | Default |
|---|---|---|
| `distimes` | `list[list[int]] \| None` | `None` |
| `discharge_times` | `list[list[int]] \| None` | `None` (alias for `distimes`) |
| `flagged` | `list[bool] \| None` | `None` |
| `remove_flagged` | `bool \| None` | `None` |
| `remove_duplicates` | `bool \| None` | `None` |
| `pulse_trains` | `list[list[float]] \| None` | `None` |
| `total_samples` | `int` | (required) |
| `fsamp` | `float \| None` | `None` |
| `grid_names` | `list[str] \| None` | `None` |
| `mu_grid_index` | `list[int] \| None` | `None` |
| `mu_uids` | `list[str] \| None` | `None` |
| `parameters` | `dict[str, Any] \| None` | `None` |
| `muscle` | `list[str] \| str \| None` | `None` |
| `muscle_names` | `list[str] \| str \| None` | `None` (deprecated alias for `muscle`) |
| `project` | `str \| None` | `None` |
| `file_label` | `str \| None` | `None` |
| `entity_label` | `str \| None` | `None` |
| `edit_history` | `list[dict[str, Any]] \| None` | `None` |
| `artifact_times` | `list[list[int]] \| None` | `None` |
| `artifact_regions` | `list[Any] \| None` | `None` |
| `edit_signal_token` | `str \| None` | `None` |
| `participant_meta` | `dict[str, Any] \| None` | `None` |
| `powerline_freq` | `float \| None` | `None` |
| `manufacturer` | `str \| None` | `None` |
| `manufacturers_model_name` | `str \| None` | `None` |
| `placement_scheme` | `str \| None` | `None` |
| `placement_scheme_description` | `str \| None` | `None` |
| `task_description` | `str \| None` | `None` |
| `software_versions` | `str \| None` | `None` |

### `EditFilterPayload`
| Field | Type | Default |
|---|---|---|
| `project` | `str \| None` | `None` |
| `edit_signal_token` | `str \| None` | `None` |
| `file_label` | `str \| None` | `None` |
| `entity_label` | `str \| None` | `None` |
| `grid_index` | `int` | `0` |
| `mu_index` | `int` | `0` |
| `distimes` | `list[list[int]]` | (required) |
| `mu_grid_index` | `list[int] \| None` | `None` |
| `pulse_train` | `list[float] \| None` | `None` |
| `view_start` | `int` | `0` |
| `view_end` | `int` | `0` |
| `nbextchan` | `int` | `DEFAULT_NBEXTCHAN` (1000) |
| `peel_off_win` | `float` | `DEFAULT_PEEL_OFF_WIN_SEC` (0.025) |
| `use_peeloff` | `bool` | `False` |
| `lock_spikes` | `bool` | `False` |
| `flagged` | `list[bool] \| None` | `None` |
| `artifact_times` | `list[int] \| None` | `None` |

### `EditRoiPayload`
| Field | Type | Default |
|---|---|---|
| `distimes` | `list[list[int]]` | (required) |
| `mu_index` | `int` | `0` |
| `pulse_train` | `list[float] \| None` | `None` |
| `fsamp` | `float \| None` | `None` |
| `x_start` | `int` | `0` |
| `x_end` | `int` | `0` |
| `y_min` | `float \| None` | `None` |
| `y_max` | `float \| None` | `None` |
| `artifact_times` | `list[int] \| None` | `None` |

### `EditOutliersPayload`
| Field | Type | Default |
|---|---|---|
| `distimes` | `list[list[int]]` | (required) |
| `mu_index` | `int` | `0` |
| `pulse_train` | `list[float] \| None` | `None` |
| `fsamp` | `float \| None` | `None` |

### `EditDeduplicatePayload`
| Field | Type | Default |
|---|---|---|
| `distimes` | `list[list[int]]` | (required) |
| `fsamp` | `float \| None` | `None` |
| `total_samples` | `int` | `0` |
| `parameters` | `dict[str, Any] \| None` | `None` |
| `mu_grid_index` | `list[int] \| None` | `None` |
| `pulse_trains` | `list[list[float]] \| None` | `None` |

### `EditFlagPayload`
| Field | Type | Default |
|---|---|---|
| `distimes` | `list[list[int]]` | (required) |
| `mu_index` | `int` | `0` |
| `flag` | `bool \| None` | `None` |

---

## Response Envelope (`contracts.py`)

```python
def success_payload(data: Any, *, api_version: str = "v1") -> dict[str, Any]
```
Returns `{"data": <data>, "meta": {"api_version": "v1"}}`.

---

## Error Envelope (`errors.py`)

```json
{"error": {"code": "<code>", "message": "<message>", "detail": "<optional>"}}
```

| Handler | Triggers on | Code | Status |
|---|---|---|---|
| `http_exception_handler` | `HTTPException` | `http_{status}` | exc.status_code |
| `validation_exception_handler` | `RequestValidationError` | `validation_error` | 422 |
| `unhandled_exception_handler` | any uncaught `Exception` | `internal_error` | 500 |

Registered via `register_exception_handlers(app)`. Never leaks tracebacks.

---

## Binary Wire Formats (`binary.py`)

### MUB1 frame — `pack_frame(meta, arrays)` / `unpack_frame(body)`

The one format for array transfers, in both directions (memory plan §5). Little-endian.

```
"MUB1" | header_len<uint32> | header (UTF-8 JSON) | pad to 8 |
  array data; each array starts at an 8-byte-aligned offset from the data start
header = {"meta": {...}, "arrays": [{"name", "dtype", "shape", "offset"}, ...]}
dtype ∈ f4, i4, i8, u1, i2
```

`pack_frame(meta, {name: (array, dtype)})` sizes one `bytearray` up front and casts each array
straight into it (`np.copyto`, `same_kind`), returning a `memoryview` that `Response` sends
without another copy. `unpack_frame` validates the header and bounds and returns `np.frombuffer`
views. The alignment lets the client read every array as a zero-copy typed-array view.
Media type `application/x-muedit-frame`; response header `x-muedit-format: mub1`.

### Format Catalog

| Name | Magic | Header `x-muedit-format` | Used by | Encoding |
|---|---|---|---|---|
| MUB1 | `b"MUB1"` | `mub1` | Viewport series (`min`/`max` or `samples`), decompose preview (`pulse_trains_full`, `pulse_trains_all`), edit load (`pulse_trains_full`), edit save request (`pulse_trains`) | `pack_frame` — JSON meta + f4 arrays |

---

## In-Memory Cache (`cache.py`)

Thread-safe (single `threading.Lock`), TTL-based, with budget-driven eviction (max items + max bytes). On every store/get, expired entries are purged, then oldest-expiring entries are evicted to fit budget.

### Caches

| Cache | Key | TTL | Max Items | Max Bytes | Stores |
|---|---|---|---|---|---|
| `_UPLOAD_SESSION_CACHE` | UUID token | 20 min | 3 | 1 GB | Uploaded signals + QC data |
| `_DECOMP_PREVIEW_BINARY_CACHE` | UUID token | 10 min | 8 | 512 MB | Preview frames, removed on first fetch |
| `_RUN_RESULT_CACHE` | UUID token | 30 min | 1 | — | Last run's float32 pulse matrix, for the run save |
| `_EDIT_SIGNAL_CONTEXT_CACHE` | UUID token | 12 hours | 1 | — | Raw MAT signal context for editing |
| `_EDIT_SIGNAL_LABEL_INDEX` | file_label | (follows context) | — | — | Maps file_label to edit_signal_token |

### Cache Functions

| Function | Description |
|---|---|
| `_store_upload_signal(signal, source_path=None) -> token` | Store a copy of the signal, return UUID token; `source_path` records the original file path |
| `_get_upload_signal(token) -> SignalImport \| None` | Get a read-only view of the signal, refresh TTL on hit |
| `_store_signal_views(token, views)` | Attach the QC stage's pyramids and overview to the upload |
| `_get_signal_views(token) -> SignalViews \| None` | Get them (sealed, so read-only) |
| `_hold_upload(token) -> HeldUpload \| None` | Signal, source path, store and views of an upload, the store held until `release()`; used by runs, `/qc/auto` and `/series/*` |
| `_store_decomp_preview_binary(payload) -> token` | Store binary preview blob |
| `_pop_decomp_preview_binary(token) -> bytes \| memoryview \| None` | Remove and return the preview frame |
| `_store_run_result(pulse_trains) -> token` | Keep a run's float32 pulse matrix |
| `_get_run_result(token) -> FloatArray \| None` | Read-only view of the stored pulse matrix |
| `_drop_run_result(token)` | Forget it after a successful save |
| `_store_edit_signal_context(context: EditSignalContext, file_label, session, store) -> token` | Keep the context and its edit `SessionStore` (deleted when the entry is dropped), or a float32 copy without a store; index by label |
| `_get_edit_signal_context(token) -> EditSignalContext \| None` | Get a read-only view of the context |
| `_get_edit_signal_context_by_label(file_label) -> EditSignalContext \| None` | Resolve context by label |

Stores copy once (`SignalImport.clone()`, `EditSignalContext.compact_copy()`). Reads return
`readonly_view()`: the arrays are shared with the cache but not writable, and lists and metadata
dicts are fresh copies. A caller that must modify data copies only the slice it changes.

Each cache holds a small entry dataclass (`_UploadEntry`, `_PreviewBinaryEntry`,
`_EditContextEntry`) with an `expires_at` time and an `nbytes` property, which
`_evict_to_budget_locked` uses for the item and byte budgets.

`SignalViews` (defined in `cache.py`; every array float32, memory-mapped from the upload's store):

| Field | Type | Notes |
|---|---|---|
| `fsamp` | `float` | |
| `grid_names` | `list[str]` | |
| `grid_rows` | `list[tuple[int, int]]` | `[first, stop)` EMG rows of each grid |
| `emg_types` | `list[int]` | Bandpass of each grid |
| `emg` | `MinMaxPyramid` | Bandpassed EMG of the grid rows |
| `overview` | `FloatArray` | `(n_grids, n_samples)`: smoothed mean \|EMG\| per grid |
| `overview_levels` | `MinMaxPyramid` | Pyramid of `overview` |
| `aux` | `MinMaxPyramid` | Aux channels |

---

## Shared Utilities (`common.py`)

| Function | Signature | Description |
|---|---|---|
| `parse_json` | `(raw, field_name) -> Any` | Parse JSON form field, 400 on failure |
| `parse_discard_channels` | `(raw) -> list[list[int]] \| None` | Parse channel discard overrides |
| `parse_rois` | `(raw) -> list[tuple[int,int]] \| None` | Parse ROI payload |
| `parse_json_object` | `(raw, field_name) -> dict \| None` | Parse and validate JSON object |
| `build_params` | `(raw) -> DecompositionParameters` | Build decomp params from JSON override |
| `make_json_safe` | `(value) -> Any` | Convert numpy to JSON-serializable |
| `parse_entity_label` | `(file_label) -> str` | Derive BIDS entity label from filename |
| `summarize_result` | `(result, save_path, persisted) -> dict` | Build compact decomposition summary |
