# 06 — Worktree: User-Exposed vs App-Internal

This worktree maps every function/symbol in the backend to one of three categories:

- **User-exposed** — reachable by the user via API endpoints, CLI, or public package exports
- **App-internal** — called by other backend modules but not directly by the user
- **Extension API** — exported but no internal callers (intentional extension points)

Use this to trace what the user can reach.

---

## `api/` — HTTP API Layer

### `routes/preview.py`

| Symbol | Category | Reachable via |
|---|---|---|
| `preview_router` | User-exposed | `include_routers()` |
| `GET /health` | User-exposed | HTTP |
| `POST /preview-by-path` | User-exposed | HTTP |
| `POST /qc/auto` | User-exposed | HTTP |

### `routes/series.py`

| Symbol | Category | Reachable via |
|---|---|---|
| `series_router` | User-exposed | `include_routers()` |
| `GET /series/emg` | User-exposed | HTTP |
| `GET /series/overview` | User-exposed | HTTP |
| `GET /series/aux` | User-exposed | HTTP |
| `GET /series/pulse` | User-exposed | HTTP |

### `routes/decompose.py`

| Symbol | Category | Reachable via |
|---|---|---|
| `decompose_router` | User-exposed | `include_routers()` |
| `POST /decompose_stream` | User-exposed | HTTP |
| `POST /decompose/cancel` | User-exposed | HTTP |

### `routes/editing.py`

| Symbol | Category | Reachable via |
|---|---|---|
| `editing_router` | User-exposed | `include_routers()` |
| `POST /edit/session/open` | User-exposed | HTTP |
| `GET /edit/session` | User-exposed | HTTP |
| `POST /edit/ops/{op}` | User-exposed | HTTP |
| `POST /edit/session/recover` | User-exposed | HTTP |
| `POST /edit/session/prepare-grid` | User-exposed | HTTP |
| `POST /edit/session/save` | User-exposed | HTTP |
| `POST /edit/save` | User-exposed | HTTP |

### `routes/dialog.py`

| Symbol | Category | Reachable via |
|---|---|---|
| `dialog_router` | User-exposed | `include_routers()` |
| `GET /open-file` | User-exposed | HTTP |
| `_open_dialog_macos()` | App-internal | Called by route handler (macOS) |
| `_open_dialog_tkinter()` | App-internal | Called by route handler (non-macOS) |

### `routes/memory.py`

| Symbol | Category | Reachable via |
|---|---|---|
| `memory_router` | User-exposed | `include_routers()` |
| `POST /session/close` | User-exposed | HTTP (a `keepalive` fetch from a closing tab) |
| `GET /debug/memory` | User-exposed | HTTP (diagnostics) |

### `services/preview_service.py`

| Symbol | Category | Reachable via |
|---|---|---|
| `build_preview_from_path(path)` | App-internal | `POST /preview-by-path` route |
| `run_auto_qc_on_token(payload)` | App-internal | `POST /qc/auto` route |
| `_build_preview_core(filepath)` | App-internal | Called by `build_preview_from_path` |
| `_bandpassed_grids(...)` | App-internal | Called by `run_auto_qc_on_token`; a temporary store file |
| `_decomp_artifact_error(field)` | App-internal | Called by `build_preview_from_path` |
| `_mask_to_regions(mask)` | App-internal | Called by `run_auto_qc_on_token` |

### `services/series_service.py`

| Symbol | Category | Reachable via |
|---|---|---|
| `build_signal_views(signal, store, grid_counts, emg_types)` | App-internal | Called by `_build_preview_core`; pyramids, overview and channel means in one pass |
| `series_frame(kind, upload_token, start, end, bins, grid)` | App-internal | `GET /series/emg`, `/series/overview`, `/series/aux` |
| `pulse_frame(token, mu, start, end, bins)` | App-internal | `GET /series/pulse`; an edit session's or a run's train |
| `bandpassed_rows(signal, lo, hi, emg_type)` | App-internal | Preview pass and auto-QC |
| `grid_rows(grid_counts, n_rows)`, `grid_emg_type(emg_types, grid)` | App-internal | Preview and auto-QC |

### `services/decompose_service.py`

