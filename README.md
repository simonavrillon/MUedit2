# MUedit (2.0)

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
| BIDS EMG | `.bdf`, `.edf` | BIDS-formatted recordings with `_emg_channels.tsv` sidecar |
| Decomposition | `.npz` | Saved decomposition output (for editing) |

For BIDS input, point to either the `*_emg.bdf/.edf` file directly or the `emg/` directory. The loader reads all grids and auxiliary channels defined in the accompanying `*_emg_channels.tsv`.

## Requirements

- Python 3.11+
- Conda (Anaconda or Miniconda)

## Quick Start

1. Create and activate the conda environment:
```bash
conda env create -f environment.yml
conda activate MUedit
```

2. Install MUedit in editable mode:
```bash
pip install -e .
```

3. Launch the app from the repository root:

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

## Configuration

| Variable | Default | Description |
|---|---|---|
| `MUEDIT_HOST` | `0.0.0.0` | API bind host |
| `MUEDIT_BACKEND_PORT` | `8000` | API port |
| `MUEDIT_FRONTEND_PORT` | `8080` | Frontend port |
| `MUEDIT_OPEN_BROWSER` | `1` | Set to `0` to skip auto-opening browser |

Example (macOS/Linux):
```bash
MUEDIT_BACKEND_PORT=9000 MUEDIT_FRONTEND_PORT=9001 MUEDIT_OPEN_BROWSER=0 ./scripts/run_MUedit.sh
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

## Troubleshooting

- `python: command not found` — activate the conda env before launching.
- Port already in use — set `MUEDIT_BACKEND_PORT` / `MUEDIT_FRONTEND_PORT` to free ports.
- Browser does not open automatically — open `http://localhost:<MUEDIT_FRONTEND_PORT>` manually.

## Documentation

- User guide: [docs/user-guide.md](docs/user-guide.md)
- Saved files reference: [docs/saved-files.md](docs/saved-files.md)
- Loader registry guide: [docs/loader-registry.md](docs/loader-registry.md)

## Acknowledgment

This project includes code from `adapt_decomp` (see `python/src/adapt_decomp`) for adaptive decomposition workflows.
Original author: Irene Mendez Guerra
Original repository: https://github.com/imendezguerra/adapt_decomp
