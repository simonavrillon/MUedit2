# MUedit Command Line

MUedit has a command-line interface for batch work and scripting. There are two
commands: `muedit api` starts the backend (the same server the app uses), and
`muedit decompose` runs a full decomposition without the interface.

Run them through `uv` from the project folder, or directly if MUedit is installed:

```bash
uv run muedit --help          # from the MUedit folder
muedit --help                 # when the package is installed
```

Running `muedit` with no arguments prints usage and exits with code 2; an unknown
command also exits 2. A decomposition that fails with a `MueditError` prints
`muedit: <message>` and exits 1.

---

## `muedit api`

Starts the MUedit server: the API under `/api/v1` and the frontend at `/` on the
same origin. This is the same server the launchers (`MUedit.command`,
`MUedit.bat`, `scripts/run_MUedit.*`) start; it takes **no flags**.

| Environment variable | Default | Effect |
|---|---|---|
| `MUEDIT_HOST` | `127.0.0.1` | Bind host. A loopback host enables a Host-header check so only this machine's browser can call the API. |
| `MUEDIT_PORT` | `8000` | Bind port. The page opens at `http://127.0.0.1:<port>/`. |

> **A note on the port.** The launcher scripts set `MUEDIT_PORT` from
> `MUEDIT_BACKEND_PORT` (`export MUEDIT_PORT="${MUEDIT_BACKEND_PORT:-8000}"`), so a
> `MUEDIT_PORT` you export yourself is **overwritten** when you launch via
> `MUedit.command` / `MUedit.bat` / `scripts/run_MUedit.*`. To change the port there,
> set **`MUEDIT_BACKEND_PORT`**. When you run `muedit api` directly, set
> **`MUEDIT_PORT`**. See [Environment variables](#environment-variables).

---

## `muedit decompose <filepath>`

Runs an end-to-end decomposition and writes a `.npz`. With `--bids-root` it also
exports the raw EMG into a BIDS dataset (see [saved-files.md](saved-files.md)).

`filepath` is a recording file (`.mat`, `.otb+`, `.otb4`, `.bdf`, `.edf`, `.rhd`,
`.oebin`) or a BIDS EMG / Intan / Open Ephys **recording folder**.

Flags marked *(app: …)* map to the same setting in the app's settings panel; flags
without an app setting are CLI-only.

### Input and region of interest

| Flag | Default | Description |
|---|---|---|
| `--duration N` | — | Analyze only the first N seconds. |
| `--manual-roi` | off | Pick the region of interest interactively with a plot. Needs the `plot` extra: `uv sync --extra plot`. |
| `--rois start,end[;start,end]` | — | Explicit analysis windows in samples, e.g. `--rois "0,20000;40000,60000"` (quote it: the shell reads `;` as the end of the command). |

### Core algorithm

| Flag | Default | Description |
|---|---|---|
| `--niter N` | `150` | FastICA iterations *(app: Iterations)*. Must be ≥ 1. |
| `--nwindows N` | `1` | Number of analysis windows per grid *(app: Analysis windows)*. Must be ≥ 1. |
| `--duplicatesthresh T` | `0.3` | Cross-correlation threshold for duplicate removal *(app: Duplicates thresh)*. |
| `--contrast-func F` | `skew` | FastICA contrast function: `skew`, `kurtosis`, or `logcosh`. |
| `--compute-dtype D` | `float32` | Working dtype of the filtered EMG and the extended/whitened windows: `float32` or `float64`. `float32` halves their memory. |
| `--random-init` / `--no-random-init` | off | Seed each ICA filter randomly instead of from the most active sample. |

### Quality filters (applied after decomposition)

| Flag | Default | Description |
|---|---|---|
| `--sil-thr T` | `0.9` | Silhouette threshold; MUs below it are removed *(app: SIL threshold)*. |
| `--sil-filter` / `--no-sil-filter` | on | Enable/disable the SIL filter *(app: SIL filter — always on in the app; only the CLI can turn it off, which sets the threshold to −∞)*. |
| `--cov-thr T` | `0.5` | Coefficient-of-variation threshold *(app: COV threshold)*. |
| `--covfilter` / `--no-covfilter` | off | Enable/disable the COV filter *(app: COV tool)*. |

### Peel-off

| Flag | Default | Description |
|---|---|---|
| `--peel-off-enabled` / `--no-peel-off-enabled` | off | Subtract other motor units before estimating each filter *(app: Peeloff)*. |
| `--peel-off-win S` | `0.025` | Peel-off window in **seconds** *(app: Window (ms), divided by 1000)*. Must be > 0. |

### Post-processing

| Flag | Default | Description |
|---|---|---|
| `--postprocess MODE` | `windowed` | How MU filters are applied after decomposition *(app: Post-processing)*: `windowed` (filters inside each analysis window only; pulse trains are zero outside the ROI), `full-trace` (filters applied batch by batch across the whole recording, without adaptation), or `adaptive` (online adaptive post-processing — **experimental**). |

### Adaptive engine (experimental; only meaningful with `--postprocess adaptive`)

| Flag | Default | Description |
|---|---|---|
| `--adapt-batch-ms N` | `100` | Adaptive batch duration in milliseconds. Must be ≤ 200 (`edges_sec` × 1000). |
| `--adapt-wh` / `--no-adapt-wh` | on | Adapt the whitening matrix online. |
| `--adapt-sv` / `--no-adapt-sv` | on | Adapt the separation vectors online. |
| `--adapt-sd` / `--no-adapt-sd` | on | Adapt the spike-detection centroids online. |
| `--adapt-wh-learning-rate R` | `0.007` | Learning rate for the whitening-matrix update. Must be > 0. |
| `--adapt-sv-learning-rate R` | `0.003` | Learning rate for the separation-vector update. Must be > 0. |
| `--adapt-cov-alpha A` | `0.1` | EMA weight for the online covariance estimate. Must be in (0, 1]. |
| `--adapt-spike-prev-weight N` | `5` | Inertia weight for the centroid EMA update (higher = slower adaptation). Must be ≥ 0. |

### Automatic QC (in-run)

| Flag | Default | Description |
|---|---|---|
| `--auto-mask-artifacts` / `--no-auto-mask-artifacts` | off | Run the QC pipeline during the run: detect bad channels and artifact-contaminated regions on the filtered EMG and mask them out of the decomposition. The app's **Automatic QC** button does this on demand on the QC page; the app never sets this flag, so this is the way to get the same behaviour in a batch run. |

### BIDS export

| Flag | Default | Description |
|---|---|---|
| `--bids-root PATH` | — | Export the raw, unfiltered EMG into a BIDS dataset rooted at PATH (the folder that directly contains `sub-<subject>/`). |
| `--subject L` | `01` | BIDS subject label. |
| `--task L` | `task` | BIDS task label. |
| `--session L` | — | BIDS session label. |
| `--run L` | — | BIDS run label. |
| `--bids-metadata JSON` | — | Extra JSON object merged into the `*_emg.json` sidecar. Requires `--bids-root`. |
| `--bids-metadata-file PATH` | — | Path to a JSON object merged into `*_emg.json`. Requires `--bids-root`; mutually exclusive with `--bids-metadata`. |

### Validation

The run refuses to start (exit 2) when `niter < 1`, `nwindows < 1`, `peel_off_win ≤ 0`,
any adaptive learning rate ≤ 0, `adapt_cov_alpha` outside (0, 1],
`adapt_spike_prev_weight < 0`, or — with `--postprocess adaptive` — `adapt_batch_ms`
above 200 (`edges_sec` × 1000). `--bids-metadata` and `--bids-metadata-file` are
mutually exclusive, and both require `--bids-root`.

A few engine parameters have **no CLI flag** and no app control; only the API sets
them: `random_seed`, `duplicatesbgrids` (default `true` — also deduplicate across
grids), `nbextchan` (default `1000` — extended-channel count), and `edges_sec`
(default `0.2` s — edge trimming).

### Examples

Decompose a recording, whole trace, defaults:

```bash
uv run muedit decompose recording.mat
```

Decompose a 30 s window of a BIDS recording and export into a dataset:

```bash
uv run muedit decompose sub-01_task-ramp_emg.edf \
  --duration 30 --bids-root /data/study1 --subject 01 --task ramp
```

Two explicit windows, stricter SIL, with the COV filter:

```bash
uv run muedit decompose recording.otb+ \
  --rois "0,20000;40000,60000" --sil-thr 0.95 --covfilter --cov-thr 0.4
```

Experimental adaptive post-processing with in-run automatic QC:

```bash
uv run muedit decompose recording.rhd \
  --postprocess adaptive --adapt-batch-ms 100 --auto-mask-artifacts
```

Pick the region of interest interactively (needs `uv sync --extra plot`):

```bash
uv run muedit decompose recording.mat --manual-roi
```

### Where the output goes

With `--bids-root`, the run exports into that dataset as an app run does
([what a run writes](saved-files.md#what-a-run-writes)). Without it, it writes
`<input_stem>_decomp.npz` beside the input file, with the raw EMG embedded
([command line without a dataset](saved-files.md#command-line-without-a-dataset)).

---

## Environment variables

MUedit reads these variables. Set them before launching.

| Variable | Default | Read by | Effect |
|---|---|---|---|
| `MUEDIT_DATA_ROOT` | the repo's `data/`, else `~/Documents/MUedit` | backend | Output root; each project is a folder inside it. |
| `MUEDIT_HOST` | `127.0.0.1` | `muedit api` | Bind host. |
| `MUEDIT_PORT` | `8000` | `muedit api` | Bind port. |
| `MUEDIT_BACKEND_PORT` | `8000` | launchers only | Copied into `MUEDIT_PORT` by the launcher scripts — the port override when launching via `MUedit.command` / `MUedit.bat` / `scripts/run_MUedit.*`. |
| `MUEDIT_OPEN_BROWSER` | `1` | launchers only | `0` skips opening the browser. |
| `MUEDIT_NO_UV` | `0` | launchers only | `1` forces plain `python` instead of `uv run`. |
| `MUEDIT_CACHE_DIR` | the OS per-user cache (`MUedit` on macOS/Windows, `muedit` on Linux) | backend | Where session stores and unsaved-edit logs live. |
| `MUEDIT_CACHE_BUDGET_MB` | clamp(10% of RAM, 256 MB, 1 GB) | backend | In-memory budget shared by every API cache. |
| `MUEDIT_DISK_RESERVE_MB` | `1024` | backend | Free disk a session store keeps; arrays that would cut into it stay in RAM. |
| `MUEDIT_LOG_FILE` | set by the server | internal | The log file that spawned decomposition workers append to. |

Related: [saved-files.md](saved-files.md) (output paths and file formats) and
[troubleshooting.md](troubleshooting.md) (logs and common failures).