| Symbol | Category | Reachable via |
|---|---|---|
| `start_decomposition(upload_token, options, ...)` | App-internal | `POST /decompose_stream` route; 409 while a run is active, holds the upload's store |
| `decomposition_event_stream(run, is_disconnected)` | App-internal | `POST /decompose_stream` route; cancels the run when the client goes away |
| `cancel_decomposition(session)` | App-internal | `POST /decompose/cancel`, `POST /session/close` |
| `stop_decompositions()` | App-internal | App lifespan shutdown |
| `parse_stream_options(...)` | App-internal | Called by decompose_stream route |
| `_Run` | App-internal | One run: its worker process and the supervisor thread relaying events |

### `services/decompose_worker.py`

| Symbol | Category | Reachable via |
|---|---|---|
| `RunJob` | App-internal | Built by `start_decomposition`; pickled to the worker (memmaps as file locations) |
| `execute(job, store, send)` | App-internal | Worker process body |
| `child_main(job, store_path, conn)` | App-internal | `spawn` target of the worker process |

### `services/editing_service.py`

| Symbol | Category | Reachable via |
|---|---|---|
| `open_edit_session(path, session)` | App-internal | `POST /edit/session/open` route |
| `edit_session_state(token, session)` | App-internal | `GET /edit/session` route |
| `apply_edit(op, payload, session)` | App-internal | `POST /edit/ops/{op}` route |
| `recover_edits(payload, session)` | App-internal | `POST /edit/session/recover` route |
| `prepare_edit_grid(payload, session)` | App-internal | `POST /edit/session/prepare-grid` route |
| `save_edit_session(payload, session)` | App-internal | `POST /edit/session/save` route |
| `save_edits(payload)` | App-internal | `POST /edit/save` route (the run save) |
| `_new_session(...)`, `_file_extras(...)` | App-internal | Called by `open_edit_session`: the `EditSession` and what the BIDS sidecars and `.json` edit log add |
| `_state_response(...)`, `_change_response(...)` | App-internal | The MUB1 state and change frames |
| `_SaveRequest`, `_save(req)` | App-internal | Called by both saves |
| `_dedup(...)` | App-internal | Called by `_save` and the session's `remove-duplicates` |
| `_export_bids_emg(...)` | App-internal | Called by `_save` |
| `_dataset_root(edit, project)` | App-internal | Where a session save writes and a refit reads BIDS EMG |

### `services/bids_helpers.py`

| Symbol | Category | Reachable via |
|---|---|---|
| `read_bids_sidecar_meta(root, entity_label)` | App-internal | Called by the preview service and `_file_extras` |
| `_read_bids_grid(...)` | App-internal | Called by the edit session's first refit of a grid |
| `_parse_all_bids_entities(entity_label)` | App-internal | Called by editing services |
| `_parse_subject_session_from_entity_label(label)` | App-internal | Called by `read_bids_sidecar_meta` |
| `_infer_bids_root_from_decomp_path(filepath)` | App-internal | Called by editing services |
| `_normalize_bids_meta_value(value)` | App-internal | Called by `read_bids_sidecar_meta` |
| `_grid_sort_key(group)` | App-internal | Called by `_read_bids_channels_sidecar` |
| `_read_bids_channels_sidecar(path)` | App-internal | Called by `_file_extras` |

### `services/edit_helpers.py`

| Symbol | Category | Reachable via |
|---|---|---|
| `_expected_grid_count(decomp)` | App-internal | Called by `_file_extras` |
| `_pad_grid_names(names, expected, fallback)` | App-internal | Called by `_file_extras`, `_save` |
| `_normalize_muscle_names(raw)` | App-internal | Called by `_save` |
| `_normalize_flagged(raw, nmu)` | App-internal | Called by `_save` |
| `_generate_mu_uids(mu_grid_index)` | App-internal | Called by `_new_session`, `save_edits` |
| `_normalize_mu_grid_index(raw, nmu)` | App-internal | Called by `save_edits` |
| `_coerce_dup_tol(raw, default)` | App-internal | Called by `_dedup` |
| `_coerce_bool_param(raw)` | App-internal | Called by `_dedup` (`duplicatesbgrids`) |

### `schemas.py`

| Symbol | Category | Reachable via |
|---|---|---|
| `PathPayload` | User-exposed | `POST /preview-by-path`, `POST /edit/session/open` |
| `QcAutoPayload` | User-exposed | `POST /qc/auto` |
| `BidsSaveFields` | App-internal | Base of both save payloads |
| `EditSavePayload` | User-exposed | `POST /edit/save` |
| `EditSessionSavePayload` | User-exposed | `POST /edit/session/save` |
| `EditRecoverPayload` | User-exposed | `POST /edit/session/recover` |
| `EditPrepareGridPayload` | User-exposed | `POST /edit/session/prepare-grid` |
| `EditOpPayload` | User-exposed | `POST /edit/ops/{op}` |

