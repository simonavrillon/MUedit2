# Contracts

The rules both sides of a boundary depend on: what the frontend and backend must agree on, and
what files on disk promise to later versions. Field-level details live in the code named in
each section; change these only together with the other side, and with a test.

## HTTP

- Every route sits under `/api/v1` and is registered in `api/routes/__init__.py`. The frontend
  names each path once, in `frontend/src/api/routes.js`, and calls it through
  `frontend/src/api/client.js`. `tests/test_api_app.py` (`LIVE_ENDPOINTS`) fails when the two
  drift.
- Request bodies are Pydantic models in `api/schemas.py`. A missing or mistyped field is a 422.
  Unknown decomposition `params` are refused (422), as the CLI refuses unknown flags.
- Success: `{"data": <data>, "meta": {"api_version": "v1"}}` (`common.success_payload`).
- Error: `{"error": {"code", "message", "detail"}}` (`api/errors.py`).

### Errors

| Raised | Status | `code` | Client sees |
|---|---|---|---|
| `MueditError` (`muedit/errors.py`) | 400 | `http_400` | `message` as-is; `detail.field` names the request field at fault |
| `NotFoundError` / `ConflictError` | 404 / 409 | `http_404` / `http_409` | same |
| `RequestValidationError` | 422 | `validation_error` | `message` "Request validation failed", the Pydantic errors in `detail`; the page appends the first one to the message (`parseApiError`) |
| anything else | 500 | `internal_error` | a generic message; the traceback goes to `muedit.log` only |

Services raise a `MueditError` subclass for every condition the user can fix
(`SignalLoadError`, `DecompositionFileError`, `BidsFieldError`, `EditError`) and let the handler
answer. A 500 means a bug.

Logging: a user error is one INFO line, no traceback. A degradation the request survives (an
unreadable sidecar, a failed BIDS side output) is one WARNING naming what was lost. A bug is an
ERROR with its traceback.

### Tokens and expiry

| Token | Minted by | Expires when | Client recovery |
|---|---|---|---|
| `upload_token` | `/preview-by-path` | the tab opens another file, cache eviction, server restart | `withUpload` (`app/services/upload.js`) re-posts the path and retries once |
| `run_result_token` | the run's `done` event | the tab's next run, session close | none: the save reports it |
| edit `token` | `/edit/session/open` | another file opened in the tab, session close, server restart | a page reload reopens it from `sessionStorage`; otherwise "open the file again" |

The client keys its recovery on `ApiError.field` (`detail.field` of the error), never on the
message text.

### Session header

`http.js` creates one id per page load and sends it as `X-MUedit-Session` (1–64 letters,
digits or `-`; anything else is a 400). Requests without it use the `default` session.
`/session/close` is a `keepalive` fetch, not a beacon, because a beacon cannot carry the header.

## MUB1 frames

The one binary format, server to client only (`api/binary.py: pack_frame`;
`frontend/src/api/binary-payloads.js: decodeFrame`). Little-endian:

```text
"MUB1" | header_len: uint32 | header: UTF-8 JSON | pad to 8 |
array data, each array at an 8-byte-aligned offset from the data start

header = {"meta": {...}, "arrays": [{"name", "dtype", "shape", "offset"}, ...]}
dtype ∈ f4, i4, i8
```

The alignment lets the page read every array as a zero-copy typed-array view. Discharge times
of several MUs travel as a CSR pair (`spikes` + `spike_offsets`). Media type
`application/x-muedit-frame`, header `x-muedit-format: mub1`.

Test helpers mirror both ends: `tests/_frames.py` decodes like the page; `frontend/tests/_frames.js`
encodes like `pack_frame`. Change all four together.

| Frame | Routes | Arrays |
|---|---|---|
| series | `/series/emg`, `/series/overview`, `/series/aux` | `min`/`max` `f4[rows, bins]`, or `samples` `f4[rows, n]` when `meta.kind == "samples"` |
| pulse | `/series/pulse` | as series, plus `spikes`, `spike_values` (and `artifacts`, `artifact_values` for an edit session); `meta.version` |
| edit state | `/edit/session/open`, `GET /edit/session`, `/edit/session/recover` | CSR spikes of every MU |
| edit change | `/edit/ops/{op}` | CSR spikes of the `changed` MUs only |

### Edit frames

Every state and change frame, and the session save result, carry the **per-MU fields**: `n_mu`,
`mu_uids`, `mu_grid_index`, `flagged`, `versions`, `has_pulse`, `dirty`, `can_undo`,
`crash_recovery`.

- `versions[mu]` changes with every edit of that MU. The page caches pulse windows by it and
  refetches when it changes.
- A change frame's `kept_indices` (MUs removed or reordered) must be applied to every per-MU
  array before anything else (`keepEditMus`).
- History: the client holds a tail of the server's log starting at `edit_history_start`. A change
  frame says cut at `history_start`, then append `history`.

## NDJSON progress (`/decompose_stream`)

One JSON object per line (`application/x-ndjson`), produced by `services/decompose_service.py`
and folded into `state.runLive` by `decomp/live.js`.

| Event | Fields |
|---|---|
| progress | `message`, `phase` (`load` \| `preprocess` \| `decompose` \| `postprocess` \| `export`) |
| decompose search | + `grid`, `ngrid`, `window`, `nwindows`, `niter`, `iter`, `outcomes` (one letter per iteration since the last event: `k` kept, `r` rejected, `f` too few spikes), `window_done` |
| `stage: "done"` | `preview: {run_result_token?, mu_grid_index}`, `summary: {n_mu, grid_names, parameters}` |
| `stage: "error"` | `message`, `detail` (a `MueditError`'s message, else a generic one) |
| `stage: "cancelled"` | `message` |

The page reads structured fields, never message text. A stream that ends without `done`,
`error` or `cancelled` is a failed run.

## Edit operations

`/edit/ops/{op}` with the op's model from `api/schemas.py: EDIT_OPS`. `EditSession.apply`
dispatches the op name to the method of the same name (`-` → `_`), listed in
`EditSession.OPS`.

- **Op names and argument names are persisted.** The edit log stores `{"op", "args"}` and
  replays them through `apply` after a crash, so renaming an op or an argument breaks recovery
  of logs written before the change.
- **History entry types are persisted** in the `.json` edit log saved next to every `.npz`
  (listed in [docs/saved-files.md](../docs/saved-files.md#edit-log)). Add types; don't
  rename them.
- Every operation either completes or changes nothing: a failure rolls back its undo steps.
- `remove-duplicates` and a save clear the undo stack.

## Files on disk

- **Decomposition NPZ** (`decomp/decomposition_file.py`): `schema_version` 2, uncompressed,
  64-byte-aligned members, no pickle, discharge times as CSR. A file with a newer schema is
  refused; v1 files are still read through a restricted unpickler. A new key must stay optional
  on read so older files still open. The schema is documented for users in
  [docs/saved-files.md](../docs/saved-files.md).
- **Discharge times** are 0-based sample indices everywhere in memory. MATLAB files are 1-based
  and shifted on read.
- **Edit log** (`editing/edit_log.py`): `<cache dir>/edit-logs/<file key>-<token>.jsonl`, a header
  naming the file (path, size, mtime) then one line per operation. A log for a different version
  of the file is never replayed.
- **BIDS**: folders and names come only from `io/_bids_layout.py: BidsEntities`. Loader
  metadata keys that survive into a BIDS export are listed once, in
  `decomp/decomposition_file.py: LOADER_BIDS_META_KEYS`.
