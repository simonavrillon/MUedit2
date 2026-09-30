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
| 7 | GET | `/decompose_preview/{token}` | `api.fetchDecomposePreview(token)` | `handleStreamMessage` (binary fast-path) | 120s | Fetch heavy MU arrays in binary format |
| 8 | POST | `/edit/load-by-path` | `api.editLoadByPath(filepath)` | `editStage.loadDecompositionForEdit` | 120s | Load decomposition file by server path |
| 9 | POST | `/edit/add-spikes` | `api.editAction("add-spikes", payload)` | `requestRoiEdit` | 120s | Add spikes in a selected region |
| 10 | POST | `/edit/add-artifact` | `api.editAction("add-artifact", payload)` | `requestRoiEdit` | 120s | Mark an artifact region |
| 11 | POST | `/edit/delete-spikes` | `api.editAction("delete-spikes", payload)` | `requestRoiEdit` | 120s | Delete spikes in a selected region |
| 12 | POST | `/edit/delete-dr` | `api.editAction("delete-dr", payload)` | `requestRoiEdit` | 120s | Delete discharge-rate outliers in a selected range |
| 13 | POST | `/edit/update-filter` | `api.editMode("update-filter", payload)` | `requestFilterUpdate` | 120s | Re-run separation filter on current MU |
| 14 | POST | `/edit/remove-outliers` | `api.editRemoveOutliers(payload)` | `removeOutliers` | 120s | Remove outlier spikes from current MU |
| 15 | POST | `/edit/remove-duplicates` | `api.editRemoveDuplicates(payload)` | `removeDuplicateMus` | 120s | Remove duplicate MUs |
| 16 | POST | `/edit/flag-mu` | `api.editFlagMu(payload)` | `flagMuForDeletion` | 120s | Toggle MU deletion flag |
| 17 | POST | `/edit/save` | `api.editSave(payload, pulseTrains?)` | `saveEditedFile`, `autoSaveRunDecomposition` | 120s | Save edited decomposition to .npz |

> **Origin and token.** The page and the API share one origin (the server serves `frontend/` at `/`), so `API_BASE` is `${location.origin}/api/v1` and there is no CORS. In the desktop app (`?desktop=1`), `platform.initPlatform()` takes a token from the pywebview bridge and `apiFetch` sends it as `X-MUedit-Token` on every request; the server answers 401 (`unauthorized`) without it, except for `/health`. `closeSession()` posts `/session/close` with `fetch(..., { keepalive: true })` rather than a beacon, because a beacon cannot carry that header.

> Files are only ever opened by path through the native dialog. The browser-upload routes (`POST /preview`, `POST /edit/load`) and their client code were removed (audit F2/F4); `tests/test_api_http.py` fails if `routes.js` names a route the backend does not serve.

## Route Definitions (`api/routes.js`)

```javascript
export const routes = {
  seriesEmg: "/series/emg",
  seriesOverview: "/series/overview",
  seriesAux: "/series/aux",
  qcAuto: "/qc/auto",
  previewByPath: "/preview-by-path",
  decomposeStream: "/decompose_stream",
  decomposeCancel: "/decompose/cancel",
  decomposePreview: (token) => `/decompose_preview/${encodeURIComponent(token)}`,
  editSave: "/edit/save",
  editAction: (action) => `/edit/${action}`,
  editMode: (mode) => `/edit/${mode}`,
  editRemoveOutliers: "/edit/remove-outliers",
  editRemoveDuplicates: "/edit/remove-duplicates",
  editFlagMu: "/edit/flag-mu",
  editLoadByPath: "/edit/load-by-path",
  dialogOpenFile: "/dialog/open-file",
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

### POST /edit/load-by-path

```
Request:  { path: string }
Headers: Accept: application/octet-stream (implied)
Response (MUB1 frame or JSON fallback):
  {
    pulse_trains: number[][],
    pulse_trains_full: number[][],
    distime_all: number[][],
    grid_names: string[],
    mu_grid_index: number[],
    parameters: object,
    total_samples: number,
    fsamp: number,
    file_label: string,
    edit_signal_token: string,
    muscle?: string[],
    project?: string,
    participant_meta?: object,
    manufacturer?: string,
    artifact_times?: number[][],
    mu_uids?: string[],
    edit_history?: object[]
  }