### Other API modules

| Symbol | Category | Reachable via |
|---|---|---|
| `app_factory.create_app()` | App-internal | Called by `cli.serve_api` |
| `app_factory.mount_frontend()` | App-internal | Called by `cli.serve_api` |
| `routes.include_routers()` | App-internal | Called by `cli.serve_api` |
| `contracts.success_payload()` | App-internal | Called by all route handlers |
| `binary.pack_frame()` | App-internal | Called by the series, decompose and editing services |
| `binary.unpack_frame()` | App-internal | Tests only (every frame goes from server to client) |
| `errors.register_exception_handlers()` | App-internal | Called by `create_app` |
| `errors.error_payload()` | App-internal | Called by exception handlers |
| `errors.http_exception_handler` | App-internal | Registered on app |
| `errors.validation_exception_handler` | App-internal | Registered on app |
| `errors.unhandled_exception_handler` | App-internal | Registered on app |
| `config.DATA_ROOT` | App-internal | Used by `resolve_bids_root`, `project_of` |
| `config.default_data_root()` | App-internal | Sets `DATA_ROOT` at import |
| `config.resolve_bids_root()` | App-internal | Called through `common.bids_root_for()` and the edit session |
| `config.project_of()` | App-internal | Called by preview and edit services |
| `common.bids_root_for()` | App-internal | Called by the decompose route and the edit saves (400 on a bad project) |
| `common.build_params()` | App-internal | Called by decompose service |
| `common.make_json_safe()` | App-internal | Called by many services |
| `common.parse_json()` | App-internal | Called by route handlers |
| `common.parse_discard_channels()` | App-internal | Called by decompose routes |
| `common.parse_rois()` | App-internal | Called by decompose routes |
| `common.parse_json_object()` | App-internal | Called by decompose routes |
| `common.parse_entity_label()` | App-internal | Called by editing services |
| `common.request_session()` | App-internal | Router dependency: the `X-MUedit-Session` header, made the active session |
| `common.summarize_result()` | App-internal | Called by decompose service |
| `common.require_existing_path()` | App-internal | Called by preview/editing route handlers |
| `common._coerce_param_value()` | App-internal | Called by `build_params` |
| `cache.BUDGET` | App-internal | The one `MemoryBudget`; swept by the app lifespan |
| `cache.close_session()` | App-internal | Called by `POST /session/close` |
| `cache._release_upload()` | App-internal | Called by the preview service before the next load |
| `cache._store_upload_signal()` | App-internal | Called by preview service, with the upload's views, once both are built |
| `cache._hold_upload()` | App-internal | Called by decompose, preview (`/qc/auto`) and series services |
| `cache._get_upload_signal()`, `cache._get_upload_source_path()`, `cache._get_signal_views()`, `cache._get_run_result()` | App-internal | Tests (the services use `_hold_upload` / `_get_run_result_entry`) |
| `cache._store_run_result()` | App-internal | Called by decompose service |
| `cache._get_run_result_entry()` | App-internal | Called by series service (`/series/pulse`) and `save_edits` |
| `cache._store_edit_session()` | App-internal | Called by `open_edit_session`, in place of the tab's previous session |
| `cache._get_edit_session()` | App-internal | Called by editing and series services |
| `cache._resize_edit_session()` | App-internal | Called after each edit, recovery and save |
| `cache._live_edit_logs()` | App-internal | Called by `open_edit_session` (logs not offered for recovery) |
| `cache.SignalViews`, `cache.RunResult`, `cache.HeldUpload` | App-internal | Cache entry types |
| `memory.MemoryBudget` | App-internal | Byte budget over every cache: eviction, idle-session sweep, `usage()` |
| `memory.BudgetedLRU` | App-internal | One token-keyed cache: `pin`, `get`, `move`, `pop`, `discard`, `resize`, `release_session` |
| `memory.default_budget_bytes()` | App-internal | 10% of RAM in [256 MB, 1 GB], or `MUEDIT_CACHE_BUDGET_MB` |
| `memory.session_id_or_default()` | App-internal | Validates a session id |
| `memory.process_memory_bytes()`, `peak_rss_bytes()`, `physical_memory_bytes()` | App-internal | `/debug/memory`, the memory tests |

---

