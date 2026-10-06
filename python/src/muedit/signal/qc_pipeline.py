"""Automatic QC pipeline: bad channels -> artifacts -> bad channels."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

from muedit.io.store import ArrayStore
from muedit.models import BoolArray, FloatArray, IntArray
from muedit.signal.artifact_mask import (
    ArtifactMaskConfig,
    detect_artifact_masks,
)
from muedit.signal.channel_qc import (
    ChannelQCConfig,
    detect_bad_channels_per_grid,
)

logger = logging.getLogger(__name__)


@dataclass
class QCPipelineResult:
    """Output of :func:`run_auto_qc`."""

    artifact_mask: BoolArray
    bad_channel_masks: list[BoolArray]


def run_auto_qc(
    data: FloatArray,
    fsamp: float,
    grid_channel_counts: list[int],
    grid_coordinates: list[FloatArray] | None = None,
    artifact_config: ArtifactMaskConfig | None = None,
    channel_qc_config: ChannelQCConfig | None = None,
    store: ArrayStore | None = None,
) -> QCPipelineResult:
    """Run the automatic QC pipeline; ``store`` takes the artifact detector's working array."""
    prelim_bad_masks = detect_bad_channels_per_grid(
        data,
        fsamp,
        grid_channel_counts,
        grid_coordinates,
        channel_qc_config,
        structural_only=True,
    )
    prelim_bad = sum(int(m.sum()) for m in prelim_bad_masks)
    total_ch = sum(grid_channel_counts)
    logger.info(
        "QC stage 0 (preliminary bad channels): %d / %d channels (%.2f%%)",
        prelim_bad,
        total_ch,
        100.0 * prelim_bad / max(total_ch, 1),
    )

    kept_rows = _kept_rows(grid_channel_counts, prelim_bad_masks)
    _, artifact_mask = detect_artifact_masks(
        data,
        fsamp,
        [rows.size for rows in kept_rows],
        artifact_config,
        grid_rows=kept_rows,
        store=store,
    )
    logger.info(
        "QC stage 1 (artifacts): %d / %d samples (%.2f%%)",
        int(artifact_mask.sum()),
        data.shape[1],
        100.0 * artifact_mask.sum() / data.shape[1],
    )

    final_bad_masks = detect_bad_channels_per_grid(
        data,
        fsamp,
        grid_channel_counts,
        grid_coordinates,
        channel_qc_config,
        keep=~artifact_mask,
    )

    bad_channel_masks = [
        np.maximum(p.astype(int), f.astype(int)).astype(bool)
        for p, f in zip(prelim_bad_masks, final_bad_masks, strict=True)
    ]
    total_bad = sum(int(m.sum()) for m in bad_channel_masks)
    logger.info(
        "QC stage 2 (bad channels): %d / %d channels (%.2f%%)",
        total_bad,
        total_ch,
        100.0 * total_bad / max(total_ch, 1),
    )

    return QCPipelineResult(
        artifact_mask=artifact_mask,
        bad_channel_masks=bad_channel_masks,
    )


def _kept_rows(
    grid_channel_counts: list[int], bad_channel_masks: list[BoolArray]
) -> list[IntArray]:
    """Rows of each grid's kept channels in the full signal (its first channel if none is kept)."""
    rows: list[IntArray] = []
    ch_idx = 0
    for n_ch, bad in zip(grid_channel_counts, bad_channel_masks, strict=True):
        kept = np.where(~bad)[0]
        if kept.size == 0:
            kept = np.array([0])
        rows.append(ch_idx + kept)
        ch_idx += n_ch
    return rows
