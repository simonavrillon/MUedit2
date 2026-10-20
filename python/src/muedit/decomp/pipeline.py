"""Top-level decomposition pipeline entrypoint."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Any

import numpy as np

from muedit.decomp.core import decompose_step
from muedit.decomp.postprocess import export_step, postprocess_step
from muedit.decomp.preprocess import bids_export_step, load_step, preprocess_step
from muedit.decomp.types import DecompositionParameters, DecompositionResult
from muedit.io.bids import run_decomp_path
from muedit.models import FloatArray, SignalImport, resident_nbytes
from muedit.storage import ArrayStore, RamStore, copy_into


def run_decomposition(
    filepath: str,
    duration: float | None = None,
    manual_roi: bool = False,
    params: DecompositionParameters | None = None,
    save_npz: bool = True,
    progress_cb: Callable[[str, dict[str, Any]], None] | None = None,
    rois: list[tuple[int, int]] | None = None,
    discard_overrides: list[list[int]] | None = None,
    bids_root: str | None = None,
    bids_entities: dict[str, Any] | None = None,
    bids_metadata: dict[str, Any] | None = None,
    file_label: str | None = None,
    include_full_preview: bool = False,
    preloaded_signal: SignalImport | None = None,
    artifact_regions: list[tuple[int, int]] | None = None,
    store: ArrayStore | None = None,
    target_index: int | None = None,
) -> DecompositionResult:
    """Execute the full load → preprocess → (BIDS export) → decompose → postprocess pipeline.

    With ``store`` (the run's T1 folder), the filtered EMG and the pulse trains are
    memory-mapped files there; the filtered file is deleted once the run is exported.
    """
    params = params or DecompositionParameters()
    rng = np.random.default_rng(params.random_seed)

    loaded = load_step(filepath, file_label, preloaded_signal, progress_cb)
    if progress_cb:
        progress_cb("progress", {"message": "Filtering signal", "phase": "preprocess"})
    preprocessed = preprocess_step(
        loaded=loaded,
        duration=duration,
        manual_roi=manual_roi,
        rois=rois,
        params=params,
        discard_overrides=discard_overrides,
        artifact_regions=artifact_regions,
        store=store,
        target_index=target_index,
    )
    if bids_root:
        preprocessed = bids_export_step(
            loaded, preprocessed, bids_root, bids_entities, bids_metadata
        )
    # A file saved outside a BIDS dataset embeds the raw EMG; BIDS has its own copy.
    _, in_dataset = run_decomp_path(loaded.full_path, preprocessed.loader_meta)
    emg_data = (
        _emg_data_for_file(loaded.signal.data, store) if save_npz and not in_dataset else None
    )
    # Only the filtered copy is needed from here on; let the raw samples go.
    loaded = replace(loaded, signal=preprocessed.signal)
    decomposed = decompose_step(
        prep=preprocessed,
        params=params,
        rng=rng,
        progress_cb=progress_cb,
    )
    postprocessed = postprocess_step(
        prep=preprocessed,
        decomposed=decomposed,
        params=params,
        progress_cb=progress_cb,
        store=store,
    )
    result = export_step(
        loaded=loaded,
        prep=preprocessed,
        post=postprocessed,
        params=params,
        include_full_preview=include_full_preview,
        save_npz=save_npz,
        emg_data=emg_data,
        progress_cb=progress_cb,
    )
    if store is not None:
        store.discard(preprocessed.data)
        if emg_data is not None:
            store.discard(emg_data)
    return result


def _emg_data_for_file(data: FloatArray, store: ArrayStore | None) -> FloatArray:
    """``data`` itself when memory-mapped, else a float32 copy (in ``store``) held until the save."""
    if resident_nbytes(data) == 0:
        return data
    return copy_into(store if store is not None else RamStore(), "raw", data)