## `decomp/` — Decomposition Engine

| Symbol | Category | Reachable via |
|---|---|---|
| `run_decomposition()` | User-exposed | `muedit.__init__`, CLI, API `/decompose_stream` |
| `DecompositionParameters` | User-exposed | `muedit.__init__`, `muedit.decomp.__init__` |
| `load_step()` | App-internal | Called by `run_decomposition` |
| `preprocess_step()` | App-internal | Called by `run_decomposition` |
| `decompose_step()` | App-internal | Called by `run_decomposition` |
| `postprocess_step()` | App-internal | Called by `run_decomposition` |
| `export_step()` | App-internal | Called by `run_decomposition` |
| `select_roi_interactively()` | User-exposed | CLI `--manual-roi` (imports matplotlib lazily; `plot` extra) |
| `build_manual_artifact_mask()` | App-internal | Called by `preprocess_step`, editing service save |
| `batch_process_filters()` | App-internal | Called by `postprocess_step` |
| `remove_duplicates_by_grid()` | App-internal | Called by `postprocess_step` |
| `dedup_survivors()` | App-internal | Called by `remove_duplicates_by_grid`, editing service `_dedup` |
| `rem_duplicates()` | App-internal | Called by `remove_duplicates_by_grid` |
| `compute_silhouette()` | App-internal | Called by `decompose_step` |
| `extend_signal()` | App-internal | Called by `decompose_step`, `operations.py` |
| `fixed_point_alg()` | App-internal | Called by `decompose_step` |
| `get_spikes()` | App-internal | Called by `decompose_step` |
| `minimize_isi_covariance()` | App-internal | Called by `decompose_step` |
| `pca_extended_signal()` | App-internal | Called by `decompose_step`, `operations.py` |
| `subtract_mu_waveforms()` | App-internal | Called by `decompose_step`, `operations.py` |
| `whiten_extended_signal()` | App-internal | Called by `decompose_step`, `operations.py` |
| `adaptive_batch_process()` | App-internal | Called by `postprocess_step` |
| `build_preview_payload()` | App-internal | Called by `export_step` |
| `abs_means()` | App-internal | Called by `build_preview_payload` (channel means, row by row) |
| `load_decomposition_file()` | App-internal | `load_decomposition()` without the EMG; tests and scripts |
| `load_decomposition_signal_context()` | App-internal | The EMG context alone; tests and scripts |
| `normalize_distimes()` | App-internal | Called by editing service |
| `build_pulse_trains_from_distimes()` | App-internal | Called by the decomposition loader for a file without pulse trains |
| `save_editlog()` | App-internal | Called by editing service |
| `save_decomposition_npz()` | App-internal | Called by `export_step`, editing service save — owns the `.npz` schema |
| `load_decomposition()` | App-internal | Called by editing service; one read of the file for both the decomposition and its EMG |
| `pack_csr()` / `unpack_csr()` | App-internal | CSR layout of the `.npz` schema (spike times, per-window SIL, channel masks) |
| `io.npz.NpzWriter` / `io.npz.NpzArchive` | App-internal | Aligned uncompressed `.npz` writing; one-open reading with memory maps and the restricted legacy unpickler |
| `first_non_none()` | App-internal | Called by `decomp/decomposition_file.py` internals |
| All `_`-prefixed functions | App-internal | Internal helpers |

---

## `io/` — File I/O

