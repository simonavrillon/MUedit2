"""Automatic QC pipeline: bad channels -> artifacts -> bad channels."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from dataclasses import replace as dc_replace

import numpy as np

from muedit.models import BoolArray, FloatArray
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
) -> QCPipelineResult:
    """Run the automatic QC pipeline."""
    prelim_config = dc_replace(
        channel_qc_config or ChannelQCConfig(),
        intermittent_amp_ratio=float("inf"),
        contact_loss_min_run_ms=10**9,
        noisy_corr_threshold=-1.0,
        snr_thr=-float("inf"),
    )
    prelim_bad_masks = detect_bad_channels_per_grid(
        data,
        fsamp,
        grid_channel_counts,
        grid_coordinates,
        prelim_config,
    )
    prelim_bad = sum(int(m.sum()) for m in prelim_bad_masks)
    total_ch = sum(grid_channel_counts)
    logger.info(
        "QC stage 0 (preliminary bad channels): %d / %d channels (%.2f%%)",
        prelim_bad,
        total_ch,
        100.0 * prelim_bad / max(total_ch, 1),
    )

    kept_data, kept_counts, _ = _select_kept_channels(
        data,
        grid_channel_counts,
        prelim_bad_masks,
        grid_coordinates,
    )

    _, artifact_mask = detect_artifact_masks(
        kept_data,
        fsamp,
        kept_counts,
        artifact_config,
    )
    logger.info(
        "QC stage 1 (artifacts): %d / %d samples (%.2f%%)",
        int(artifact_mask.sum()),
        data.shape[1],
        100.0 * artifact_mask.sum() / data.shape[1],
    )

    qc_data = _exclude_samples(data, artifact_mask)
    final_bad_masks = detect_bad_channels_per_grid(
        qc_data,
        fsamp,
        grid_channel_counts,
        grid_coordinates,
        channel_qc_config,
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


def _select_kept_channels(
    data: FloatArray,
    grid_channel_counts: list[int],
    bad_channel_masks: list[BoolArray],
    grid_coordinates: list[FloatArray] | None,
) -> tuple[FloatArray, list[int], list[FloatArray] | None]:
    """Build a data array containing only kept channels per grid."""
    kept_slices: list[FloatArray] = []
    kept_counts: list[int] = []
    kept_coords: list[FloatArray] | None = [] if grid_coordinates is not None else None

    ch_idx = 0
    for grid_idx, (n_ch, bad) in enumerate(
        zip(grid_channel_counts, bad_channel_masks, strict=True)
    ):
        kept = np.where(~bad)[0]
        if kept.size == 0:
            kept = np.array([0])
        kept_slices.append(data[ch_idx + kept, :])
        kept_counts.append(kept.size)
        if grid_coordinates is not None and kept_coords is not None:
            kept_coords.append(grid_coordinates[grid_idx][kept])
        ch_idx += n_ch

    kept_data = np.vstack(kept_slices) if kept_slices else np.zeros((0, data.shape[1]))
    return kept_data, kept_counts, kept_coords


def _exclude_samples(
    data: FloatArray,
    exclude_mask: BoolArray,
) -> FloatArray:
    """Drop excluded columns so channel QC only sees valid samples."""
    if not exclude_mask.any() or exclude_mask.all():
        return data
    return data[:, ~exclude_mask]
