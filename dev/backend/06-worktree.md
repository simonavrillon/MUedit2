# 06 — Worktree: User-Exposed vs App-Internal

This worktree maps every function/symbol in the backend to one of three categories:

- **User-exposed** — reachable by the user via API endpoints, CLI, or public package exports
- **App-internal** — called by other backend modules but not directly by the user
- **Extension API** — exported but no internal callers (intentional extension points)

Use this to trace what the user can reach and identify dead code (see `07-dead-code-candidates.md`).

---

## `api/` — HTTP API Layer

### `routes/preview.py`

| Symbol | Category | Reachable via |
|---|---|---|
| `preview_router` | User-exposed | `include_routers()` |
| `GET /health` | User-exposed | HTTP |
| `POST /preview` | User-exposed | HTTP |
| `POST /preview-by-path` | User-exposed | HTTP |
| `POST /qc/window` | User-exposed | HTTP |

### `routes/decompose.py`

| Symbol | Category | Reachable via |
|---|---|---|
| `decompose_router` | User-exposed | `include_routers()` |
| `POST /decompose` | User-exposed | HTTP |
| `POST /decompose_stream` | User-exposed | HTTP |
| `GET /decompose_preview/{token}` | User-exposed | HTTP |

### `routes/editing.py`

| Symbol | Category | Reachable via |
|---|---|---|
| `editing_router` | User-exposed | `include_routers()` |
| `GET /config` | User-exposed | HTTP |
| `POST /edit/load` | User-exposed | HTTP |
| `POST /edit/load-by-path` | User-exposed | HTTP |
| `POST /edit/save` | User-exposed | HTTP |
| `POST /edit/update-filter` | User-exposed | HTTP |
| `POST /edit/add-spikes` | User-exposed | HTTP |
| `POST /edit/add-artifact` | User-exposed | HTTP |
| `POST /edit/delete-spikes` | User-exposed | HTTP |
| `POST /edit/delete-dr` | User-exposed | HTTP |
| `POST /edit/remove-outliers` | User-exposed | HTTP |
| `POST /edit/remove-duplicates` | User-exposed | HTTP |
| `POST /edit/flag-mu` | User-exposed | HTTP |

### `routes/dialog.py`

| Symbol | Category | Reachable via |
|---|---|---|
| `dialog_router` | User-exposed | `include_routers()` |
| `GET /open-file` | User-exposed | HTTP |
| `_open_dialog_macos()` | App-internal | Called by route handler (macOS) |
| `_open_dialog_tkinter()` | App-internal | Called by route handler (non-macOS) |

### `services/preview_service.py`

| Symbol | Category | Reachable via |
|---|---|---|
| `build_preview(file)` | App-internal | `POST /preview` route |
| `build_preview_from_path(path)` | App-internal | `POST /preview-by-path` route |
| `get_qc_window(payload)` | App-internal | `POST /qc/window` route |
| `_build_preview_core(filepath)` | App-internal | Called by `build_preview`/`build_preview_from_path` |
| `_encode_qc_raw_f32(...)` | App-internal | Called by `get_qc_window` |
| `_decomp_artifact_error(field)` | App-internal | Called by `build_preview` |

### `services/decompose_service.py`

| Symbol | Category | Reachable via |
|---|---|---|
| `run_decomposition_once(...)` | App-internal | `POST /decompose` route |
| `decomposition_event_stream(...)` | App-internal | `POST /decompose_stream` route |
| `fetch_decompose_preview_binary(token)` | App-internal | `GET /decompose_preview/{token}` route |
| `resolve_decompose_input(...)` | App-internal | Called by decompose routes |
| `parse_stream_options(...)` | App-internal | Called by decompose_stream route |
| `_as_f32_matrix(value)` | App-internal | Called by `_encode_decompose_preview_f32` |
| `_encode_decompose_preview_f32(preview)` | App-internal | Called by `decomposition_event_stream` |

### `services/editing_service.py`