| Symbol | Category | Reachable via |
|---|---|---|
| `load_signal()` | User-exposed | `muedit.__init__`, `muedit.io.__init__`, pipeline |
| `get_loader()` | User-exposed | `muedit.io.__init__` |
| `register_loader()` | Extension API | `muedit.__init__`, `muedit.io.__init__` (no internal callers) |
| `supported_extensions()` | User-exposed | `muedit.io.__init__` |
| `load_mat()` | App-internal | Called via `factory._LOADERS` |
| `load_otb_plus()` | App-internal | Called via `factory._LOADERS` |
| `load_otb4()` | App-internal | Called via `factory._LOADERS` |
| `load_bids_signal()` | App-internal | Called via `factory._LOADERS` |
| `load_intan()` | App-internal | Called via `factory._LOADERS` |
| `load_openephys()` | App-internal | Called via `factory._LOADERS` |
| `export_bids_emg()` | App-internal | Called by `preprocess._export_raw_emg_bids`, editing service |
| `write_bids_dataset_description()` | App-internal | Called by `preprocess`, editing service |
| `export_bids_mu_derivatives()` | App-internal | Called by editing service |
| `build_entities()` | App-internal | Called by `export_bids_emg` |
| `load_bids_emg_grid()` | App-internal | Windowed grid read (sample window) |
| `read_bids_emg_grid()` | App-internal | Called by `bids_helpers._read_bids_grid` |
| `resolve_bids_emg_path()` | App-internal | Called by `_bids_reader`, `bids_helpers` |
| `select_grid_channels()` | App-internal | Called by `load_bids_emg_grid` |
| `NpzWriter`, `NpzArchive`, `RowSource` (`io/npz.py`) | App-internal | Called by `decomposition_file` and the saves (rows written as they are read) |
| `SessionStore`, `RamStore`, `ArrayStore` (`io/store.py`) | App-internal | Where loaders, runs and edit sessions put full-length arrays |
| `copy_into()`, `store_signal()`, `sample_blocks()` | App-internal | Block-by-block copies into a store |
| `purge_stale_sessions()` | App-internal | Called by the app lifespan at startup |
| `store_usage()` | App-internal | Called by `/debug/memory` |
| All `_`-prefixed functions | App-internal | Internal helpers |

---

## `signal/` — Signal Processing

| Symbol | Category | Reachable via |
|---|---|---|
| `run_auto_qc()` | User-exposed | `muedit.signal.__init__`, pipeline |
| `QCPipelineResult` | User-exposed | `muedit.signal.__init__` |
| `bandpass_signals()` | User-exposed | `muedit.signal.__init__`, pipeline |
| `demean()` | User-exposed | `muedit.signal.__init__` |
| `notch_signals()` | User-exposed | `muedit.signal.__init__`, pipeline |
| `format_hdemg_signal()` | User-exposed | `muedit.signal.__init__`, pipeline, loaders |
| `_detect_bad_channels()` | App-internal | Called by QC pipeline (not in `__all__`) |
| `detect_bad_channels_per_grid()` | App-internal | Called by QC pipeline |
| `_channel_qc_diagnostics()` | App-internal | Called by `_detect_bad_channels` |
| `_detect_artifact_mask()` | App-internal | Called by `detect_artifact_masks` |
| `detect_artifact_masks()` | App-internal | Called by QC pipeline |
| `mask_to_intervals()`, `intervals_to_mask()` | App-internal | `.npz` artifact intervals, preview artifact regions |
| `extend_signal()` | App-internal | Called by `decompose_step`, `operations.py` |
| `signed_square()` | App-internal | Called by `decompose_step`, `operations.py` |
| `find_refractory_peaks()` | App-internal | Called by `decompose_step`, `operations.py` |
| `split_by_amplitude()` | App-internal | Called by `decompose_step`, `operations.py` |
| `isi_cov()` | App-internal | Called by `rem_duplicates`, `minimize_isi_covariance` |
| `bandpass_inplace()`, `notch_inplace()`, `emg_filter_inplace()` | App-internal | Filters a few rows at a time in place (`preprocess_step`, series service, edit session) |
| `MinMaxPyramid`, `view()`, `envelope()` (`signal/pyramid.py`) | App-internal | Built by `build_signal_views`; read by `series_frame`, `pulse_frame` |
| `StreamedExtender`, `RowSelection`, `extend_mask()` (`signal/streaming.py`) | App-internal | Extended signal read batch by batch (full-trace and adaptive postprocess) |
| `moving_average_ms()` | App-internal | Called by series service |
| `get_grid_electrode_metadata()` | App-internal | Called by `export_bids_emg` |
| `ArtifactMaskConfig` | App-internal | Used by artifact mask functions |
| `ChannelQCConfig` | App-internal | Used by channel QC functions |
| `ChannelQCMetrics` | App-internal | Returned by `_channel_qc_diagnostics` |
| `GridSpec` | App-internal | Used by `format_hdemg_signal` |
| All `_`-prefixed functions | App-internal | Internal helpers |

---

## `editing/` — Motor-Unit Editing

