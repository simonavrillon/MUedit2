# Architecture

How MUedit is put together, and the decisions behind its shape. The code holds the details
(signatures, fields, defaults); this page gives you the map to read it with. Wire formats are in
[contracts.md](contracts.md), change playbooks in [recipes.md](recipes.md).

## One process, one origin

`muedit api` (`cli.py`) starts one FastAPI server that serves the API under `/api/v1` and
`frontend/` at `/`. Because the page and the API share one origin there is no CORS, and when the
server is bound to loopback a `TrustedHostMiddleware` rejects DNS-rebound requests. The
frontend has no build step: the browser loads `frontend/app.js` as an ES module.

Startup (`api/app_factory.py`, lifespan): purge the session stores of exited processes and edit
logs older than 30 days, start the cache sweeper. Shutdown: stop running decompositions and
clear the caches.

## The four stages

```text
Import ──> QC / ROI ──> Decompose ──> Edit
landing    stageQc      stageRun      stageEdit
```

| Stage | Frontend | Backend | Server holds |
|---|---|---|---|
| Import | `stages/import-stage.js` | `routes/dialog.py`, `routes/preview.py` | the upload (`SignalImport` + QC pyramids) under `upload_token` |
| QC | `stages/qc-stage.js`, `view/qc-renderer.js`, `signal/` | `routes/preview.py` (`/qc/*`), `routes/series.py` | — |
| Decompose | `stages/run-stage.js`, `decomp/run.js`, `decomp/live.js` | `routes/decompose.py`, `services/decompose_*.py`, `decomp/` | the run's pulse trains under `run_result_token` |
| Edit | `stages/edit-stage.js`, `services/editing-service.js`, `view/edit-canvas.js` | `routes/editing.py`, `services/editing_service.py`, `editing/` | the `EditSession` under its `token` |

Opening a `.npz` (or a `.mat` decomposition) skips straight to Edit. A finished run saves itself
(`/edit/save`) and preloads the saved file into an edit session.

## The server holds the data; the page holds a window