```

### POST /edit/add-spikes

```
Request: {
  distimes: number[],
  mu_index: number,
  pulse_train: number[],
  fsamp: number,
  x_start: number,
  x_end: number,
  y_min: number,
  y_max: number
}
Response: { distimes: number[] }
```

### POST /edit/add-artifact

```
Request: same as add-spikes + { artifact_times: number[] }
Response: { distimes: number[], artifact_times: number[] }
```

### POST /edit/delete-spikes

```
Request: same as add-spikes + { artifact_times: number[] }
Response: { distimes: number[], artifact_times: number[] }
```

### POST /edit/delete-dr

```
Request: {
  distimes: number[],
  mu_index: number,
  pulse_train: number[],
  fsamp: number,
  x_start: number,
  x_end: number,
  y_min: number
}
Response: { distimes: number[] }
```

### POST /edit/update-filter

```
Request: {
  project: string,
  edit_signal_token: string,
  file_label: string,
  grid_index: number,
  mu_index: number,
  distimes: number[],
  mu_grid_index: number[],
  pulse_train: number[],
  view_start: number,
  view_end: number,
  use_peeloff: boolean,
  lock_spikes: boolean,
  flagged: boolean,
  artifact_times: number[]
}
Response: { distimes: number[], pulse_train: number[] }
```

### POST /edit/remove-outliers

```
Request: {
  distimes: number[],
  mu_index: number,
  pulse_train: number[],
  fsamp: number
}
Response: { distimes: number[] }
```

### POST /edit/remove-duplicates

```
Request: {
  distimes: number[][],
  fsamp: number,
  total_samples: number,
  mu_grid_index: number[],
  parameters: object
}
Response: { kept_indices: number[] /* ascending */, distimes: number[][], removed_count: number }
```

### POST /edit/flag-mu

```
Request: {
  distimes: number[],
  mu_index: number,
  flag: boolean
}
Response: { flag: boolean }
```

### POST /edit/save

```
Request: MUB1 frame (application/x-muedit-frame) with this object as meta and
         `pulse_trains` as an f4 [n_mu, total_samples] array; or plain JSON
         when there are no pulse trains to send (the run save sends
         `run_result_token` instead, and the server uses its stored copy)
{
  distimes: number[][],
  flagged: boolean[],
  run_result_token?: string,
  total_samples: number,
  fsamp: number,
  grid_names: string[],
  mu_grid_index: number[],
  mu_uids: string[],
  parameters: object,
  muscle: string[],
  edit_history: object[],
  artifact_times: number[][],
  artifact_regions: number[][],
  entity_label: string,
  file_label: string,
  edit_signal_token: string,
  software_versions: object
}
Response: { mode: string, path: string, keptIndices: number[], editHistory: object[] }
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

### MUB1 frame — `/series/*`, `/edit/load-by-path`, `/decompose_preview/{token}`, `/edit/save` request

```
Offset      Size        Field
0           4           magic "MUB1"
4           4           uint32 headerLen
8           headerLen   JSON {meta, arrays: [{name, dtype, shape, offset}]}
aligned 8   ...         array data; each array at dataStart + offset (8-byte aligned)
```

`decodeFrame` returns typed-array views into the response buffer (no copy);
`encodeFrame` writes rows straight into one `ArrayBuffer`. dtypes: `f4`,
`i4`, `i8`, `u1`, `i2`. Pulse matrices are still turned into `number[][]` for
state until the edit session moves server-side.

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

### Silent Failure (Ambiguous .mat)

When loading a `.mat` file that could be raw or decomposition:
1. Raw preview is attempted with `silentFailure: true`
2. If it fails, the decomposition load path is tried
3. The user does not see the raw preview failure message
