# 04 - API Surface

All HTTP endpoints used by the frontend, their payloads, and binary formats.

## Endpoint Table

| # | Method | Route | Client Method | Used By | Timeout | Purpose |
|---|---|---|---|---|---|---|
| 1 | GET | `/health` | `api.healthUrl()` | `initializeApp` → `waitForBackend` | 60s poll | Backend health check |
| 2 | GET | `/dialog/open-file` | `api.openFileDialog()` | `importStage.handleNativeDialogOpen` (browser only) | 120s | Open native OS file dialog; the desktop app uses its bridge's `open_file()` instead |
| 3 | POST | `/preview-by-path` | `api.fetchPreviewByPath(path)` | `qcStage.requestPreview` (with filepath) | 120s | Fetch preview metadata for raw file by path |
| 4 | GET | `/series/emg` | `api.fetchSeries("emg", params)` | `qcStage.requestQcGridWindow` | 120s | One min/max envelope per channel of a grid over the ROI (`QC_TRACE_BINS` bins) |
| 4b | GET | `/series/overview`, `/series/aux` | `api.fetchSeries(kind, params)` | `qcStage.requestPreview` | 120s | Whole-recording envelopes of each grid's mean \|EMG\| and of the aux channels (`OVERVIEW_BINS` bins) |
| 5 | POST | `/qc/auto` | `api.runAutoQc(payload)` | `qcStage.runAutoQc` | 300s | Run automatic QC: detect bad channels + artifact windows |
| 6 | POST | `/decompose_stream` | `api.decomposeStream(formData)` | `runStage.runDecomposition` | 15min | Main decomposition (streaming NDJSON response); 409 while another run is active |
| 6b | POST | `/decompose/cancel` | `api.cancelDecomposition()` | `runStage.cancelDecomposition` (Cancel button) | 120s | Stop this tab's run; its stream ends with `cancelled` |
| 7 | GET | `/decompose_preview/{token}` | `api.fetchDecomposePreview(token)` | `handleStreamMessage` (binary fast-path) | 120s | Fetch the run's discharge times in binary format |
| 8 | GET | `/series/pulse` | `api.fetchPulse(params)` | the run explorer and the edit canvas, through `createViewFetcher` | 120s | One MU's pulse train over the window on screen, with its discharges there |
| 9 | POST | `/edit/save` | `api.editSave(payload)` | `fileSession.persistNpzBySaveTarget` ← `autoSaveRunDecomposition` | 120s | Save a finished run (its pulse trains and discharge times stay on the server) |
| 10 | POST | `/edit/session/open` | `api.editOpen(filepath)` | `loadDecompositionForEdit` | 120s | Open a decomposition in a server-side edit session |
| 11 | POST | `/edit/session/recover` | `api.editRecover(token, apply)` | `loadDecompositionForEdit`, when the open reports `recoverable_edits` | 120s | Replay or drop the unsaved edits an earlier session left |
| 11b | POST | `/edit/session/prepare-grid` | `api.editPrepareGrid(token, grid, project)` | `prepareEditGrid` ← `showEditSession` (the grid on screen) and the grid dropdown | 10min | Filter a grid's EMG ahead of its first Update Filter; nothing waits for the answer |
| 12 | GET | `/edit/session` | `api.editSessionState(token)` | `restoreEditSession` (page reload) | 120s | The whole state of the session this page had open |
| 13 | POST | `/edit/ops/{op}` | `api.editOp(op, payload)` | `requestEditOp` ← every edit action | 120s | Apply one edit; the frame says what changed |
| 14 | POST | `/edit/session/save` | `api.editSessionSave(payload)` | `saveEditedFile` | 120s | Save the session's edits |
| 15 | POST | `/session/close` | `api.closeSession()` | `initializeApp`'s `pagehide` handler | — | Free what the server holds for this tab and stop its run |

> **Origin and token.** The page and the API share one origin (the server serves `frontend/` at `/`), so `API_BASE` is `${location.origin}/api/v1` and there is no CORS. In the desktop app (`?desktop=1`), `platform.initPlatform()` takes a token from the pywebview bridge and `apiFetch` sends it as `X-MUedit-Token` on every request; the server answers 401 (`unauthorized`) without it, except for `/health`. `closeSession()` posts `/session/close` with `fetch(..., { keepalive: true })` rather than a beacon, because a beacon cannot carry that header.