Full-length arrays (EMG, pulse trains) never cross to the browser. The server keeps them, and
the page asks for the part it draws: `/series/*` returns a min/max envelope sized to the
canvas, or the raw samples when zoomed in far enough. Every request names what it reads by
token (`upload_token`, `run_result_token`, edit `token`), and a token the server no longer
holds is a 400 naming that field, which the page recovers from (see
[contracts.md](contracts.md#tokens-and-expiry)).

This is why:
- the run's `done` event carries a token, not the pulse trains or the discharge times;
- the edit page holds discharge times and per-MU fields, but fetches pulse trains window by window;
- every edit is one request (`/edit/ops/{op}`) answered with what changed.

## Sessions, memory and disk

Each browser tab sends a session id (`X-MUedit-Session`). Uploads, runs and edit sessions
belong to the tab that made them, one of each per tab; `/session/close` (sent on `pagehide`)
drops them.

- **Session stores** (`storage.py`): a folder of memory-mapped `.npy` files under
  `<cache dir>/sessions/`, one per upload, run or edit session. Loaders and the pipeline write
  full-length arrays into a store block by block, so a long recording never sits whole on the
  heap. `RamStore` is the heap stand-in when no store is given.
- **Caches** (`api/cache.py`, `api/memory.py`): every cache is a `BudgetedLRU` under one
  `MemoryBudget` (10% of RAM, 256 MB–1 GB). Only heap bytes count; memory-mapped arrays are free.
  The active session's entries and held entries are never evicted; idle sessions close after
  20 minutes.
- **Runs** execute in a `spawn`ed worker process (`services/decompose_worker.py`), one at a
  time (409 otherwise). Only file locations cross between the processes; progress returns
  over a pipe and is relayed to the page as NDJSON.

## Backend layers

```text
api/routes  ──>  api/services  ──>  editing  ──>  decomp  ──>  io  ──>  signal
                                                               (all over models, errors, storage)
```

Each layer imports only the layers to its right. `api/` holds everything HTTP: request models
(`schemas.py`), the envelope (`common.py`), error handlers (`errors.py`), MUB1 frames
(`binary.py`), caches. The domain packages know nothing of HTTP and raise `MueditError` for
anything the user can fix.

The decomposition pipeline (`decomp/pipeline.py: run_decomposition`) is five steps, each a
function with a dataclass output in `decomp/types.py`: load → preprocess (+ BIDS export) →
decompose (FastICA, `core.py`/`algorithm.py`) → postprocess (filters applied over the trace,
optionally adaptive; duplicates removed) → export (NPZ). The CLI and the API run the same
pipeline.

## Frontend shape

- **One context.** `app/create-app.js` merges every service factory (ui, file session, QC, run,
  edit stage) into one `app` object typed by `App` in `app/context.js`. A factory reads only
  `state`, `els` and `api` while it is constructed; it reaches other services as `app.x()`
  inside function bodies, so the stages can call each other.
- **What goes on `app`.** Only what another module calls through it (tests replace those
  members with recorders). A stage's own actions and pure helpers are imported where used.
- **State.** One `state` object (`state/state.js`) with a nested `state.edit` slice that mirrors
  the server's edit session. Mutations go through `state/actions.js`; a new raw file resets
  state through `state/transitions.js`.
- **Stages.** `app/stages/lifecycle.js` declares each stage's `blocked`, `enter`, `exit` and
  `render`. `render` draws only the active stage, once per frame; view modules only draw what
  they are given.
- **Types.** `tsc --checkJs` in `strict` mode over JSDoc annotations. Backend JSON enters typed
  as `JsonObject` and is normalised at the boundary (`api/payloads.js`, `api/binary-payloads.js`).

## Editing on the server

An `EditSession` (`editing/session.py`) holds one decomposition: discharge times as sorted
int32 arrays, pulse trains memory-mapped from the file (copied into the session store on an
MU's first refit, never written in the file), per-MU flags, uids and versions, an undo stack
(100 steps) and the edit history. The pure computations live in `editing/operations.py`.

Every applied operation is appended to an on-disk JSONL log (`editing/edit_log.py`) before the
answer goes back, so after a crash, reopening the same file offers to replay the unsaved edits.

Decisions worth knowing before changing this area:

- **Refits filter as the run did.** **Update filter** reruns the ICA steps on the grid's EMG
  notched and bandpassed over the whole recording (the FFT notch can't run on a window), with
  the run's saved `nbextchan`, `compute_dtype` and `peel_off_win`. The filtered grid is built
  once into the session store; the page asks for it ahead of time (`/edit/session/prepare-grid`)
  so the first refit doesn't wait.
- **One duplicate rule.** The edit stage's **Remove duplicates** and the save-time removal call
  `decomp.postprocess.dedup_survivors`, the pipeline's own, so they agree with the run.
- **Saves are atomic, BIDS side outputs best effort.** Both saves write the `.npz` and its
  `.json` edit log to temporary files and rename them over the previous pair; a failure there
  fails the save. The participant row, BIDS EMG and MU derivatives after it only report
  failures (`bids_emg_error`, `bids_warnings`).
- **A file saves back into its own dataset** while the picked project is still the one it
  opened from (`editing_service._dataset_root`). The page keeps it so: opening a
  decomposition locks the Projects tree and Change output folder (`setProjectLocked`), and
  opening a recording unlocks them.

## Known asymmetries

- `/qc/auto` runs on bandpassed EMG only; a run with `auto_mask_artifacts` (CLI only) runs QC on
  notched and bandpassed EMG, so the two can disagree on a recording with strong mains noise.
  The app never sets `auto_mask_artifacts`: its runs use the QC page's result as sent.

## Outputs

Every run exports into a BIDS dataset (`<data root>/<project>/`), and decompositions land in
its `derivatives/muedit/`. Formats are user-facing and documented in
[docs/saved-files.md](../docs/saved-files.md); BIDS layout lives in `io/_bids_layout.py`
(`BidsEntities`), the one place that knows where files sit.
