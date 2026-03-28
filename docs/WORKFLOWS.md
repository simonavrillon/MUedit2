# MUedit Workflows

This document lists runnable script/entrypoint surfaces and the supported end-to-end workflows.

## Documentation Map

- Frontend architecture: `docs/FRONTEND_ARCHITECTURE.md`
- Backend architecture: `docs/BACKEND_ARCHITECTURE.md`
- Saved files reference: `docs/SAVED_FILES.md`

## Script And Entrypoint Inventory

### Backend (Python)

Installed console entrypoints (`pyproject.toml`):
- `muedit-api` -> starts FastAPI backend
- `muedit-decompose` -> runs CLI decomposition

Internal module command used by launch scripts (run from `python/` with `PYTHONPATH=python/src`):
- `python -m muedit.cli api`

### Frontend

Frontend runtime entrypoint:
- `frontend/app.js`

Frontend static serving:
- `python -m http.server <port>` (run from `frontend/`)

### Combined Launch Scripts

Repository launchers:
- `scripts/run_MUedit.sh` (macOS/Linux)
- `scripts/run_MUedit.ps1` (Windows PowerShell)

These scripts start backend + frontend and optionally open the browser.

## Workflow 1: Web App (Backend + Frontend)

### Option A (recommended): Combined launcher

macOS / Linux:

```bash
./scripts/run_MUedit.sh
```

Windows PowerShell:

```powershell
.\scripts\run_MUedit.ps1
```

Then open:
- Frontend: `http://localhost:8080`
- Backend health: `http://localhost:8000/api/v1/health`

### Option B: Start services manually

Backend:

```bash
muedit-api
```

Frontend (new terminal):

```bash
cd frontend
python -m http.server 8080
```

### Environment Variables (Web Workflow)

Used by `scripts/run_MUedit.sh` and `scripts/run_MUedit.ps1`:

| Variable | Default | Purpose |
|---|---|---|
| `MUEDIT_HOST` | `0.0.0.0` | Backend bind host |
| `MUEDIT_BACKEND_PORT` | `8000` | Backend API port |
| `MUEDIT_FRONTEND_PORT` | `8080` | Frontend static-server port |
| `MUEDIT_OPEN_BROWSER` | `1` | Auto-open browser if `1` |

Example custom ports:

```bash
MUEDIT_BACKEND_PORT=9000 MUEDIT_FRONTEND_PORT=9001 ./scripts/run_MUedit.sh
```

## Workflow 2: Direct Backend Usage (Without Frontend)

### 1. Activate environment

```bash
conda activate MUedit
```

### 2. Install package (if needed)

```bash
pip install -e .
```

Optional (for local checks/build tooling):

```bash
pip install -e ".[dev]"
```

### 3. Run backend directly

API server only:

```bash
muedit-api
```

CLI decomposition:

```bash
muedit-decompose /path/to/signal.mat
```

Example with options:

```bash
muedit-decompose /path/to/signal.otb4 --duration 30 --roi 1000,90000 --niter 200 --cov-filter
```

### 4. Outputs

The decomposition command writes output artifacts (for example `*_decomp.npz`) according to pipeline export logic.

### 5. CLI options aligned with app settings panel

The CLI mirrors decomposition controls exposed in the app settings panel:

- `--niter`
- `--nwindows`
- `--duplicatesthresh`
- `--sil-thr`
- `--sil-filter` / `--no-sil-filter`
- `--cov-thr`
- `--cov-filter` / `--no-cov-filter`
- `--peel-off` / `--no-peel-off`
- `--peel-off-window-ms`
- `--use-adaptive` / `--no-use-adaptive`

ROI and input options:

- `--duration`
- `--manual-roi`
- `--roi`
- `--rois`

BIDS/session options:

- `--bids-root`
- `--subject`
- `--task`
- `--session`
- `--run`
- `--bids-metadata`
- `--bids-metadata-file`
