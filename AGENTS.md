# MUedit — agent & contributor guide

MUedit decomposes high-density EMG recordings (surface or intramuscular arrays) into
motor unit pulse trains. It is a **local** browser app: a Python backend (FastAPI)
serves a vanilla-JS frontend, and the computation runs on your machine. Nothing is
uploaded.

This file is the entry point for anyone (or any agent) working in this repository: a
documentation map, the repository layout, the commands to run, test, and lint, and the rules
every change follows. End-user docs live in `docs/`; developer docs in `dev/`. Each code area
has its own `AGENTS.md` with its conventions; read it before editing there.

## Documentation map

| Doc | Audience | What it's for |
|---|---|---|
| [README.md](README.md) | everyone | what MUedit is, install, launch, 5-minute tour |
| [docs/user-guide.md](docs/user-guide.md) | app users | the app stage by stage: controls, settings, shortcuts |
| [docs/saved-files.md](docs/saved-files.md) | analysing outputs | every file MUedit writes; NPZ schema; edit log |
| [docs/cli.md](docs/cli.md) | scripters | `muedit api` / `muedit decompose` flags; environment variables |
| [docs/troubleshooting.md](docs/troubleshooting.md) | anyone stuck | symptom/fix; log & cache locations |
| [dev/architecture.md](dev/architecture.md) | contributors | how the pieces fit, and why: stages, server-held data, sessions, layers |
| [dev/contracts.md](dev/contracts.md) | contributors | what both sides of a boundary rely on: HTTP, MUB1 frames, NDJSON, edit ops, files |
| [dev/recipes.md](dev/recipes.md) | contributors | checklists for common changes: a route, an edit op, a parameter, a control, an NPZ key |
| [dev/extending-loaders.md](dev/extending-loaders.md) | contributors | add a raw-signal loader or an electrode grid |
| [python/src/muedit/AGENTS.md](python/src/muedit/AGENTS.md) | contributors | backend conventions |
| [frontend/AGENTS.md](frontend/AGENTS.md) | contributors | frontend conventions |
| [tests/AGENTS.md](tests/AGENTS.md) | contributors | pytest conventions and helpers |

## Repository layout

```text
python/src/muedit/     backend
  api/                 HTTP routes, schemas, services, app factory
  decomp/              decomposition pipeline (load → filter → decompose → post-process → save)
  io/                  loaders (.mat/.otb+/.otb4/.bdf/.edf/.rhd/.oebin), BIDS export, .npz I/O
  signal/              QC pipeline, channel QC, segmentation, filters, grid catalogue, pyramids
  editing/             edit session, operations, edit log
frontend/              vanilla JS app (no framework)
  src/app/stages/      import, qc, run, edit, layout stages
  src/view/            renderers (controls, plots, edit-canvas, run-live)
  src/api/             client, routes, payloads, NDJSON, binary frames
  index.html           the page
tests/                 pytest suite (backend)
frontend/tests/        node --test suite
scripts/               run_MUedit.sh / run_MUedit.ps1 (the launchers)
data/                  output root (BIDS datasets land here); recordings for the full suite
notebooks/             bids_dataset_metadata.ipynb (dataset-level BIDS metadata)
```

## Environment & tooling

Setup is in the [README](README.md). Everything runs through [uv](https://docs.astral.sh/uv/)
(`uv run …`; nothing to activate):

```bash
uv sync --extra dev              # locked environment + pytest, ruff, mypy, pre-commit
uv run pre-commit install        # commit hooks
```

`uv.lock` is committed and CI runs with `--locked`: after editing dependencies in
`pyproject.toml`, run `uv lock` and commit both.

## Run

```bash
./MUedit.command                 # macOS (double-click), or scripts/run_MUedit.sh
MUedit.bat                       # Windows (double-click), or scripts/run_MUedit.ps1
uv run muedit api                # backend only — http://127.0.0.1:8000
uv run muedit decompose FILE     # decomposition without the UI (see docs/cli.md)
```

The launchers run `uv run muedit api` with `MUEDIT_OPEN_BROWSER=1`, so the server opens the
browser at `http://127.0.0.1:8000/` once it is listening. The app runs in the launcher's
terminal — quit with `Ctrl+C`. See [docs/cli.md](docs/cli.md) for every
environment variable.

## Test

```bash
uv run pytest                     # full suite (needs recordings under data/)
uv run pytest -m "not data"       # the tier CI runs, no recordings required
```

The `data` pytest marker marks tests that need real recordings under `data/`. The
BIDS-export test (`tests/test_decomp_bids_export.py`) also runs the official validator when
`deno` is installed; without it, that check is skipped.

## Lint & typecheck

```bash
uv run ruff check . && uv run ruff format .
uv run mypy
```

Frontend (Node 20 or later):

```bash
cd frontend
npm ci
npm run check                     # eslint, prettier --check, tsc, node --test
```

## Rules for every change

- **Simplest change that works.** Fix in place; no new abstractions, options or guard tests
  beyond what the change needs.
- **Same concept, same name** across modules and across the backend/frontend boundary
  (`fsamp`, `grid_names`, `muscle_names`, `mu_grid_index`). Grep before naming.
- **Python docstrings are one line; each JS module opens with a comment stating its role.**
  Beyond that, comments say only what the code can't (a spec rule, a unit fact, an ordering
  constraint, why a constant has its value).
