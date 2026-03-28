"""MUedit — FastICA-based Motor Unit decomposition of High-Density EMG signals.

This package implements the signal-processing pipeline described in:

  Avrillon et al. (2024). "MUedit: An open-source software to decompose and
  analyse the discharge activity of individual motor units from high-density
  electromyographic signals." *Journal of Electromyography and Kinesiology.*

Sub-modules
-----------
models
    Dataclasses for signal import, decomposition state, and export payloads.
io.factory
    Registry-backed dispatch for loading EMG files (.mat, .otb+, .otb4).
io.loaders
    Format-specific parsers for each supported file type.
signal.filters
    Bandpass and notch filtering utilities.
decomp.algorithm
    Core fastICA routines: extension, PCA, whitening, fixed-point algorithm,
    spike detection, SIL metric, and duplicate removal.
editing
    Interactive spike-level editing operations (add, delete, update filter).
export.bids
    Write decomposition results in the BIDS standard (EDF/BDF + JSON + TSV).
export.io
    Load EMG grids from an existing BIDS dataset.
utils
    Grid-layout helpers and signal pre-processing utilities.
decomp.adaptive_batch
    PyTorch-based online adaptation of separation filters across batches.
"""
