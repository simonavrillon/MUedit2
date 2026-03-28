"""Top-level decomposition pipeline entrypoint and helper re-exports."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import numpy as np

from muedit.decomp.core import decompose_step
from muedit.decomp.postprocess import export_step, postprocess_step
from muedit.decomp.preprocess import load_step, preprocess_step
from muedit.decomp.types import DecompositionParameters


def run_decomposition(
    filepath: str,
    duration: float | None = None,
    manual_roi: bool = False,
    params: DecompositionParameters | None = None,
    save_npz: bool = True,
    progress_cb: Callable[[str, dict[str, Any]], None] | None = None,
    roi: tuple[int, int] | None = None,
    rois: list[tuple[int, int]] | None = None,
    discard_overrides: list[list[int]] | None = None,
    bids_root: str | None = None,
    bids_entities: dict[str, Any] | None = None,
    bids_metadata: dict[str, Any] | None = None,
    file_label: str | None = None,
    include_full_preview: bool = False,
    preloaded_signal: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], str]:
    """Execute the full load → preprocess → decompose → postprocess pipeline."""
    params = params or DecompositionParameters()
    rng = np.random.default_rng(params.random_seed)

    loaded = load_step(filepath, file_label, preloaded_signal, progress_cb)
    preprocessed = preprocess_step(
        loaded=loaded,
        duration=duration,
        manual_roi=manual_roi,
        roi=roi,
        rois=rois,
        params=params,
        discard_overrides=discard_overrides,
        bids_root=bids_root,
        bids_entities=bids_entities,
        bids_metadata=bids_metadata,
    )
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
        bids_root=bids_root,
        bids_entities=bids_entities,
        progress_cb=progress_cb,
    )
    return export_step(
        loaded=loaded,
        prep=preprocessed,
        post=postprocessed,
        params=params,
        include_full_preview=include_full_preview,
        save_npz=save_npz,
        save_emg_data=not bool(bids_root),
        progress_cb=progress_cb,
    )
