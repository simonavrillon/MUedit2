# 07 — Dead Code Candidates

Analysis of functions, parameters, and code paths that may be unreachable, deprecated, or never reached. The codebase is notably clean -- no commented-out code, no TODO/FIXME removal markers were found. The candidates below are the only items worth auditing.

---

## Summary

| Category | Count |
|---|---|
| Extension API (intentionally no internal callers) | 1 |
| Deprecated fields/flags (still functional, backward compat) | 3 |
| Unused parameters (reserved for future use) | 1 |
| Unexported public functions (not in `__all__` but could be) | 2 |
| Unreachable branches | 0 found |
| Commented-out code | 0 found |

---

## 1. Extension API: `register_loader`

| Location | `io/factory.py` |
|---|---|
| Exports | `muedit.__init__`, `muedit.io.__init__` |
| Internal callers | None |
| Status | **Intentional** extension point for user-registered loaders |

This is exported in the top-level package and the `io` sub-package, but is never called from within the codebase. It exists to allow users to register custom file loaders via `register_loader(".ext", my_loader)`. Not dead code -- this is a public API by design.

---

## 2. Deprecated: `muscle_names` field in `EditSavePayload`

| Location | `api/schemas.py` |
|---|---|
| Status | **Deprecated alias** for `muscle` |
| Still accepted | Yes |

The `EditSavePayload` model has a `muscle_names` field marked as a deprecated alias for `muscle`. It is still accepted for backward compatibility. If no frontend code sends `muscle_names`, this field can be removed.

**Action**: Check if the frontend still sends `muscle_names`. If not, remove the field.

---

## 3. Deprecated: `--use-adaptive` and `--full-trace` CLI flags

| Location | `cli.py` |
|---|---|
| Status | **Deprecated**, superseded by `--postprocess` |
| Still functional | Yes |

Both flags are marked "Deprecated alias for --postprocess" in the help text. They map to `POSTPROCESS_MODES["adaptive"]` and `POSTPROCESS_MODES["full-trace"]` respectively. If no scripts or users still use them, they can be removed.

**Action**: Check `scripts/` and any automation for usage. If unused, remove both flags and keep only `--postprocess`.

---

## 5. Unexported Public Functions

These functions are not in `signal.__all__` but are public (no underscore prefix) and called by the QC pipeline:

| Function | Location | Caller |
|---|---|---|
| `detect_bad_channels()` | `signal/channel_qc.py` | `qc_pipeline.run_auto_qc` |
| `detect_artifact_mask()` | `signal/artifact_mask.py` | `detect_artifact_masks` |
| `detect_artifact_masks()` | `signal/artifact_mask.py` | `qc_pipeline.run_auto_qc` |
| `detect_bad_channels_per_grid()` | `signal/channel_qc.py` | `qc_pipeline.run_auto_qc` |
| `channel_qc_diagnostics()` | `signal/channel_qc.py` | `detect_bad_channels` |

These are app-internal (not user-exposed via `__all__`) but are public functions without underscore prefixes. This is not dead code, but the naming convention is inconsistent -- some internal helpers use underscore prefixes, these do not.

**Action**: If the convention is "underscore = internal", these should either be renamed or added to `__all__` if they are intended to be public.

---

## 6. Branches That May Be Unreachable

No unreachable branches were identified by the exploration. The codebase uses defensive coding (try/except, fallbacks, None checks) consistently. All conditional branches in the decomposition pipeline, editing operations, and QC pipeline appear to be reachable through their respective parameter combinations.

### Areas to verify manually

| Area | What to check |
|---|---|
| `operations.py: _recompute_spikes_in_window` | The `lock_spikes=True` path -- verify the frontend actually sends `lock_spikes=True` for any operation |
| `operations.py: _recompute_spikes_in_window` | The `use_peeloff=True` path -- verify the frontend sends `use_peeloff=True` |
| `decomp/postprocess.py: postprocess_step` | The `full_trace=True` mode (dewhitened filter application) -- verify this mode is actually used (vs. windowed and adaptive) |
| `decomp/algorithm.py: fixed_point_alg` | The `"kurtosis"` and `"logcosh"` contrast functions -- verify the frontend/CLI ever passes `contrast_func != "skew"` |
| `adapt_decomp/adaptation.py: AdaptiveDecomp._init_contrast_calibration` | Called from `__init__` -- verify the contrast calibration path is actually exercised |

---

## 7. Test Coverage Gaps

Modules with no direct unit tests (only indirect coverage through integration tests):

| Module | Indirect Coverage Via |
|---|---|
| `cli.py` | None |
| `models.py` | `io` tests (via `SignalImport`) |
| `signal/grid.py` | `decomp` pipeline tests (via `format_hdemg_signal`) |
| `signal/downsample.py` | Preview service (indirect) |
| `decomp/preview.py` | `decomp` pipeline tests (indirect) |
| `decomp/types.py` | `decomp` pipeline tests (indirect) |
| `editing/operations.py` | `test_bids_frontend_fields` (indirect via API) |
| `adapt_decomp/config.py` | `decomp` postprocess tests (indirect) |
| `api/cache.py` | None direct (used by all services) |
| `api/common.py` | None direct (used by all services) |
| `api/binary.py` | None direct (used by services) |
| `api/services/preview_service.py` | Route tests (indirect) |
| `api/services/decompose_service.py` | Route tests (indirect) |
| `api/services/editing_service.py` | `test_bids_frontend_fields` (indirect) |
| `api/services/bids_helpers.py` | None direct |
| `api/services/edit_helpers.py` | None direct |
| `api/routes/dialog.py` | None |

**Action**: Priority targets for direct unit tests: `editing/operations.py` (complex, only indirectly tested), `api/cache.py` (thread safety, eviction logic), `api/binary.py` (wire format correctness).

---

## 8. No Dead Code Found In

The following were explicitly checked and are clean:

- No commented-out function definitions (`# def ...`)
- No commented-out return statements (`# return ...`)
- No TODO/FIXME/XXX/HACK markers mentioning removal
- No "dead code", "no longer used", "remove this" comments
- No unused imports detected (would be caught by ruff, which is in dev dependencies)