| Symbol | Category | Reachable via |
|---|---|---|
| `update_motor_unit_filter_window()` | User-exposed | `muedit.editing.__init__`, editing service |
| `add_spikes_in_roi()` | User-exposed | `muedit.editing.__init__`, editing service |
| `add_artifact_in_roi()` | User-exposed | `muedit.editing.__init__`, editing service |
| `delete_spikes_in_roi()` | User-exposed | `muedit.editing.__init__`, editing service |
| `delete_artifacts_in_roi()` | User-exposed | `muedit.editing.__init__`, editing service |
| `delete_high_discharge_rate_spikes_in_roi()` | User-exposed | `muedit.editing.__init__` (no edit-session operation) |
| `remove_discharge_rate_outliers()` | User-exposed | `muedit.editing.__init__`, editing service |
| `SpikeTimes` | User-exposed | `muedit.editing.__init__` (type alias) |
| `FilterUpdateResult` | User-exposed | `muedit.editing.__init__` (type alias) |
| `_recompute_spikes_in_window()` | App-internal | Called by `update_motor_unit_filter_window` |
| `EditSession` (`session.py`) | App-internal | Built by `open_edit_session`; every edit operation, undo, saves |
| `EditError`, `Change`, `spike_array()` | App-internal | Refused edit (400), what an edit changed, sorted int32 spikes |
| `EditLog`, `find_recoverable()`, `purge_old_logs()` (`edit_log.py`) | App-internal | Per-session operation log; recovery on reopen; startup cleanup |

---

## `adapt_decomp/` — Adaptive Online Decomposition

| Symbol | Category | Reachable via |
|---|---|---|
| `AdaptiveDecomp` | User-exposed | `muedit.adapt_decomp.__init__` |
| `Config` | User-exposed | `muedit.adapt_decomp.__init__` |
| `run_adaptive_decomposition()` | User-exposed | `muedit.adapt_decomp.__init__`, `adaptive_batch.py` |
| `AdaptiveDecomp.run()` | App-internal | Called by `run_adaptive_decomposition`, `adaptive_batch` passes |
| `Calibration`, `calibration_centroids()` | App-internal | The calibration a forward pass fits and the backward pass reuses |
| `AdaptiveDecomp._calibrate()`, `_set_centroids()` | App-internal | Called by `__init__` |
| `AdaptiveDecomp._whiten()`, `_separate()`, `_project()` | App-internal | Called by `run` |
| `AdaptiveDecomp._detect_spikes_with_context()`, `_detect_spikes()` | App-internal | Called by `run` |
| `AdaptiveDecomp._kl_divergence()`, `_wh_loss()`, `_contrast_value()`, `_sv_loss()` | App-internal | Losses, called by `run` |
| `AdaptiveDecomp._update_separation_vectors()` | App-internal | Called by `run` |

---

## `models.py`

| Symbol | Category | Reachable via |
|---|---|---|
| `SignalImport` | User-exposed | Returned by `load_signal`; used by `decomp`, `cache`, API services |
| `FloatArray`, `IntArray`, `BoolArray` | App-internal | Array annotations in `signal`, `decomp`, `editing` |
| `EditSignalContext` | App-internal | Built by `decomp.decomposition_file`; held by the `EditSession` |
| `LoadedDecomposition` | App-internal | Used by `decomp.decomposition_file` |
| `DecompositionSignalExport` | App-internal | Used by `decomp.postprocess.export_step` |
| `DecompositionExport` | App-internal | Used by `decomp.postprocess.export_step` |
| `_as_2d_float_array()` | App-internal | Called by `SignalImport.from_mapping` |
| `_as_name_list()` | App-internal | Called by `SignalImport.build` for gridname/muscle coercion |
| `_ensure_channel_matrix()` | App-internal | Called by `SignalImport.from_mapping` |

---

## `paths.py`, `app_log.py`

| Symbol | Category | Reachable via |
|---|---|---|
| `paths.cache_dir()` | App-internal | Session stores, edit logs (`MUEDIT_CACHE_DIR` overrides) |
| `paths.log_dir()` | App-internal | `cli.serve_api` |
| `paths.repo_root()`, `paths.frontend_dir()` | App-internal | `config.default_data_root`, `cli` |
| `app_log.log_to_file()` | App-internal | Called by `cli.serve_api` |
| `app_log.log_to_inherited_file()` | App-internal | Called by `decompose_worker.child_main` |

## `cli.py`

| Symbol | Category | Reachable via |
|---|---|---|
| `serve_api()` | User-exposed | `muedit-api` entry point |
| `run_decomposition_cli()` | User-exposed | `muedit-decompose` entry point |
| `main()` | User-exposed | `python -m muedit.cli` (dispatcher) |
| `_parse_roi(value)` | App-internal | Called by `run_decomposition_cli` |
| `_parse_rois(value)` | App-internal | Called by `run_decomposition_cli` |