> Files are only ever opened by path through the native dialog. `tests/test_api_http.py` fails if `routes.js` names a route the backend does not serve.

> **Session header.** `http.js` creates one `SESSION_ID` per page load and `apiFetch` sends it as `X-MUedit-Session`. The server scopes the tab's upload, run and edit session to it, and `/session/close` frees them.

## Route Definitions (`api/routes.js`)

```javascript
export const routes = {
  seriesEmg: "/series/emg",
  seriesOverview: "/series/overview",
  seriesAux: "/series/aux",
  seriesPulse: "/series/pulse",
  qcAuto: "/qc/auto",
  previewByPath: "/preview-by-path",
  decomposeStream: "/decompose_stream",
  decomposeCancel: "/decompose/cancel",
  decomposePreview: (token) => `/decompose_preview/${encodeURIComponent(token)}`,
  editSave: "/edit/save",
  editSessionOpen: "/edit/session/open",
  editSession: "/edit/session",
  editSessionRecover: "/edit/session/recover",
  editSessionPrepareGrid: "/edit/session/prepare-grid",
  editSessionSave: "/edit/session/save",
  editOp: (op) => `/edit/ops/${op}`,
  dialogOpenFile: "/dialog/open-file",
  sessionClose: "/session/close",
  health: "/health",
};
```

---

## Payload Details

### GET /dialog/open-file

```
Request:  no body
Response: { path: string, name: string }
```

### POST /preview-by-path

```
Request:  { path: string }
Response: {
  project?: string,          // the file's project folder under the output folder, when it lies
                             //   in a BIDS dataset there; "" in a dataset elsewhere
  upload_token: string,
  grid_names: string[],
  total_samples: number,
  channel_means: number[][],
  coordinates: number[][][],
  metadata: {
    emg_hpf?, emg_lpf?, gains?,
    device_name?, bad_channels_per_grid?,
    manufacturer?, ...
  },
  muscle: string[],
  auxiliary_names: string[],
  fsamp: number,
  participant_meta?: { age?, sex?, handedness? },
  ...
}
```

### GET /series/emg, /series/overview, /series/aux

```
Query:    upload_token, start (default 0), end (0 = the end), bins (1-8192),
          grid (emg only)
Response: MUB1 frame, decoded by decodeSeriesFrame into
  {
    meta: { kind: "envelope" | "samples", start, end, bins, factor,
            total_samples, fsamp, names, grid? },
    rows: ({ min: Float32Array, max: Float32Array } | Float32Array)[]
  }
  One row per channel (emg), grid (overview) or aux channel. "samples" rows
  come when the window has no more samples than bins. Rows are views into the
  response buffer. The canvases draw an envelope as a zig-zag through each
  bin's max and min.
```

### POST /decompose_stream (FormData)

```
Request FormData:
  upload_token: string           (required; from /preview-by-path)
  params: string                 (JSON.stringify of buildDecomposeParams)
  persist_output: "false"
  discard_channels: string       (JSON.stringify of state.discardMasks, if any)
  rois: string                   (JSON.stringify of state.rois, if any)
  artifact_regions: string      (JSON.stringify of state.artifactRegions, if any)
  project: string                 (BIDS project name, if set)
  bids_entities: string          (JSON.stringify of collectBidsEntities, if any)
  bids_export: "true"
  full_preview: "true"

Response: NDJSON stream (one JSON object per line):
  { pct: number, message: string, stage?: string }
  { preview: {...}, binary_token?: string }
  { summary: { per_grid: [...], parameters: {...} } }
  { stage: "done" }
  { stage: "error", message: string }
  { stage: "cancelled", message: string }   (after /decompose/cancel, a disconnect or tab close)

A second run while one is active gets HTTP 409 before any stream starts.
```

### GET /decompose_preview/{token}

