# 04 - API Surface

All HTTP endpoints used by the frontend, their payloads, and binary formats.

## Endpoint Table

| # | Method | Route | Client Method | Used By | Timeout | Purpose |
|---|---|---|---|---|---|---|
| 1 | GET | `/health` | `api.healthUrl()` | `initializeApp` → `waitForBackend` | 60s poll | Backend health check |
| 2 | GET | `/dialog/open-file` | `api.openFileDialog()` | `importStage.handleNativeDialogOpen` | 120s | Open native OS file dialog |
| 3 | POST | `/preview-by-path` | `api.fetchPreviewByPath(path)` | `qcStage.requestPreview` (with filepath) | 120s | Fetch preview metadata for raw file by path |
| 4 | POST | `/qc/window` | `api.fetchQcWindow(payload, {preferBinary})` | `qcStage.requestQcGridWindow` | 120s | Fetch QC channel traces for a grid window |
| 5 | POST | `/qc/auto` | `api.runAutoQc(payload)` | `qcStage.runAutoQc` | 300s | Run automatic QC: detect bad channels + artifact windows |
| 6 | POST | `/decompose_stream` | `api.decomposeStream(formData)` | `runStage.runDecomposition` | 15min | Main decomposition (streaming NDJSON response) |
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
| 17 | POST | `/edit/save` | `api.editSave(payload)` | `saveEditedFile`, `autoSaveRunDecomposition` | 120s | Save edited decomposition to .npz |

> Files are only ever opened by path through the native dialog. The browser-upload routes (`POST /preview`, `POST /edit/load`) and their client code were removed (audit F2/F4); `tests/test_api_http.py` fails if `routes.js` names a route the backend does not serve.

## Route Definitions (`api/routes.js`)

```javascript
export const routes = {
  qcWindow: "/qc/window",
  qcAuto: "/qc/auto",
  previewByPath: "/preview-by-path",
  decomposeStream: "/decompose_stream",
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
  upload_token: string,
  mean_abs: number[],            // per-channel mean
  grid_mean_abs: number[][],     // per-grid mean
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
  auxiliary: number[][],
  auxiliary_names: string[],
  fsamp: number,
  participant_meta?: { age?, sex?, handedness? },
  ...
}
```

### POST /qc/window

```
Request:  {
  upload_token: string,
  grid_index: number,
  start: number,
  end: number,
  target_fs: 1000
}
Headers: Accept: application/octet-stream (when preferBinary)

Response (binary MQCR format or JSON fallback):
  {
    grid_index: number,
    channel_index: number,
    start: number,
    end: number,
    total_samples: number,
    fsamp: number,
    channels: [{ channel_index: number, series: number[] }]
  }
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
```

### GET /decompose_preview/{token}

```
Headers: Accept: application/octet-stream
Response (binary MDPV format or JSON fallback):
  {
    ...meta,
    pulse_trains_full: number[][],
    pulse_trains_all: number[][]
  }
```

### POST /edit/load-by-path

```
Request:  { path: string }
Headers: Accept: application/octet-stream (implied)
Response (binary MELD format or JSON fallback):
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
Response: { kept_indices: number[], ... }
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
Request: {
  distimes: number[][],
  flagged: boolean[],
  pulse_trains: number[][],
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
Response: { mode: string, path: string }
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

### MQCR (QC Raw Float32) — `/qc/window` response

```
Offset  Size  Field
0       4     magic "MQCR"
4       4     uint32 version (must be 1)
8       4     int32  grid_index
12      4     int32  channel_index
16      4     int32  start
20      4     int32  end
24      4     int32  total_samples
28      4     float32 fsamp
32      4     uint32 nChannels
36      ...   per channel:
  36+0  4     int32  channel_index
  36+4  4     uint32 n
  36+8  n*4   float32[n] samples
```

### MELD (Edit-Load Float32) — `/edit/load-by-path` response

```
Offset  Size    Field
0       4       magic "MELD"
4       4       uint32 version (must be 1)
8       4       uint32 metaLen
12      4       uint32 rows
16      4       uint32 cols
20      metaLen bytes   JSON metadata
20+m    rows*cols*4    float32[rows*cols] pulse train data
```

### MDPV (Decompose-Preview Float32) — `/decompose_preview/{token}` response

```
Offset  Size    Field
0       4       magic "MDPV"
4       4       uint32 version (must be 1)
8       4       uint32 metaLen
12      4       uint32 rowsFull
16      4       uint32 colsFull
20      4       uint32 rowsAll
24      4       uint32 colsAll
28      metaLen bytes   JSON metadata
28+m    rowsFull*colsFull*4  float32 full pulse trains
28+m+a  rowsAll*colsAll*4   float32 all pulse trains
```

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

The QC-stage calls (`/qc/window`, `/qc/auto`) do not retry; they report the error.

### Silent Failure (Ambiguous .mat)

When loading a `.mat` file that could be raw or decomposition:
1. Raw preview is attempted with `silentFailure: true`
2. If it fails, the decomposition load path is tried
3. The user does not see the raw preview failure message
