# MUedit (v2.0.0)

Decomposes high-density EMG signals into motor unit pulse trains.

## Status

This software is currently in **beta** and is intended for testing purposes, not for research projects.

## Features

- High-density EMG decomposition into motor unit pulse trains
- Adaptive decomposition workflows (via `adapt_decomp`)
- Web-based interface with FastAPI backend and JavaScript frontend
- Cross-platform launchers (macOS/Linux and Windows)

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

- Workflow guide: [docs/workflows.md](docs/workflows.md)
- Saved files reference: [docs/saved-files.md](docs/saved-files.md)

## Acknowledgment

This project includes code from `adapt_decomp` (see `python/src/adapt_decomp`) for adaptive decomposition workflows.
Original author: Irene Mendez Guerra
Original repository: https://github.com/imendezguerra/adapt_decomp
