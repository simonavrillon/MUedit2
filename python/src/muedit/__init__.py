"""MUedit — FastICA-based Motor Unit decomposition of High-Density EMG signals.

This package implements the signal-processing pipeline described in:

  Avrillon et al. (2024). "MUedit: An open-source software to decompose and
  analyse the discharge activity of individual motor units from high-density
  electromyographic signals." *Journal of Electromyography and Kinesiology.*

Sub-modules
-----------
Core
~~~~
models
    Dataclasses for signal import, decomposition state, and export payloads.
editing
    Spike-level editing operations: add, delete, update filter, peel-off.
cli
    Command-line interface (``serve`` and ``decompose`` sub-commands).

I/O
~~~
io.factory
    Registry-backed loader dispatch for all supported file formats.
io.loaders
    Format-specific parsers (.mat, .otb+, .otb4, .bdf/.edf).
io.bids
    Write decomposition results in the BIDS standard (EDF/BDF + JSON + TSV).

Signal
~~~~~~
signal.filters
    Bandpass and notch filtering utilities.
signal.grid
    Grid layout inference and basic signal helpers.

Decomposition
~~~~~~~~~~~~~
decomp.types
    Typed dataclasses for pipeline inputs and outputs.
decomp.pipeline
    Top-level ``run_decomposition`` entry point.
decomp.preprocess
    File load, grid formatting, and filtering steps.
decomp.core
    fastICA decompose step orchestration.
decomp.postprocess
    Deduplication, preview building, and BIDS export hooks.
decomp.preview
    Utilities for building frontend preview payloads from decomposition output.
decomp.algorithm
    Core fastICA routines: extension, PCA, whitening, fixed-point iteration,
    spike detection, SIL metric, and duplicate removal.
decomp.io
    Signal serialization and cloning helpers.
decomp.adaptive_batch
    PyTorch-based online adaptation of separation filters across batches.

API
~~~
api.app_factory
    FastAPI application factory.
api.routes
    Router registration (preview, decompose, editing, dialog).
api.schemas
    Pydantic request and response schemas.
api.contracts
    Standard response envelope helpers.
api.cache
    Upload token cache and signal-window storage.
api.common
    Shared request parsing and serialization utilities.
api.errors
    Exception handlers and error payload formatting.
api.services.decompose_service
    Decomposition streaming and result serialization.
api.services.editing_service
    Filter update, ROI editing, and file save logic.
api.services.preview_service
    QC preview building and channel windowing.
api.services.bids_helpers
    BIDS-specific helpers for decomposition loading and channel sidecar parsing.
api.services.edit_helpers
    Editing-workflow data normalization helpers.
"""
