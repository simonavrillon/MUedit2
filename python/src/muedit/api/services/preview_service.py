"""Application services for preview and QC windows."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np

from muedit.api import config
from muedit.api.cache import (
    hold_upload,
    release_upload,
    store_upload,
)
from muedit.api.common import (
    dataset_root_of,
    make_json_safe,
    missing_upload,
    parse_entity_label,
    require_existing_path,
)
from muedit.api.memory import DEFAULT_SESSION
from muedit.api.schemas import QcAutoPayload, SegmentPayload
from muedit.api.services.series_service import (
    bandpassed_row,
    build_signal_views,
    grid_emg_type,
)
from muedit.errors import MueditError
from muedit.io.aux_channels import find_target_channel
from muedit.io.bids import read_sidecar_meta
from muedit.io.factory import get_loader, load_signal
from muedit.models import FloatArray, SignalImport
from muedit.signal.artifact_mask import mask_to_intervals
from muedit.signal.filters import for_each_row
from muedit.signal.grid import format_hdemg_signal, grid_rows
from muedit.signal.qc_pipeline import run_auto_qc
from muedit.signal.segmentation import segment_target
from muedit.storage import ArrayStore, SessionStore

logger = logging.getLogger(__name__)


def _build_preview_core(file_path: str, session: str = DEFAULT_SESSION) -> dict[str, Any]:
    """Load a signal into a session store and build the QC series and the preview."""
    release_upload(session)
    store = SessionStore.create("upload")
    try:
        signal = load_signal(file_path, store=store)
        coordinates, _, _, emg_type = format_hdemg_signal(signal.grid_names)
        views, channel_means = build_signal_views(
            signal, store, [c.shape[0] for c in coordinates], emg_type
        )
    except BaseException:
        store.close()
        raise
    # Kept only once built: another file opened in this tab meanwhile drops kept uploads, not this.
    upload_token = store_upload(signal, file_path, store, views, session)

    return make_json_safe(
        {
            "upload_token": upload_token,
            "grid_names": signal.grid_names,
            "total_samples": int(signal.data.shape[1]),
            "fsamp": signal.fsamp,
            "channel_means": [means.tolist() for means in channel_means],
            "coordinates": [coords.tolist() for coords in coordinates],
            "metadata": signal.metadata,
            "muscle_names": signal.muscle_names,
            "aux_names": signal.aux_names,
            "segments": signal.metadata.get("segments") or [],
            "target_index": find_target_channel(signal.aux_names, signal.metadata.get("aux_kinds")),
        }
    )


def build_preview_from_path(file_path: str, session: str = DEFAULT_SESSION) -> dict[str, Any]:
    """Build preview payload from a file path already available on disk."""
    require_existing_path(file_path)
    get_loader(file_path)  # an unsupported file leaves the tab's upload in place
    result = _build_preview_core(file_path, session)

    # Best-effort: enrich with participant and hardware info from BIDS sidecars.
    try:
        bids_root = dataset_root_of(file_path)
        if bids_root is not None:
            result["project"] = config.project_of(bids_root)
            entity_label = parse_entity_label(Path(file_path).name)
            result.update(read_sidecar_meta(bids_root, entity_label))
    except Exception:  # noqa: BLE001  (logged; sidecar errors never block the preview)
        logger.warning("Cannot read the BIDS sidecars of %s", file_path, exc_info=True)

    return result


def _bandpassed_grids(
    signal: SignalImport, grid_channel_counts: list[int], emg_types: list[int], store: ArrayStore
) -> FloatArray:
    """The grid channels bandpassed into one float32 array of ``store``, a few rows at once."""
    # No notch, unlike the in-run auto-QC (preprocess_step): results can differ on powerline noise.
    n_rows, n_samples = sum(grid_channel_counts), signal.data.shape[1]
    out = store.allocate("qc-auto", (n_rows, n_samples), np.float32)
    for grid, rows in enumerate(grid_rows(grid_channel_counts, n_rows)):
        emg_type = grid_emg_type(emg_types, grid)

        def filter_row(row: int, emg_type: int = emg_type) -> None:
            out[row] = bandpassed_row(signal, row, emg_type)

        for_each_row(filter_row, range(rows.start, rows.stop), n_samples)
    return store.seal(out)


def run_auto_qc_on_token(payload: QcAutoPayload) -> dict[str, Any]:
    """Run the automatic QC pipeline over the upload's grid channels, bandpassed for the run."""
    held = hold_upload(payload.upload_token)
    if held is None:
        raise missing_upload()
    try:
        return _auto_qc(held.signal, held.store)
    finally:
        held.release()


def _auto_qc(signal: SignalImport, store: ArrayStore) -> dict[str, Any]:
    coordinates, _, _, emg_type = format_hdemg_signal(signal.grid_names)
    grid_channel_counts = [int(c.shape[0]) for c in coordinates]
    total_declared = sum(grid_channel_counts)
    n_rows = signal.data.shape[0]
    if total_declared > n_rows:
        raise MueditError(
            f"Grid catalogue declares {total_declared} channels but the cached signal has {n_rows}",
            "upload_token",
        )

    data = _bandpassed_grids(signal, grid_channel_counts, emg_type, store)
    try:
        result = run_auto_qc(
            data,
            signal.fsamp,
            grid_channel_counts,
            grid_coordinates=coordinates,
            store=store,
        )
    finally:
        store.discard(data)
    return {
        "bad_channels_per_grid": [m.astype(int).tolist() for m in result.bad_channel_masks],
        "artifact_regions": mask_to_intervals(result.artifact_mask).tolist(),
    }


def segment_target_on_token(payload: SegmentPayload) -> dict[str, Any]:
    """Split the upload's target channel into ramps, plateaus, sinusoids and irregular blocks."""
    held = hold_upload(payload.upload_token)
    if held is None:
        raise missing_upload()
    try:
        signal = held.signal
        names = signal.aux_names
        n_aux = signal.aux_data.shape[0]
        if n_aux == 0:
            raise MueditError("This recording has no auxiliary channels", "target_index")
        target_index = (
            payload.target_index
            if payload.target_index is not None
            else find_target_channel(names, signal.metadata.get("aux_kinds"))
        )
        if target_index is None:
            raise MueditError(
                "No auxiliary channel is named as a target; show it alone in the auxiliary plot",
                "target_index",
            )
        if not 0 <= target_index < n_aux:
            raise MueditError(f"Auxiliary channel {target_index} does not exist", "target_index")
        segments = segment_target(np.asarray(signal.aux_data[target_index]), signal.fsamp)
        if not segments:
            raise MueditError("The target holds no contraction to segment", "target_index")
        return {
            "target_index": target_index,
            "target_name": (
                names[target_index] if target_index < len(names) else f"Aux {target_index + 1}"
            ),
            "segments": [s.to_dict() for s in segments],
        }
    finally:
        held.release()