| Symbol | Category | Reachable via |
|---|---|---|
| `load_decomposition(file)` | App-internal | `POST /edit/load` route |
| `load_decomposition_binary(file)` | App-internal | `POST /edit/load` route (binary) |
| `load_decomposition_from_path(path)` | App-internal | `POST /edit/load-by-path` route |
| `load_decomposition_binary_from_path(path)` | App-internal | `POST /edit/load-by-path` route (binary) |
| `save_edits(payload)` | App-internal | `POST /edit/save` route |
| `update_filter(payload)` | App-internal | `POST /edit/update-filter` route |
| `add_spikes(payload)` | App-internal | `POST /edit/add-spikes` route |
| `add_artifact(payload)` | App-internal | `POST /edit/add-artifact` route |
| `delete_spikes(payload)` | App-internal | `POST /edit/delete-spikes` route |
| `delete_dr(payload)` | App-internal | `POST /edit/delete-dr` route |
| `remove_outliers(payload)` | App-internal | `POST /edit/remove-outliers` route |
| `remove_duplicates_service(payload)` | App-internal | `POST /edit/remove-duplicates` route |
| `flag_mu(payload)` | App-internal | `POST /edit/flag-mu` route |
| `_init_loaded_decomp(filepath, file_label)` | App-internal | Called by load functions |
| `_encode_edit_load_f32(loaded)` | App-internal | Called by binary load functions |
| `_wrap_edit_load_binary(loaded)` | App-internal | Called by binary load functions |
| `_dedup(...)` | App-internal | Called by `save_edits`/`remove_duplicates_service` |
| `_export_bids_from_mat_context(...)` | App-internal | Called by `save_edits` |

### `services/bids_helpers.py`

| Symbol | Category | Reachable via |
|---|---|---|
| `read_bids_sidecar_meta(root, entity_label)` | App-internal | Called by preview/editing services |
| `_load_bids_grid(...)` | App-internal | Called by `update_filter` |
| `_parse_all_bids_entities(entity_label)` | App-internal | Called by editing services |
| `_parse_subject_session_from_entity_label(label)` | App-internal | Called by `read_bids_sidecar_meta` |
| `_infer_bids_root_from_decomp_path(filepath)` | App-internal | Called by editing services |
| `_normalize_bids_meta_value(value)` | App-internal | Called by `read_bids_sidecar_meta` |
| `_grid_sort_key(group)` | App-internal | Called by `_read_bids_channels_sidecar` |
| `_read_bids_channels_sidecar(path)` | App-internal | Called by `load_decomposition_from_path` |

### `services/edit_helpers.py`

| Symbol | Category | Reachable via |
|---|---|---|
| `_expected_grid_count(loaded)` | App-internal | Called by `save_edits` |
| `_pad_grid_names(names, expected, fallback)` | App-internal | Called by `save_edits` |
| `_normalize_muscle_names(raw)` | App-internal | Called by `save_edits` |
| `_normalize_flagged(raw, nmu)` | App-internal | Called by `save_edits` |
| `_generate_mu_uids(mu_grid_index)` | App-internal | Called by `save_edits` |
| `_normalize_mu_grid_index(raw, nmu)` | App-internal | Called by `save_edits` |
| `_coerce_dup_tol(raw, default)` | App-internal | Called by `save_edits`/`remove_duplicates_service` |

### `schemas.py`

| Symbol | Category | Reachable via |
|---|---|---|
| `PathPayload` | User-exposed | `POST /preview-by-path`, `POST /edit/load-by-path` |
| `QcWindowPayload` | User-exposed | `POST /qc/window` |
| `EditSavePayload` | User-exposed | `POST /edit/save` |
| `EditFilterPayload` | User-exposed | `POST /edit/update-filter` |
| `EditRoiPayload` | User-exposed | `POST /edit/add-spikes`, `/edit/add-artifact`, `/edit/delete-spikes`, `/edit/delete-dr` |
| `EditOutliersPayload` | User-exposed | `POST /edit/remove-outliers` |
| `EditDeduplicatePayload` | User-exposed | `POST /edit/remove-duplicates` |
| `EditFlagPayload` | User-exposed | `POST /edit/flag-mu` |

### Other API modules

