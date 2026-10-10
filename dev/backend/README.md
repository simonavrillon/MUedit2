# MUedit Backend Development Docs

Auto-generated documentation of the MUedit2 Python backend: architecture, API surface, decomposition engine, I/O, signal processing, editing operations, and a worktree of what each module exposes.

## Contents

| File | Description |
|---|---|
| [01-architecture.md](01-architecture.md) | Module structure, entry points, boot sequence, package exports, data models |
| [02-api-surface.md](02-api-surface.md) | All API routes, request/response schemas, binary wire formats, caching |
| [03-decomposition-engine.md](03-decomposition-engine.md) | Pipeline stages, FastICA algorithm, postprocessing, adaptive online learning |
| [04-io-signal-qc.md](04-io-signal-qc.md) | File loaders, BIDS export, filtering, grid inference, QC pipeline, artifact masking |
| [05-editing-operations.md](05-editing-operations.md) | Every motor-unit editing operation, parameters, and data flow |
| [06-worktree.md](06-worktree.md) | Worktree: user-exposed elements vs app-internal functions, per module |

## How to Use This Documentation

- **Understanding the backend architecture**: start with `01-architecture.md`
- **Finding an API endpoint**: check `02-api-surface.md`
- **Understanding the decomposition pipeline**: start with `03-decomposition-engine.md`
- **Understanding file loading and QC**: start with `04-io-signal-qc.md`
- **Finding a specific editing operation**: check `05-editing-operations.md`
- **Checking what a module exposes or who calls it**: check `06-worktree.md`

## The Four Workflow Stages

The backend serves four user-facing stages (mirroring the frontend):

1. **File Loading** — Raw EMG import from `.mat`, `.otb+`, `.otb4`, `.bdf`/`.edf`, `.rhd`, `.oebin` formats. Signal preview computation, grid geometry inference, BIDS export.
2. **Quality Check & Experiment Info** — Automatic QC pipeline: bad-channel detection, artifact masking. BIDS metadata enrichment (participant, hardware, electrode placement).
3. **Decomposition** — FastICA-based convolutive source separation with optional adaptive online post-processing. SIL scoring, duplicate removal, preview payload generation.
4. **Editing** — A server-side edit session per tab: filter updates, spike add/delete, artifact marking, discharge-rate pruning, outlier removal, deduplication, flagging, undo, recovery of unsaved edits after a crash, and BIDS save.

## Backend File Map

```
python/src/muedit/
├── __init__.py                              Top-level exports (imported on first use)
├── cli.py                                   CLI entry point (api serve, decompose)
├── paths.py                                 Per-user cache/log folders, checkout, frontend
├── app_log.py                               Server log file, shared with spawned workers
├── models.py                                SignalImport, LoadedDecomposition, DecompositionExport
│
├── api/
│   ├── app_factory.py                       FastAPI app construction, Host and token checks, frontend mount
│   ├── routes/
│   │   ├── __init__.py                       include_routers()
│   │   ├── preview.py                        /preview-by-path, /qc/auto, /health
│   │   ├── series.py                         /series/emg, /series/overview, /series/aux, /series/pulse
│   │   ├── decompose.py                      /decompose_stream, /decompose/cancel
│   │   ├── editing.py                        /edit/session/*, /edit/ops/{op}, /edit/save
│   │   ├── dialog.py                         /dialog/open-file
│   │   └── memory.py                         /session/close, /debug/memory
│   ├── services/
│   │   ├── preview_service.py                Preview building + on-demand auto-QC
│   │   ├── series_service.py                 QC pyramids, viewport envelopes, pulse-train frames
│   │   ├── decompose_service.py              One run at a time, cancel, NDJSON streaming
│   │   ├── decompose_worker.py               Run body, executed in a spawned worker process
│   │   ├── editing_service.py                Edit sessions: open, apply, recover, save + BIDS save
│   │   ├── bids_helpers.py                   BIDS sidecar parsing + entity resolution
│   │   └── edit_helpers.py                   Normalization helpers for edit payloads
│   ├── schemas.py                            Pydantic request models
│   ├── contracts.py                          Response envelope
│   ├── binary.py                             MUB1 frame packer and unpacker
│   ├── cache.py                              The session-scoped caches (upload, preview, run, edit session)
│   ├── memory.py                             MemoryBudget + BudgetedLRU: one byte budget over every cache
│   ├── common.py                             Shared parsing + serialization utilities
│   ├── config.py                             DATA_ROOT, resolve_bids_root(), project_of()
│   └── errors.py                             Exception handlers + error envelope
│
├── decomp/
│   ├── pipeline.py                           run_decomposition() — 5-stage pipeline
│   ├── core.py                               decompose_step() — ICA loop
│   ├── algorithm.py                          FastICA, whitening, dedup, peel-off, silhouette
│   ├── preprocess.py                         load_step, preprocess_step, ROI resolution, BIDS export
│   ├── postprocess.py                        postprocess_step, export_step, NPZ save
│   ├── adaptive_batch.py                     Online adaptive post-processing
│   ├── preview.py                            Preview payload builder
│   ├── decomposition_file.py                 Decomposition file load/save (NPZ schema, MAT)
│   └── types.py                              DecompositionParameters, step output dataclasses
│
├── io/
│   ├── factory.py                            Loader registry + dispatch
│   ├── loaders.py                            Loader re-export facade
│   ├── bids.py                               BIDS EMG export (EDF/BDF + sidecars + derivatives)
│   ├── _bids_reader.py                       BIDS EMG reading (pyedflib + channels.tsv)
│   ├── _intan.py                             Intan RHD loader (3 save layouts)
│   ├── _openephys.py                         Open Ephys binary-format loader
│   ├── mat.py                                MATLAB .mat v5 + v7.3 (HDF5) loader
│   ├── _otb.py                               OT Bioelettronica OTB+ and OTB4 loaders
│   ├── npz.py                                Aligned .npz writer, memory-mapping reader, legacy unpickler
│   └── store.py                              Session stores: full-length arrays as memory-mapped .npy files
│
├── signal/
│   ├── filters.py                            demean, bandpass, notch (whole or in place, by row block)
│   ├── downsample.py                         moving average (the QC overview)
│   ├── decomp_primitives.py                  extend_signal, signed_square, peak picking, k-means split
│   ├── grid.py                               GridSpec catalog, format_hdemg_signal
│   ├── pyramid.py                            Min/max pyramids and viewport envelopes
│   ├── streaming.py                          StreamedExtender: the extended signal read batch by batch
│   ├── channel_qc.py                         Bad-channel detection (6 criteria)
│   ├── artifact_mask.py                      Artifact region detection
│   └── qc_pipeline.py                        Auto QC orchestration (run_auto_qc)
│
├── editing/
│   ├── operations.py                         MU editing operations (filter update, spike/artifact add/delete, rate pruning)
│   ├── session.py                            EditSession: the decomposition being edited, undo, pulse-train copies
│   └── edit_log.py                           Per-session JSONL log of operations, replayed to recover unsaved edits
│
└── adapt_decomp/
    ├── config.py                             Config dataclass for adaptive decomposition
    └── adaptation.py                         AdaptiveDecomp class + run_adaptive_decomposition()
```
