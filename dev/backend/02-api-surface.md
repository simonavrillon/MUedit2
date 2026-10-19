# 02 — API Surface

All routers use prefix `/api/v1` (dialog uses `/api/v1/dialog`). All JSON responses are wrapped in the canonical v1 envelope: `{"data": <data>, "meta": {"api_version": "v1"}}`. Errors use `{"error": {"code": ..., "message": ..., "detail": ...}}`.

The same server serves `frontend/` at `/` (after the routers), so the page and the API share one
origin and no CORS headers are sent. A `project` that is not a single folder name is refused with a 400 on the
`project` field (`/decompose_stream` with `bids_export`, `/edit/save`, `/edit/session/save`).

Each browser tab sends its session id in `X-MUedit-Session` (`common.request_session`). A request
that carries one makes that session the active one. The caches, the run and the edit session a tab
holds are scoped to it (see [In-Memory Cache](#in-memory-cache-cachepy)), and `/session/close`
drops them when the tab closes. A request without the header uses the `default` session; a
malformed id (not 1–64 letters, digits or `-`) is a 400 on `X-MUedit-Session`, or on `session`
for `/session/close`.

---

## Routes

Signals and decompositions are always opened by server path. `/decompose_stream` only accepts an
`upload_token` minted by `/preview-by-path`, and the edit stage works on an edit session opened by
`/edit/session/open`.

### Preview Router (`routes/preview.py`)

| Method | Path | Accepts | Returns | Service |
|---|---|---|---|---|
| GET | `/health` | — | `{status: "ok"}` | — |
| POST | `/preview-by-path` | JSON: `PathPayload` | JSON: `{upload_token, grid_names, total_samples, fsamp, channel_means, coordinates, metadata, muscle_names, aux_names, target_index}` (`target_index`: the aux channel named as a target, or `null`) + BIDS sidecar metadata and `project` (`config.project_of` of the file's BIDS root) when the file lies in a BIDS dataset | `build_preview_from_path(path)` |
| POST | `/qc/auto` | JSON: `QcAutoPayload` | JSON: `{bad_channels_per_grid, artifact_regions}` (per grid, 0/1 per channel; `[start, end)` sample pairs) | `run_auto_qc_on_token(payload)` |
| POST | `/qc/segment` | JSON: `SegmentPayload` | JSON: `{target_index, target_name, segments}` | `segment_target_on_token(payload)` |

The preview bandpasses the grid channels once, a few rows at a time, and keeps in the upload's
session store what the QC stage draws. That is min/max pyramids of the bandpassed EMG, of each
grid's smoothed mean |EMG| (the overview) and of the aux channels, with levels at bins of 16, 64,
256, … samples. The bandpassed EMG itself is not stored. `/qc/auto` bandpasses it again into a
temporary store file.

`/qc/auto` runs `run_auto_qc` on bandpassed EMG only, with **no notch filter**. A run with
`auto_mask_artifacts` (the CLI's `--auto-mask-artifacts`) runs it on notched and bandpassed EMG
(see the engine doc, Stage 2). The two can therefore flag different bad channels and artifact
regions for the same recording, most visibly on one with strong powerline interference. The app
never sets `auto_mask_artifacts`, so its runs use the QC page's result as sent.

`/qc/segment` splits the target channel (`aux_index`, or the first aux channel
`find_target_channel` names as a target) into `segments`, each
`{start, end, kind, direction, level_start, level_end, level_min, level_max, frequency_hz,
amplitude, offset, n_cycles, cycles}` with `kind` one of `ramp` | `plateau` | `sinusoid` |
`irregular` (see [04-io-signal-qc.md](04-io-signal-qc.md#target-segmentation-signalsegmentationpy)).
Rest is left out, so consecutive segments share their boundary sample unless rest lies between
them. It is a 400 on `aux_index` when the recording has no aux channel, none is named as a target
and none was given, the index does not exist, or the target holds no contraction (flat).

### Series Router (`routes/series.py`)

| Method | Path | Accepts | Returns | Service |
|---|---|---|---|---|
| GET | `/series/emg` | query: `upload_token`, `grid`, `start`, `end` (0 = end), `bins` (1–8192, default 1024) | MUB1 frame of one grid's bandpassed EMG | `series_frame("emg", …)` |
| GET | `/series/overview` | query: `upload_token`, `start`, `end`, `bins` | MUB1 frame, one row per grid | `series_frame("overview", …)` |
| GET | `/series/aux` | query: `upload_token`, `start`, `end`, `bins` | MUB1 frame, one row per aux channel | `series_frame("aux", …)` |
| GET | `/series/pulse` | query: `token` (an edit session or a finished run), `mu`, `start`, `end`, `bins` | MUB1 frame of one MU's pulse train, with its discharges in view | `pulse_frame(…)` |

A series frame holds `min` and `max` `f4[rows, bins]`, served from the coarsest pyramid level
with at least one level bin per output bin. When the window has no more samples than bins, it
holds `samples` `f4[rows, end − start]` instead (for the EMG, bandpassed on demand with 1 s of
padding). Its meta has `kind` (`envelope` | `samples`), `start`, `end`, `bins`, `factor`
(samples per source bin), `total_samples`, `fsamp` and `names`. An output bin holds every sample
of its range plus at most one level bin of its neighbours.

A pulse frame is built the same way from the MU's full-length pulse train (a train drawn from
the discharge times when the file has none). It adds `spikes` `i4` (the discharges in view) and
`spike_values` `f4` (the train's value at each). For an edit session it also adds `artifacts` and
`artifact_values`. Its meta carries `version` and `flagged` (0 and false for a run result). `version`
changes with every edit of the MU, so the client caches frames by it.

### Decompose Router (`routes/decompose.py`)

| Method | Path | Accepts | Returns | Service |
|---|---|---|---|---|
| POST | `/decompose_stream` | JSON: `DecomposePayload` (422 before any run starts when a field is missing or invalid; 400 with `field: upload_token` when the token expired, `field: project` for a project that is not a folder name) | `StreamingResponse` NDJSON (`application/x-ndjson`); 409 while another run is active | `start_decomposition()` + `decomposition_event_stream()` |
| POST | `/decompose/cancel` | — (the session header names the run) | JSON: `{cancelled: bool}`; false when this session has no run | `cancel_decomposition(session)` |

The server runs one decomposition at a time, in a worker process started with `spawn`
(`decompose_worker.child_main`). The worker opens the upload's memory-mapped files and writes the
run's arrays into a run store the server created, so only file locations cross between the
processes. Progress comes back over a pipe as NDJSON events — each carries its `phase`
(`load` \| `preprocess` \| `decompose` \| `postprocess` \| `export`), and the decompose search's
events add their position (`grid`, `window`, `niter`) and one outcome letter per iteration since
the last event (`k` kept, `r` rejected, `f` too few spikes), so the run page can draw the search
without parsing message text. The stream ends with `done`, `error` (also when the worker process dies),
or `cancelled`. An `error` event is `{stage, message, detail}`: `detail` is the message of a
`MueditError` the run raised, else `"Internal error; see the server log"`; the traceback goes to
`muedit.log` only. A run is cancelled by `/decompose/cancel`, by the client disconnecting, by
`/session/close` for its session, and at server shutdown; cancelling terminates the worker process.

The `done` event's preview carries only `mu_grid_index` (`DONE_PREVIEW_KEYS`): the page already
shows the recording the rest of the run's preview describes, and neither `pulse_trains_full` nor
`spike_times` crosses to the client. When the run has a full-length pulse matrix (`full_preview`),
the preview also carries `run_result_token`: the server keeps the matrix and the discharge times (the session's latest run,
until the session closes) for `/series/pulse` and the run save, which sends the token instead of the
data. A save naming a token the server no longer holds, and no discharge times, is refused with 400
(`field: run_result_token`).

### Editing Router (`routes/editing.py`)

The edit stage edits a server-side **edit session** (`editing.session.EditSession`, see
[05-editing-operations.md](05-editing-operations.md)). The server holds the decomposition at full
resolution; the client holds the discharge times, the per-MU fields and the edit history, and
fetches pulse trains through `/series/pulse`.

| Method | Path | Accepts | Returns | Service |
|---|---|---|---|---|
| POST | `/edit/session/open` | JSON: `PathPayload` (a `.npz` or `.mat` decomposition) | MUB1 state frame | `open_edit_session(path, session)` |
| GET | `/edit/session` | query: `token` | MUB1 state frame; the calling tab takes the session over (a reloaded page) | `edit_session_state(token, session)` |
| POST | `/edit/ops/{op}` | JSON: the op's model in `EDIT_OPS` (422 for a missing or mistyped field) | MUB1 change frame | `apply_edit(op, payload, session)` |
| POST | `/edit/session/recover` | JSON: `EditRecoverPayload` | MUB1 state frame with `recovered_edits` | `recover_edits(payload, session)` |
| POST | `/edit/session/prepare-grid` | JSON: `EditPrepareGridPayload` | `{grid}` once the grid's EMG is filtered | `prepare_edit_grid(payload, session)` |
| POST | `/edit/session/save` | JSON: `EditSessionSavePayload` | JSON: save result + the per-MU fields | `save_edit_session(payload, session)` |
| POST | `/edit/save` | JSON: `EditSavePayload` | JSON: save result | `save_edits(payload)` |

**Operations** (`/edit/ops/{op}`): `add-spikes`, `add-artifact`, `delete-spikes`,
`remove-outliers`, `update-filter`, `flag`, `reset`, `duplicate`, `remove-duplicates`, `undo`.
An operation the session refuses (empty window, no EMG to refit on) is a 400. An index outside
the session's MUs or grids is a 400 with the message `<field> <index> is out of range [0, <n>)`
and `detail: {field: "mu" | "grid"}`; `/series/emg` and `/series/pulse` answer their `grid` and `mu` alike (a
wrong type stays a 422). An expired token is a 400 with `field: token`.

**State frame** (open, state, recover). Meta: the file fields (`file_label`, `source_path`,
`fsamp`, `total_samples`, `grid_names`, `rois`, `parameters`, `muscle`, `sil`, `project` and the
BIDS sidecar fields when the file lies in a BIDS dataset), `token`, the per-MU fields,
`edit_history`, and `recoverable_edits` (unsaved edits an earlier session left for this file).
Arrays: `spikes`/`spike_offsets` and `artifacts`/`artifact_offsets`, CSR over all MUs.

**Change frame** (ops). Meta: the per-MU fields, `changed` (indices of the MUs whose spikes, flag
or train changed), `history_start` and `history` (the client cuts its log at `history_start` and
appends `history`), `kept_indices` when MUs were removed or reordered, and operation extras
(`removed_count`, `fsamp`, `undone`). Arrays: CSR spikes and artifacts of the `changed` MUs only.

**Per-MU fields**, in every state and change frame and in the session save result: `n_mu`,
`mu_uids`, `mu_grid_index`, `flagged`, `versions`, `has_pulse`, `dirty`, `can_undo`,
`crash_recovery` (false once the session's edit log cannot be written: unsaved edits would not
survive a crash; the page warns).

**Saves.** `/edit/session/save` writes the session's edits (flagged MUs and duplicates removed
unless `remove_flagged` / `remove_duplicates` are false). The saved file becomes the session's
baseline, and its edit log starts over. `/edit/save` is the run save: it writes the run held under
`run_result_token` (its discharge times, unless `spike_times` are sent, its pulse trains, SIL,
windows and target segments). Both return `{saved, path, kept_indices, mu_uids, edit_history,
bids_emg_paths?, bids_emg_error?, bids_deriv_paths?, bids_warnings?}`: `bids_emg_error` says why the
BIDS EMG was not written, `bids_warnings` which other BIDS side outputs were not.

### Dialog Router (`routes/dialog.py`)

| Method | Path | Accepts | Returns | Service |
|---|---|---|---|---|
| GET | `/open-file` | — | JSON: `{path: str|None, name: str|None}` | `_open_dialog_macos()` (AppleScript) or `_open_dialog_tkinter()` (subprocess) |

Accepted extensions: `mat, otb+, otb4, npz, bdf, edf, rhd, oebin`. Returns 408 on timeout, 500 on failure.

### Session Router (`routes/memory.py`)

| Method | Path | Accepts | Returns | Service |
|---|---|---|---|---|
| POST | `/session/close` | query: `session` | 204; cancels the session's run and drops everything it holds | `cancel_decomposition()` + `cache.close_session()` |
| GET | `/debug/memory` | — | JSON: process memory, `BUDGET.usage()` per cache and per session, the session stores on disk | — |

The frontend sends `/session/close` as a `keepalive` fetch (which, unlike a beacon, carries the session header) when its page is hidden for good.

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

### `SegmentPayload`
| Field | Type | Default |
|---|---|---|
| `upload_token` | `str` | (required) |
| `aux_index` | `int \| None` | `None` (the channel named as a target) |

### `BidsSaveFields`

The session form's fields, shared by both saves.

| Field | Type | Default |
|---|---|---|
| `muscle` | `list[str] \| str \| None` | `None` |
| `muscle_names` | `list[str] \| str \| None` | `None` (deprecated alias for `muscle`) |
| `project` | `str \| None` | `None` |
| `file_label` | `str \| None` | `None` |
| `entity_label` | `str \| None` | `None` |
| `participant_meta` | `ParticipantMeta \| None` (`age`, `sex`, `handedness`; empty or `n/a` = not given; `age` a whole number 0–150) | `None` |
| `powerline_freq` | `float \| None`, in `(0, 1000]` | `None` |
| `units` | `str \| None` (of the EMG, over the loader's) | `None` |
| `manufacturer` | `str \| None` | `None` |
| `manufacturers_model_name` | `str \| None` | `None` |
| `placement_scheme` | `str \| None` | `None` |
| `placement_scheme_description` | `str \| None` | `None` |
| `task_description` | `str \| None` | `None` |
| `software_versions` | `str \| None` | `None` |
| `remove_flagged` | `bool \| None` | `None` (true) |
| `remove_duplicates` | `bool \| None` | `None` (true) |

### `EditSavePayload` (`BidsSaveFields` +)
| Field | Type | Default |
|---|---|---|
| `spike_times` | `list[list[int]] \| None`, each in `[0, 2**31)` | `None` (the run's, from `run_result_token`) |
| `run_result_token` | `str \| None` | `None` |
| `total_samples` | `int`, `> 0` | (required) |
| `fsamp` | `float`, `> 0` | (required) |
| `grid_names` | `list[str] \| None` | `None` |
| `mu_grid_index` | `list[int] \| None`, each `>= 0` | `None` |
| `parameters` | `dict[str, Any] \| None` | `None` |
| `artifact_regions` | `list[Any] \| None` | `None` (`[start, end]` pairs or `{start, end}` objects) |

### `EditSessionSavePayload` (`BidsSaveFields` +)
| Field | Type | Default |
|---|---|---|
| `token` | `str` | (required) |

### `EditRecoverPayload`
| Field | Type | Default |
|---|---|---|
| `token` | `str` | (required) |
| `apply` | `bool` | `True` (false drops the unsaved edits) |

### `DecomposePayload`

| Field | Type | Default |
|---|---|---|
| `upload_token` | `str` | (required) |
| `params` | `DecompositionParameters` (overrides; unknown keys ignored, `0`/`1` accepted for booleans) | the defaults |
| `persist_output` | `bool` | `False` (also save the `.npz` next to the source) |
| `full_preview` | `bool` | `False` (keep the full pulse trains for the explorer and the save) |
| `rois` | `list[Span] \| None` | `None` (the whole recording) |
| `artifact_regions` | `list[Span] \| None` | `None` |
| `discard_channels` | `list[list[int]] \| None` | `None` (per grid, per channel: 1 = discarded) |
| `target_index` | `int \| None` | `None` (the segmented auxiliary channel) |
| `bids_export` | `bool` | `False` |
| `project` | `str \| None` | `None` |
| `bids_entities`, `bids_metadata` | `dict[str, Any] \| None` | `None` |

`Span` is `{start: int ≥ 0, end: int > start}` (else a 422); an end past the recording is
clamped to it. `params` is range-checked by `DecompositionParameters` itself
(see the engine doc); a rejected value is a 422 on `params`.

### Edit operations (`EDIT_OPS`)

Each `op` has its own model; every model carries `token: str`. Fields the request leaves out are
not passed, so the session method's defaults apply.

| `op` | Model | Fields |
|---|---|---|
| `add-spikes`, `add-artifact` | `RoiAddPayload` | `mu`, `x_start`, `x_end`, `y_min: float \| None` |
| `delete-spikes` | `RoiDeletePayload` | as `RoiAddPayload`, plus `y_max: float \| None` |
| `update-filter` | `FilterUpdatePayload` | `mu`, `view_start`, `view_end`, `use_peeloff=False`, `lock_spikes=False`, `project`, `nbextchan`, `peel_off_win`, `compute_dtype` (left out: the run's own) |
| `flag` | `FlagPayload` | `mu`, `flag=True` |
| `remove-outliers`, `reset`, `duplicate` | `MuOpPayload` | `mu` |
| `remove-duplicates`, `undo` | `EditOpPayload` | — |

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
| `muedit_exception_handler` | `MueditError` | `http_{status}` | exc.status (400; 404 for `NotFoundError`) |
| `unhandled_exception_handler` | any uncaught `Exception` | `internal_error` | 500 |

Registered via `register_exception_handlers(app)`.

`MueditError` (`muedit/errors.py`, a `ValueError`) is an error the user can fix: its message is
the envelope's `message`, and `detail` is `{field}` when it names the request field at fault
(`upload_token`, `token`, `path`, …). Every 400 and 404 is one; services raise it and let the
handler answer. Its subclasses: `NotFoundError` (404, a missing file), `SignalLoadError` (a
recording any loader refuses, `field: path`, a MAT decomposition opened as a signal among them),
`DecompositionFileError` (`field: path`), `BidsFieldError` (`field: bids_entities`) and
`EditError`. `HTTPException` is left to the routing-level 408 (dialog timeout), 409 (a run is
already going) and the dialog's 500. Anything else is a bug: a 500 with the generic message and
no detail.

Logging (`muedit.log`): a user error is one INFO line with its message, no traceback (the
handler logs the request's method, path and status; a run's relay logs its error event); a
degradation the request survives (an unreadable sidecar, edit log or `settings.xml`, a failed BIDS
side output) is one WARNING naming what was lost and why, with the traceback only where a broad
`except Exception` could hide a bug; a bug is an ERROR with its traceback.

---

## Binary Wire Formats (`binary.py`)

### MUB1 frame — `pack_frame(meta, arrays)` / `unpack_frame(body)`

The one format for array transfers. Little-endian.

```
"MUB1" | header_len<uint32> | header (UTF-8 JSON) | pad to 8 |
  array data; each array starts at an 8-byte-aligned offset from the data start
header = {"meta": {...}, "arrays": [{"name", "dtype", "shape", "offset"}, ...]}
dtype ∈ f4, i4, i8, u1, i2
```

`pack_frame(meta, {name: (array, dtype)})` sizes one `bytearray` up front and casts each array
straight into it (`np.copyto`, `same_kind`), returning a `memoryview` that `Response` sends
without another copy. `unpack_frame` (used by the tests) validates the header and bounds and returns
`np.frombuffer` views. The alignment lets the client read every array as a zero-copy typed-array view.
Media type `application/x-muedit-frame`; response header `x-muedit-format: mub1`.

### Format Catalog

| Name | Magic | Header `x-muedit-format` | Used by | Encoding |
|---|---|---|---|---|
| MUB1 | `b"MUB1"` | `mub1` | Viewport series (`min`/`max` or `samples`), pulse frames, decompose preview (CSR `spikes`/`spike_offsets`), edit session state and change frames (CSR spikes and artifacts) | `pack_frame` — JSON meta + typed arrays |

All frames go from server to client; no request body is a frame.

---

## In-Memory Cache (`cache.py`, `memory.py`)

Every cache is a `BudgetedLRU` counted against one `MemoryBudget` (`cache.BUDGET`). The budget is
10% of the machine's RAM, between 256 MB and 1 GB, or `MUEDIT_CACHE_BUDGET_MB`. Entries count only
their heap bytes: arrays memory-mapped from a session store cost nothing.

Each entry belongs to the session that stored it. Inserting an entry that does not fit evicts
other sessions' entries, least recently used first; the active session's entries, and entries a
request or run holds (`BudgetedLRU.hold`), are never evicted, and may exceed the budget. A held
entry is still dropped explicitly (a new file, a closed tab). A sweep thread (every 60 s) closes
sessions idle for 20 minutes, except the active one. `per_session=1` means a session's next entry
replaces its previous one. `on_drop` releases what an entry holds outside the heap (its store).

### Caches

| Cache | Per session | Holds | On drop |
|---|---|---|---|
| `_UPLOADS` | 1 | The opened recording (`SignalImport`, memory-mapped from its store) and its `SignalViews` | Closes the store |
| `_RUN_RESULTS` | 1 | The last run's pulse trains (in the run store) and discharge times | Closes the store |
| `_EDIT_SESSIONS` | 1 | The open `EditSession` | `EditSession.close()` (keeps its log only with unsaved edits) |

### Cache Functions

| Function | Description |
|---|---|
| `close_session(session)` | Drop everything the session holds |
| `_release_upload(session)` | Drop the session's upload before it loads the next file. A file opened while another loads in the same tab is pinned only once built, so a drop never deletes a store still being written: the upload pinned last stays, the other answers 400 (`field: upload_token`) |
| `_store_upload_signal(signal, source_path, session, store, views) -> token` | Keep a signal and the QC stage's views, once both are built; with `store`, take over the store they were written in, else keep a copy |
| `_hold_upload(token) -> HeldUpload \| None` | Signal, source path, store and views of an upload, kept from eviction and the store held until `release()`; used by runs, `/qc/auto` and `/series/*` |
| `_store_run_result(pulse_trains, session, store, spikes) -> token` | Keep a finished run, taking over its run store |
| `_held_run_result(token)` | Context manager: the run (or None), its pulse trains read-only, its store held until the block ends; used by `/series/pulse` and `save_edits` |
| `_store_edit_session(edit, session) -> token` | Keep an edit session in place of the session's last, which closes |
| `_get_edit_session(token, session=None)` | The edit session; with `session`, it moves to that tab |
| `_resize_edit_session(token)` | Recount its bytes after an edit |
| `_live_edit_logs()` | The edit logs open sessions are writing (not offered for recovery) |

Uploads are stored once (a copy, or the store the loader wrote). Holds return `readonly_view()`:
the arrays are shared with the cache but not writable, and lists and metadata dicts are fresh
copies.

### Session stores (`storage.py`)

A `SessionStore` is a folder of `.npy` files under `<cache dir>/sessions/`, one per upload, run or
edit session, holding its full-length arrays as memory maps. `close()` deletes the folder (after
the last `hold()` is released); once it was asked for, `hold()` raises `StoreClosedError`, and a
`release()` without a `hold()` is logged and ignored. `seal()` returns a read-only map, but
`RamStore.seal()` returns the array itself, still writable. An array that would leave less than 1 GB free on disk
(`MUEDIT_DISK_RESERVE_MB`) stays on the heap instead. Each folder names its owner process;
`purge_stale_sessions()` deletes the folders of exited processes at startup. `RamStore` is the
heap stand-in used when no store is given.

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
| `make_json_safe` | `(value) -> Any` | Convert numpy to JSON-serializable |
| `parse_entity_label` | `(file_label) -> str` | Derive BIDS entity label from filename |
