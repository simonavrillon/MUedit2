# 02 — API Surface

All routers use prefix `/api/v1` (dialog uses `/api/v1/dialog`). All JSON responses are wrapped in the canonical v1 envelope: `{"data": <data>, "meta": {"api_version": "v1"}}`. Errors use `{"error": {"code": ..., "message": ..., "detail": ...}}`.

---

## Routes

The non-streaming `POST /decompose`, `GET /config`, and the multipart upload routes `POST /preview` and `POST /edit/load` were removed as unreachable from the frontend (structural audit F4). Signals and decompositions are always opened by server path; `/decompose_stream` only accepts an `upload_token` minted by `/preview-by-path`.

### Preview Router (`routes/preview.py`)

| Method | Path | Accepts | Returns | Service |
|---|---|---|---|---|
| GET | `/health` | — | `{status: "ok"}` | — |
| POST | `/preview-by-path` | JSON: `PathPayload` | JSON: `{upload_token, mean_abs, grid_mean_abs, grid_names, total_samples, fsamp, channel_means, coordinates, metadata, muscle, auxiliary, auxiliary_names}` + BIDS sidecar metadata | `build_preview_from_path(path)` |
| POST | `/qc/window` | JSON: `QcWindowPayload` | Binary MQCR (`x-muedit-format: qc-raw-f32-v1`) | `get_qc_window(payload)` |
| POST | `/qc/auto` | JSON: `QcAutoPayload` | JSON: `{bad_channels_per_grid, artifact_regions, artifact_samples, total_samples, fsamp, grid_names}` | `run_auto_qc_on_token(payload)` |

### Decompose Router (`routes/decompose.py`)

| Method | Path | Accepts | Returns | Service |
|---|---|---|---|---|
| POST | `/decompose_stream` | form fields: `upload_token` (required; 400 with `field: upload_token` when missing or expired), `params`, `duration`, `persist_output`, `roi_start: int`, `roi_end: int`, `rois` (JSON str), `discard_channels`, `bids_export: bool`, `project: str`, `bids_entities` (JSON str), `bids_metadata` (JSON str), `full_preview`, `artifact_regions` (JSON str) | `StreamingResponse` NDJSON (`application/x-ndjson`) | `decomposition_event_stream()` |
| GET | `/decompose_preview/{token}` | path param `token: str` | Binary MDPV (`x-muedit-format: decompose-preview-f32-v1`) | `fetch_decompose_preview_binary(token)` |

Header `x-muedit-binary` (default `"1"`) controls binary vs JSON preview encoding in stream mode.

### Editing Router (`routes/editing.py`)

| Method | Path | Accepts | Returns | Service |
|---|---|---|---|---|
| POST | `/edit/load-by-path` | JSON: `PathPayload`; header `x-muedit-binary` | JSON or Binary MELD (`x-muedit-format: edit-load-f32-v1`) | `load_decomposition_binary_from_path(path)` / `load_decomposition_from_path(path)` |
| POST | `/edit/save` | JSON: `EditSavePayload` | JSON: `{saved, path, bids_emg_paths?, bids_deriv_paths?}` | `save_edits(payload)` |
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

### `QcWindowPayload`
| Field | Type | Default |
|---|---|---|
| `upload_token` | `str` | (required) |
| `grid_index` | `int` | `0` |
| `start` | `int` | `0` |
| `end` | `int` | `0` |
| `target_fs` | `float` | `1000.0` |
| `channel_index` | `int \| None` | `None` |

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

### `pack_json_f32_payload(magic, meta, *arrays)` — Generic packer

Used by MDPV and MELD. All values little-endian.

```
magic(4B) | version<uint32=1> | meta_len<uint32> |
  [arr0_rows<uint32> | arr0_cols<uint32>] x N_arrays |
  meta_bytes(UTF-8 JSON) |
  arr0_data(float32, C-order) | arr1_data | ...
```

### Format Catalog

| Name | Magic | Header `x-muedit-format` | Used by | Encoding |
|---|---|---|---|---|
| MDPV | `b"MDPV"` | `decompose-preview-f32-v1` | Decompose preview (streamed) | `pack_json_f32_payload` — 2 f32 matrices (`pulse_trains_full`, `pulse_trains_all`) + JSON meta |
| MELD | `b"MELD"` | `edit-load-f32-v1` | Edit load (loaded decomposition) | `pack_json_f32_payload` — 1 f32 matrix (`pulse_trains_full`) + JSON meta |
| MQCR | `b"MQCR"` | `qc-raw-f32-v1` | QC channel-window raw traces | Custom encoding (see below) |

### MQCR Custom Encoding

```
"MQCR" | version<uint32=1> | grid_index<int32> | channel_index<int32> |
  start<int32> | end<int32> | total_samples<int32> | fsamp<float32> |
  n_channels<uint32> |
  [channel_index<int32> | n_samples<uint32> | float32_data] x n_channels
```

---

## In-Memory Cache (`cache.py`)

Thread-safe (single `threading.Lock`), TTL-based, with budget-driven eviction (max items + max bytes). On every store/get, expired entries are purged, then oldest-expiring entries are evicted to fit budget.

### Caches

| Cache | Key | TTL | Max Items | Max Bytes | Stores |
|---|---|---|---|---|---|
| `_UPLOAD_SESSION_CACHE` | UUID token | 20 min | 3 | 1 GB | Uploaded signals + QC data |
| `_DECOMP_PREVIEW_BINARY_CACHE` | UUID token | 10 min | 8 | 512 MB | Binary preview blobs |
| `_EDIT_SIGNAL_CONTEXT_CACHE` | UUID token | 12 hours | 1 | — | Raw MAT signal context for editing |
| `_EDIT_SIGNAL_LABEL_INDEX` | file_label | (follows context) | — | — | Maps file_label to edit_signal_token |

### Cache Functions

| Function | Description |
|---|---|
| `_store_upload_signal(signal) -> token` | Store signal, return UUID token |
| `_get_upload_signal(token) -> dict \| None` | Get cloned signal, refresh TTL on hit |
| `_store_qc_signal(token, data, fsamp, grid_names, discard_channels)` | Attach QC arrays to upload session |
| `_get_qc_signal(token) -> dict \| None` | Get QC arrays (read-only view) |
| `_store_decomp_preview_binary(payload) -> token` | Store binary preview blob |
| `_get_decomp_preview_binary(token) -> bytes \| None` | Get binary preview |
| `_store_edit_signal_context(context, file_label) -> token` | Store raw signal context, index by label |
| `_get_edit_signal_context(token) -> dict \| None` | Get cloned context |
| `_get_edit_signal_context_by_label(file_label) -> dict \| None` | Resolve context by label |

Signal cloning uses `SignalImport.from_mapping().clone()` to avoid shared mutable arrays.

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