| Symbol | Category | Reachable via |
|---|---|---|
| `app_factory.create_app()` | App-internal | Called by `cli.serve_api` |
| `routes.include_routers()` | App-internal | Called by `cli.serve_api` |
| `contracts.success_payload()` | App-internal | Called by all route handlers |
| `binary.pack_json_f32_payload()` | App-internal | Called by preview/decompose/edit services |
| `errors.register_exception_handlers()` | App-internal | Called by `create_app` |
| `errors.error_payload()` | App-internal | Called by exception handlers |
| `errors.http_exception_handler` | App-internal | Registered on app |
| `errors.validation_exception_handler` | App-internal | Registered on app |
| `errors.unhandled_exception_handler` | App-internal | Registered on app |
| `config.DATA_ROOT` | App-internal | Used by `GET /config`, `resolve_bids_root` |
| `config.resolve_bids_root()` | App-internal | Called by decompose/edit services |
| `common.build_params()` | App-internal | Called by decompose service |
| `common.make_json_safe()` | App-internal | Called by many services |
| `common.parse_json()` | App-internal | Called by route handlers |
| `common.parse_discard_channels()` | App-internal | Called by decompose routes |
| `common.parse_rois()` | App-internal | Called by decompose routes |
| `common.parse_json_object()` | App-internal | Called by decompose routes |
| `common.safe_unlink()` | App-internal | Called by many services |
| `common.save_upload_to_temp()` | App-internal | Called by preview/editing services |
| `common.parse_entity_label()` | App-internal | Called by editing services |
| `common.serialize_preview()` | App-internal | Called by decompose service |
| `common.summarize_result()` | App-internal | Called by decompose service |
| `common._coerce_param_value()` | App-internal | Called by `build_params` |
| `cache._store_upload_signal()` | App-internal | Called by preview service |
| `cache._get_upload_signal()` | App-internal | Called by decompose service |
| `cache._store_qc_signal()` | App-internal | Called by preview service |
| `cache._get_qc_signal()` | App-internal | Called by preview service |
| `cache._store_decomp_preview_binary()` | App-internal | Called by decompose service |
| `cache._get_decomp_preview_binary()` | App-internal | Called by decompose service |
| `cache._store_edit_signal_context()` | App-internal | Called by editing service |
| `cache._get_edit_signal_context()` | App-internal | Called by editing service |
| `cache._get_edit_signal_context_by_label()` | App-internal | Called by editing service |
| `cache._clone_signal()` | App-internal | Called by cache store functions |
| `cache._purge_expired_caches_locked()` | App-internal | Called by cache operations |
| `cache._array_nbytes()` | App-internal | Called by eviction |
| `cache._signal_nbytes()` | App-internal | Called by eviction |
| `cache._qc_nbytes()` | App-internal | Called by eviction |
| `cache._evict_to_budget_locked()` | App-internal | Called by cache store functions |

---

## `decomp/` — Decomposition Engine

| Symbol | Category | Reachable via |
|---|---|---|
| `run_decomposition()` | User-exposed | `muedit.__init__`, CLI, API `/decompose` |
| `DecompositionParameters` | User-exposed | `muedit.__init__`, `muedit.decomp.__init__` |
| `load_step()` | App-internal | Called by `run_decomposition` |
| `preprocess_step()` | App-internal | Called by `run_decomposition` |
| `decompose_step()` | App-internal | Called by `run_decomposition` |
| `postprocess_step()` | App-internal | Called by `run_decomposition` |
| `export_step()` | App-internal | Called by `run_decomposition` |
| `select_roi_interactively()` | User-exposed | CLI `--manual-roi` |
| `batch_process_filters()` | App-internal | Called by `postprocess_step` |
| `rem_duplicates()` | App-internal | Called by `postprocess_step`, editing service |
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
| `downsample_vector()` | App-internal | Called by `build_preview_payload` |
| `load_decomposition_file()` | App-internal | Called by editing service |
| `load_decomposition_signal_context()` | App-internal | Called by editing service |
| `normalize_distimes()` | App-internal | Called by editing service |
| `build_pulse_trains_from_distimes()` | App-internal | Called by editing service |
| `save_editlog()` | App-internal | Called by editing service |
| `first_non_none()` | App-internal | Called by `decomp/io.py` internals |
| All `_`-prefixed functions | App-internal | Internal helpers |

---

## `io/` — File I/O