```
Headers: Accept: application/octet-stream
Response (MUB1 frame; the server serves it once):
  meta:   the preview's JSON fields (fsamp, total_samples, grid_names, rois,
          mu_grid_index, channel_means, coordinates, metadata, muscle)
  arrays: spikes i4 + spike_offsets i8 (CSR discharge times → distime_all)
Pulse trains stay on the server: /series/pulse with the run_result_token.
```

### GET /series/pulse

```
Query:    token (an edit session, or a run's run_result_token), mu, start, end, bins
Response: MUB1 frame, decoded by decodePulseFrame into a PulseView
  {
    mu, start, end, bins,
    version,            // changes with every edit of the MU: the edit stage
                        //   refetches when it differs from state.edit.versions[mu]
    flagged,
    row,                // { min, max } per bin, or the samples (like /series/*)
    spikes, spikeValues,        // discharges in view, and the train's value at each
    artifacts, artifactValues   // edit session only
  }
```

### Edit session frames

`/edit/session/open`, `/edit/session`, `/edit/session/recover` and `/edit/ops/{op}` all answer a
MUB1 frame, decoded by `decodeEditSessionFrame` into `{ meta, spikes, artifacts }`, where `spikes`
and `artifacts` are `Int32Array` rows cut from CSR arrays.

```
Every frame's meta carries the per-MU fields:
  n_mu, mu_uids, mu_grid_index, flagged, versions, has_pulse, dirty, can_undo

State frame (open, state, recover) — setEditSession(state, frame):
  meta: token, file_label, source_path, fsamp, total_samples, grid_names, rois,
        parameters, muscle, sil, project?, BIDS sidecar fields,
        edit_history, recoverable_edits, recovered_edits? (recover)
  rows: every MU's spikes and artifacts

Change frame (ops) — applyEditChange(state, frame):
  meta: changed (MU indices), history_start, history, kept_indices?
        (MUs were removed or reordered), removed_count?, fsamp?, undone?
  rows: spikes and artifacts of the `changed` MUs only
  The client cuts editHistory at history_start and appends history.
```

### POST /edit/session/open

```
Request:  { path: string }     // a .npz or .mat decomposition
Response: state frame
```

When `meta.recoverable_edits > 0`, `loadDecompositionForEdit` asks the user (`window.confirm`)
and posts `/edit/session/recover` with `{ token, apply }` before showing the file.

### POST /edit/ops/{op}

```
Request: { token, ...args }    // built by requestEditOp; unset args are left out
  add-spikes, add-artifact   { mu, x_start, x_end, y_min }          (drawn box → samples, pulse value)
  delete-spikes              { mu, x_start, x_end, y_min, y_max }
  delete-dr                  { mu, x_start, x_end, y_min }          (y_min: rate in Hz)
  update-filter              { mu, view_start, view_end, use_peeloff, lock_spikes, project }
  remove-outliers, reset, duplicate   { mu }
  flag                       { mu, flag }
  remove-duplicates, undo    {}
Response: change frame
```

### POST /edit/session/save

```
Request: withBidsSaveFields({ token, muscle, entity_label, file_label, software_versions })
         plus the session form's fields (project, participant_meta, powerline_freq, …)
Response: { saved, path, kept_indices, mu_uids, edit_history,
            bids_emg_paths?, bids_deriv_paths?, …per-MU fields }
          applyEditSave mirrors the saved file: flagged and duplicate MUs are gone.
```

### POST /edit/save (the run save)

```
Request: {
  run_result_token: string,     // the run's pulse trains and discharge times stay on the server
  distimes?: number[][],        // only when there is no token
  total_samples: number,
  fsamp: number,
  grid_names: string[],
  mu_grid_index: number[],
  parameters: object,
  muscle: string[],
  artifact_regions: number[][],
  file_label: string,
  ...BIDS form fields (withBidsSaveFields)
}
Response: { saved, path, kept_indices, mu_uids, edit_history, bids_emg_paths?, bids_deriv_paths? }
          400 (field: run_result_token) when the server no longer holds the run
```

---

## Decompose Parameters (Wire Format)

Built by `buildDecomposeParams()` in `decomp/params.js`:

| Wire Key | Value | Source DOM | Notes |
|---|---|---|---|
| `niter` | `raw.niter` | `#niter` | Iteration count (default 150) |
| `nwindows` | `raw.nwindows` | `#nwindows` | Number of analysis windows (default 1) |
| `nbextchan` | `1000` | hardcoded | Extension channels |
| `duplicatesthresh` | `raw.duplicatesthresh` | `#duplicatesthresh` | Duplicate threshold (default 0.3) |
| `sil_thr` | `raw.silVal` | `#silValue` | SIL threshold (default 0.9) |
| `cov_thr` | `raw.covVal` | `#covValue` | CoV threshold (default 0.5) |
| `covfilter` | `raw.covOn ? 1 : 0` | `#covToggle` | CoV filter on/off |
| `contrast_func` | `"skew"` | hardcoded | Contrast function |
| `initialization` | `0` | hardcoded | Init mode |
| `peel_off_enabled` | `raw.peelOn ? 1 : 0` | `#peelOffToggle` | Peel-off toggle |
| `peel_off_win` | `raw.peelWindow / 1000` | `#peelOffWindow` | Peel-off window (ms -> s) |
| `use_adaptive` | 0/1 | `#postprocessMode` | From postprocess mode flags |
| `full_trace` | 0/1 | `#postprocessMode` | From postprocess mode flags |
| `auto_mask_artifacts` | `0` | hardcoded | Auto-QC runs only on demand via `POST /qc/auto` |

### Post-processing Mode Flags

Mode keys match `POSTPROCESS_MODES` in `decomp/types.py` and the CLI `--postprocess` choices (checked by `tests/test_frontend_param_parity.py`).

| Mode | `use_adaptive` | `full_trace` |
|---|---|---|
| `windowed` (default) | 0 | 0 |
| `full-trace` | 0 | 1 |
| `adaptive` (beta) | 1 | 0 |

---

## Binary Payload Formats

All integers and floats are little-endian. When the magic prefix is absent, the buffer is parsed as JSON text.

### MUB1 frame — `/series/*`, `/decompose_preview/{token}`, the edit session routes

```
Offset      Size        Field
0           4           magic "MUB1"
4           4           uint32 headerLen
8           headerLen   JSON {meta, arrays: [{name, dtype, shape, offset}]}
aligned 8   ...         array data; each array at dataStart + offset (8-byte aligned)
```

`decodeFrame` returns typed-array views into the response buffer (no copy);
`csrRows` cuts CSR `values`/`offsets` pairs into one `Int32Array` view per MU.
dtypes: `f4`, `i4`, `i8`, `u1`, `i2`. Every frame goes from server to client;
`encodeFrame` only builds the decoder tests' input.

---

## Error Handling

### `http.js` — `parseApiError(res)`

Extracts error messages from JSON responses:
- Handles `error.message` (FastAPI format)
- Handles `detail` as string, array (Pydantic validation errors), or object

### `error-service.js` — `handleError(err, setStatus, label)`

```
console.error(err)
setStatus(`${label}: ${err.message}`, "error")
```

### Upload Token Expiry

The backend caches the loaded signal under the upload token; it is lost on backend restart or cache eviction. When `/decompose_stream` returns an `upload_token` error, `runDecomposition` (`decomp/run.js`):
1. Clears the token
2. Calls `POST /preview-by-path` with `state.file.path` to mint a fresh token (ROIs, channel masks and artifact regions stay in frontend state and are reused)
3. Retries the decomposition once; if the reload fails the original error is shown

The QC-stage calls (`/series/*`, `/qc/auto`) do not retry; they report the error.

### Edit Session Expiry

An edit call naming a session the server no longer holds (a server restart, the tab's session
closed) is a 400 on `token`: "Edit session expired; open the file again". On a page reload,
`restoreEditSession` reopens the session whose token `sessionStorage` kept; if it is gone, the
token is forgotten and the page starts empty.

### Silent Failure (Ambiguous .mat)

When loading a `.mat` file that could be raw or decomposition:
1. Raw preview is attempted with `silentFailure: true`
2. If it fails, the decomposition load path is tried
3. The user does not see the raw preview failure message