- **Change both sides of a contract together.** Routes, MUB1 frames, NDJSON events and edit ops
  are listed in [dev/contracts.md](dev/contracts.md). Edit op names and history entry types are
  persisted in users' files: add them, don't rename them.
- **Never touch** `research/` (private, gitignored) or recordings under `data/`; don't hand-edit
  `uv.lock` (run `uv lock`) or `frontend/package-lock.json`.
- **No new dependencies without asking**, backend or frontend (which has no runtime
  dependencies at all). Use NumPy, SciPy and the standard library, or write the few lines.
- **User-facing text is plain sentences** that say what happened and what to do next ("Edit
  session expired; open the file again"). Error messages reach users verbatim: no internals,
  class names or tracebacks.

## Commits

- Subject: an imperative sentence, sentence case, no prefix (`feat:`), no full stop. Join two
  changes with ", and": *Pick projects from a dropdown, and choose segment types in bulk in QC*.
- Body: prose saying what changed and why, wrapped at ~72 columns; bullets only for a long list.
- One logical change per commit, with its docs and tests.

## Glossary

Use these terms in code, UI text and docs alike.

| Term | Meaning | In code |
|---|---|---|
| MU, motor unit | one decomposed source | `mu`, `n_mu` |
| discharge times | the samples an MU fires at (say "discharges" in docs and UI) | `spike_times` (one array per MU), `spikes` (one MU's array) |
| recording length | samples in the whole recording, or in the array at hand | `total_samples`, `n_samples` |
| pulse train (IPT) | an MU's source signal, from which discharges are picked | `pulse_trains` |
| grid | one electrode array; a recording can hold several | `grid_names`, `mu_grid_index` |
| ROI, analysis window | a stretch of the recording to decompose | `rois` |
| artifact window | a time range excluded on the QC page | `artifact_regions`, `artifact_intervals` |
| artifact marker | a peak marked as an artifact on one MU in Edit | `artifacts`, `artifact_times` |
| project | a BIDS dataset in the output folder | `project`, `bids_root` |
| SIL | silhouette score, the MU quality measure | `sil`, `sil_thr` |

## Before you finish

1. `uv run ruff check . && uv run ruff format .` and `uv run mypy`
2. `uv run pytest -m "not data"` (or the files for the area you touched)
3. `cd frontend && npm run check`, if you touched `frontend/`
4. Docs: update the one home of anything user-visible you changed (below), in the same commit.
   Update `dev/` only when a contract, a layer rule or a recipe changed; never copy code into it.

## Documentation conventions

- **One home per fact.** UI labels, shortcuts, and defaults → `docs/user-guide.md`;
  on-disk formats → `docs/saved-files.md`; CLI and environment variables → `docs/cli.md`;
  architecture, contracts and change recipes → `dev/`; loader/grid extension →
  `dev/extending-loaders.md`; code conventions → the area's `AGENTS.md`. Cross-link; never
  duplicate.
- **`dev/` holds what the code can't say**: decisions, cross-file contracts, multi-file
  checklists. Signatures, field lists and defaults stay in the code, where they can't go stale.
- **bold** = an exact UI label (it matches the app and `frontend/index.html`).
  `` `backticks` `` = a file path, NPZ key, CLI flag, or environment variable.
- Tables are reference; prose is orientation. Tips and warnings are blockquotes.
