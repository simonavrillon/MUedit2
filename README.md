# MUedit

Decomposes high-density EMG signals into motor unit pulse trains.

## Status

This software is functional and ready for use. The `adapt_decomp` feature still requires fine tuning and remains in **beta**. We continue to actively maintain and update these tools in line with the advancements of our research projects, and we welcome any contributions to make them useful for the wider community.

## Features

- High-density EMG decomposition into motor unit pulse trains
- Adaptive decomposition workflows (via `adapt_decomp`)
- Web-based interface with FastAPI backend and JavaScript frontend
- Cross-platform launchers (macOS/Linux and Windows)
- Interactive editing of decomposed motor units
- BIDS-compliant export of raw EMG and decomposition results

## Supported Input Formats

| Format | Extension | Description |
|---|---|---|
| MATLAB | `.mat` | v5 and v7.3 (HDF5) signal structs |
| OTB+ | `.otb+` | OT Biolab+ archive (tar/zip with XML + `.sig`) |
| OTB4 | `.otb4` | OT Biolab4 proprietary binary format |
| Intan RHD | `.rhd` | Intan RHD recordings — single-file or directory (all three save layouts) |
| BIDS EMG | `.bdf`, `.edf` | BIDS-formatted recordings with a `_channels.tsv` sidecar (legacy `_emg_channels.tsv` also accepted) |
| Decomposition | `.npz` | Saved decomposition output (for editing) |

For BIDS input, point to either the `*_emg.bdf/.edf` file directly. The loader reads all grids and auxiliary channels defined in the accompanying `*_channels.tsv`.

For Intan RHD input, point to the `.rhd` file (traditional single-file layout) or the recording directory (split layouts). The loader reads the header, resolves the save layout, groups amplifier channels into one grid per port, and picks up a `muedit_grids.json` sidecar if present for grid/muscle identification.

## Requirements

- [uv](https://docs.astral.sh/uv/) — installs its own Python, so no system
  Python is needed:
  ```bash
  # macOS / Linux
  curl -LsSf https://astral.sh/uv/install.sh | sh
  # Windows (PowerShell)
  powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
  ```

## Quick Start

1. Create the environment from the lockfile (installs Python 3.12, every
   dependency and MUedit itself, in one step):
```bash
uv sync
```

2. Launch the app from the repository root:

macOS / Linux:
```bash
./scripts/run_MUedit.sh
```

Windows (PowerShell):
```powershell
.\scripts\run_MUedit.ps1
```

The launcher starts:
- Backend API on `http://localhost:8000`
- Frontend on `http://localhost:8080`

The browser opens automatically unless disabled via env var.

Windows first-time setup (if script execution is blocked):
```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

## CLI Entrypoints

After installation, MUedit exposes:

- `muedit-api` — starts the FastAPI backend
- `muedit-decompose` — runs decomposition from the terminal

## Verify Installation

With MUedit running:

1. Open `http://localhost:8080` in a browser.
2. Check backend health:
```bash
curl http://localhost:8000/api/v1/health
```

## Development

Install the dev tooling (pytest, mypy, ruff, pre-commit) alongside the runtime
dependencies, then enable the commit hooks:

```bash
uv sync --extra dev
uv run pre-commit install
```

Common tasks — `uv run` executes inside the project environment, so nothing
needs activating:

```bash
uv run pytest              # full suite (needs recordings under data/)
uv run pytest -m "not data"  # the tier CI runs, no recordings required
uv run mypy
uv run ruff check . && uv run ruff format .
```

Optional extras: `--extra notebook` (JupyterLab for `notebooks/`) and
`--extra research` (Optuna). Combine them, e.g. `uv sync --extra dev --extra notebook`.

`uv.lock` pins the full transitive dependency graph and is committed, so every
machine and CI resolve identical versions. After editing dependencies in
`pyproject.toml`, run `uv lock` and commit the result; CI runs with `--locked`
and fails if the two have drifted. To move a pinned dependency deliberately:

```bash
uv lock --upgrade-package numpy
```

> **Migrating from conda:** `environment.yml` is deprecated and kept for one
> release so in-flight work is not disrupted. It is no longer tested in CI and
> will be removed; `uv sync` reproduces the same package versions it pinned.
> `deno`, used only by the BIDS validation cell in
> `notebooks/bids_dataset_metadata.ipynb`, is not a Python package and is now
> installed separately (that notebook explains how).

## Troubleshooting

- `python: command not found` — run `uv sync` first; the launcher scripts
  pick up the project environment automatically, with no activation step.
- Port already in use — set `MUEDIT_BACKEND_PORT` / `MUEDIT_FRONTEND_PORT` to free ports.
- Browser does not open automatically — open `http://localhost:8080` manually.

## Documentation

- User guide: [docs/user-guide.md](docs/user-guide.md)
- Saved files reference: [docs/saved-files.md](docs/saved-files.md)
- Loader registry guide: [docs/loader-registry.md](docs/loader-registry.md)

## Acknowledgment

This project includes code from `adapt_decomp` (see `python/src/adapt_decomp`) for adaptive decomposition workflows.
Original author: Irene Mendez Guerra
Original repository: https://github.com/imendezguerra/adapt_decomp