| Symbol | Category | Reachable via |
|---|---|---|
| `load_signal()` | User-exposed | `muedit.__init__`, `muedit.io.__init__`, pipeline |
| `clone_signal()` | User-exposed | `muedit.io.__init__`, pipeline |
| `get_loader()` | User-exposed | `muedit.io.__init__` |
| `register_loader()` | Extension API | `muedit.__init__`, `muedit.io.__init__` (no internal callers) |
| `supported_extensions()` | User-exposed | `muedit.io.__init__` |
| `load_mat()` | App-internal | Called via `factory._LOADERS` |
| `load_otb_plus()` | App-internal | Called via `factory._LOADERS` |
| `load_otb4()` | App-internal | Called via `factory._LOADERS` |
| `load_bids_signal()` | App-internal | Called via `factory._LOADERS` |
| `load_intan()` | App-internal | Called via `factory._LOADERS` |
| `export_bids_emg()` | App-internal | Called by `preprocess._export_raw_emg_bids`, editing service |
| `write_bids_dataset_description()` | App-internal | Called by `preprocess`, editing service |
| `export_bids_mu_derivatives()` | App-internal | Called by editing service |
| `build_entities()` | App-internal | Called by `export_bids_emg` |
| `load_bids_emg_grid()` | App-internal | Called by `bids_helpers._load_bids_grid` |
| `resolve_bids_emg_path()` | App-internal | Called by `_bids_reader`, `bids_helpers` |
| `select_grid_channels()` | App-internal | Called by `load_bids_emg_grid` |
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
| `detect_bad_channels()` | App-internal | Called by QC pipeline (not in `__all__`) |
| `detect_bad_channels_per_grid()` | App-internal | Called by QC pipeline |
| `channel_qc_diagnostics()` | App-internal | Called by `detect_bad_channels` |
| `detect_artifact_mask()` | App-internal | Called by `detect_artifact_masks` |
| `detect_artifact_masks()` | App-internal | Called by QC pipeline |
| `extend_signal()` | App-internal | Called by `decompose_step`, `operations.py` |
| `signed_square()` | App-internal | Called by `decompose_step`, `operations.py` |
| `find_refractory_peaks()` | App-internal | Called by `decompose_step`, `operations.py` |
| `split_by_amplitude()` | App-internal | Called by `decompose_step`, `operations.py` |
| `isi_cov()` | App-internal | Called by `rem_duplicates`, `minimize_isi_covariance` |
| `raw_series_at_fs()` | App-internal | Called by preview service |
| `moving_average_ms()` | App-internal | Called by preview service |
| `get_grid_electrode_metadata()` | App-internal | Called by `export_bids_emg` |
| `ArtifactMaskConfig` | App-internal | Used by artifact mask functions |
| `ChannelQCConfig` | App-internal | Used by channel QC functions |
| `ChannelQCMetrics` | App-internal | Returned by `channel_qc_diagnostics` |
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
| `delete_high_discharge_rate_spikes_in_roi()` | User-exposed | `muedit.editing.__init__`, editing service |
| `remove_discharge_rate_outliers()` | User-exposed | `muedit.editing.__init__`, editing service |
| `SpikeTimes` | User-exposed | `muedit.editing.__init__` (type alias) |
| `FilterUpdateResult` | User-exposed | `muedit.editing.__init__` (type alias) |
| `_recompute_spikes_in_window()` | App-internal | Called by `update_motor_unit_filter_window` |

---

## `adapt_decomp/` — Adaptive Online Decomposition

| Symbol | Category | Reachable via |
|---|---|---|
| `AdaptiveDecomp` | User-exposed | `muedit.adapt_decomp.__init__` |
| `Config` | User-exposed | `muedit.adapt_decomp.__init__` |
| `run_adaptive_decomposition()` | User-exposed | `muedit.adapt_decomp.__init__`, `adaptive_batch.py` |
| `AdaptiveDecomp.run()` | App-internal | Called by `run_adaptive_decomposition` |
| `AdaptiveDecomp._whiten()` | App-internal | Called by `run` |
| `AdaptiveDecomp._separate()` | App-internal | Called by `run` |
| `AdaptiveDecomp._detect_spikes()` | App-internal | Called by `run` |
| `AdaptiveDecomp._kl_divergence()` | App-internal | Called by `_wh_loss` |
| `AdaptiveDecomp._wh_loss()` | App-internal | Called by `run` |
| `AdaptiveDecomp._contrast_value()` | App-internal | Called by `_sv_loss` |
| `AdaptiveDecomp._sv_loss()` | App-internal | Called by `run` |
| `AdaptiveDecomp._update_separation_vectors()` | App-internal | Called by `run` |
| `AdaptiveDecomp._init_whitening_calibration()` | App-internal | Called by `__init__` |
| `AdaptiveDecomp._init_contrast_calibration()` | App-internal | Called by `__init__` |

---

## `models.py`

| Symbol | Category | Reachable via |
|---|---|---|
| `SignalImport` | User-exposed | Used by `io.factory`, `cache`, `io.loaders` |
| `LoadedDecomposition` | App-internal | Used by `decomp.io` |
| `DecompositionSignalExport` | App-internal | Used by `decomp.postprocess.export_step` |
| `DecompositionExport` | App-internal | Used by `decomp.postprocess.export_step` |
| `_as_2d_float_array()` | App-internal | Called by `SignalImport.from_mapping` |
| `_ensure_channel_matrix()` | App-internal | Called by `SignalImport.from_mapping` |

---

## `cli.py`

| Symbol | Category | Reachable via |
|---|---|---|
| `serve_api()` | User-exposed | `muedit-api` entry point |
| `run_decomposition_cli()` | User-exposed | `muedit-decompose` entry point |
| `main()` | User-exposed | `python -m muedit.cli` (dispatcher) |
| `_parse_roi(value)` | App-internal | Called by `run_decomposition_cli` |
| `_parse_rois(value)` | App-internal | Called by `run_decomposition_cli` |
