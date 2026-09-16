"""Application services for preview and QC windows."""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Any

import numpy as np
from fastapi import HTTPException
from fastapi.responses import Response

from muedit.api.cache import (
    _get_qc_signal,
    _store_qc_signal,
    _store_upload_signal,
)
from muedit.api.common import (
    make_json_safe,
    parse_entity_label,
    require_existing_path,
)
from muedit.api.schemas import QcAutoPayload, QcWindowPayload
from muedit.api.services.bids_helpers import (
    _infer_bids_root_from_decomp_path,
    read_bids_sidecar_meta,
)
from muedit.decomp.preview import downsample_vector
from muedit.io.factory import clone_signal, get_loader, load_signal
from muedit.signal.downsample import (
    PREVIEW_MOVING_AVG_MS,
    moving_average_ms,
    raw_series_at_fs,
)
from muedit.signal.filters import bandpass_signals
from muedit.signal.grid import format_hdemg_signal
from muedit.signal.qc_pipeline import run_auto_qc


def _encode_qc_raw_f32(
    *,
    grid_index: int,
    channel_index: int,
    start: int,
    end: int,
    total_samples: int,
    fsamp: float,
    channels: list[dict[str, Any]],
) -> bytes:
    """Encode QC raw traces to compact float32 binary payload (MQCR v1)."""
    parts: list[bytes] = []
    parts.append(b"MQCR")
    parts.append(struct.pack("<I", 1))
    parts.append(struct.pack("<i", int(grid_index)))
    parts.append(struct.pack("<i", int(channel_index)))
    parts.append(struct.pack("<i", int(start)))
    parts.append(struct.pack("<i", int(end)))
    parts.append(struct.pack("<i", int(total_samples)))
    parts.append(struct.pack("<f", float(fsamp)))
    parts.append(struct.pack("<I", len(channels)))
    for entry in channels:
        ch_idx = int(entry.get("channel_index", 0))
        series = np.asarray(entry.get("series", []), dtype=np.float32)
        parts.append(struct.pack("<i", ch_idx))
        parts.append(struct.pack("<I", int(series.size)))
        parts.append(series.astype("<f4", copy=False).tobytes(order="C"))
    return b"".join(parts)


def _build_preview_core(filepath: str) -> dict[str, Any]:
    """Load signal, preprocess EMG grids, cache QC data, and build UI preview payload."""
    loaded_signal = load_signal(filepath)
    upload_token = _store_upload_signal(loaded_signal, source_path=filepath)
    signal = clone_signal(loaded_signal)
    data = signal["data"]
    fsamp = float(signal["fsamp"])

    grid_names = signal.get("gridname", ["Default"])
    coordinates, _, discard_channels, emg_type = format_hdemg_signal(grid_names)

    ch_idx = 0
    for i in range(len(grid_names)):
        n_channels_grid = coordinates[i].shape[0]
        grid_data = data[ch_idx : ch_idx + n_channels_grid, :]
        current_type = emg_type[i] if i < len(emg_type) else 1
        data[ch_idx : ch_idx + n_channels_grid, :] = bandpass_signals(
            grid_data, fsamp, emg_type=current_type
        )
        ch_idx += n_channels_grid

    _store_qc_signal(upload_token, data, fsamp, grid_names, discard_channels)

    mean_abs = moving_average_ms(np.mean(np.abs(data), axis=0), fsamp, PREVIEW_MOVING_AVG_MS)
    mean_abs_downsampled = downsample_vector(mean_abs, fsamp)

    grid_means = []
    channel_means = []
    ch_idx = 0
    for i in range(len(grid_names)):
        n_channels_grid = len(discard_channels[i])
        grid_data = data[ch_idx : ch_idx + n_channels_grid, :]
        grid_mean_abs = moving_average_ms(
            np.mean(np.abs(grid_data), axis=0), fsamp, PREVIEW_MOVING_AVG_MS
        )
        grid_means.append(downsample_vector(grid_mean_abs, fsamp))
        channel_means.append(np.mean(np.abs(grid_data), axis=1).tolist())
        ch_idx += n_channels_grid

    return make_json_safe(
        {
            "upload_token": upload_token,
            "mean_abs": mean_abs_downsampled,
            "grid_mean_abs": grid_means,
            "grid_names": grid_names,
            "total_samples": int(data.shape[1]),
            "fsamp": fsamp,
            "channel_means": channel_means,
            "coordinates": [coords.tolist() for coords in coordinates],
            "metadata": signal.get("metadata", {}),
            "muscle": signal.get("muscle", []),
            "auxiliary": (
                [
                    downsample_vector(signal["auxiliary"][i, :], fsamp)
                    for i in range(signal["auxiliary"].shape[0])
                ]
                if signal.get("auxiliary") is not None and signal["auxiliary"].size > 0
                else []
            ),
            "auxiliary_names": signal.get("auxiliaryname", []),
        }
    )


def _decomp_artifact_error(field: str) -> HTTPException:
    return HTTPException(
        status_code=400,
        detail={
            "field": field,
            "reason": "This MAT file is a decomposition artifact; load it in edit mode.",
        },
    )


def build_preview_from_path(filepath: str) -> dict[str, Any]:
    """Build preview payload from a file path already available on disk."""
    require_existing_path(filepath)
    try:
        get_loader(filepath)
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail={"field": "path", "reason": str(exc)}
        ) from exc
    try:
        result = _build_preview_core(filepath)
    except (OSError, ValueError) as exc:
        if "contains decomposition fields" in str(exc):
            raise _decomp_artifact_error("path") from exc
        raise

    # Best-effort: enrich with participant and hardware info from BIDS sidecars.
    try:
        bids_root = _infer_bids_root_from_decomp_path(filepath)
        if bids_root is not None:
            entity_label = parse_entity_label(Path(filepath).name)
            result.update(read_bids_sidecar_meta(bids_root, entity_label))
    except Exception:  # noqa: BLE001
        pass  # best-effort; never block the preview on sidecar errors

    return result


def get_qc_window(payload: QcWindowPayload) -> Response:
    """Return channel-window QC data from cached signal as packed float32 binary."""
    cached = _get_qc_signal(payload.upload_token)
    if cached is None:
        raise HTTPException(
            status_code=400,
            detail={
                "field": "upload_token",
                "reason": "Missing or expired QC cache; request /api/v1/preview-by-path first",
            },
        )

    grid_index = payload.grid_index
    start = payload.start
    end = payload.end
    target_fs = payload.target_fs
    channel_index_raw = payload.channel_index

    data = cached["data"]
    fsamp = float(cached["fsamp"])
    offsets: list[int] = cached["channel_offsets"]
    masks: list[np.ndarray] = cached["discard_channels"]

    if grid_index < 0 or grid_index >= len(offsets):
        raise HTTPException(status_code=400, detail="grid_index out of range")

    n_grid_ch = int(masks[grid_index].size)
    offset = int(offsets[grid_index])
    total_samples = int(data.shape[1])

    s = max(0, min(start, max(0, total_samples - 1)))
    e = max(s + 1, min(end if end > 0 else total_samples, total_samples))

    grid_block = data[offset : offset + n_grid_ch, s:e]

    if channel_index_raw is None:
        channels_payload = [
            {
                "channel_index": ch_idx,
                "series": raw_series_at_fs(grid_block[ch_idx], fsamp, target_fs),
            }
            for ch_idx in range(n_grid_ch)
        ]
        payload_bytes = _encode_qc_raw_f32(
            grid_index=grid_index,
            channel_index=-1,
            start=s,
            end=e,
            total_samples=total_samples,
            fsamp=fsamp,
            channels=channels_payload,
        )
        return Response(
            content=payload_bytes,
            media_type="application/octet-stream",
            headers={"x-muedit-format": "qc-raw-f32-v1"},
        )

    channel_index = channel_index_raw
    if channel_index < 0 or channel_index >= n_grid_ch:
        raise HTTPException(status_code=400, detail="channel_index out of range")
    series = raw_series_at_fs(grid_block[channel_index], fsamp, target_fs)
    payload_bytes = _encode_qc_raw_f32(
        grid_index=grid_index,
        channel_index=channel_index,
        start=s,
        end=e,
        total_samples=total_samples,
        fsamp=fsamp,
        channels=[{"channel_index": channel_index, "series": series}],
    )
    return Response(
        content=payload_bytes,
        media_type="application/octet-stream",
        headers={"x-muedit-format": "qc-raw-f32-v1"},
    )


def _mask_to_regions(mask: np.ndarray) -> list[list[int]]:
    """Convert a boolean sample mask into contiguous ``[start, end)`` ranges."""
    if mask is None or not mask.any():
        return []
    diff = np.diff(mask.astype(np.int8), prepend=0, append=0)
    starts = np.flatnonzero(diff == 1)
    ends = np.flatnonzero(diff == -1)
    return [[int(s), int(e)] for s, e in zip(starts, ends, strict=True)]


def run_auto_qc_on_token(payload: QcAutoPayload) -> dict[str, Any]:
    """Run the automatic QC pipeline over the cached preview signal."""
    cached = _get_qc_signal(payload.upload_token)
    if cached is None:
        raise HTTPException(
            status_code=400,
            detail={
                "field": "upload_token",
                "reason": "Missing or expired QC cache; request /api/v1/preview-by-path first",
            },
        )

    fsamp = float(cached["fsamp"])
    grid_names = list(cached["grid_names"])
    coordinates, _, _, _ = format_hdemg_signal(grid_names)
    grid_channel_counts = [int(c.shape[0]) for c in coordinates]
    total_declared = sum(grid_channel_counts)

    data = np.asarray(cached["data"], dtype=np.float64)
    if total_declared > data.shape[0]:
        raise HTTPException(
            status_code=400,
            detail={
                "field": "upload_token",
                "reason": (
                    f"Grid catalogue declares {total_declared} channels but the cached "
                    f"signal has {data.shape[0]}"
                ),
            },
        )

    result = run_auto_qc(
        data[:total_declared],
        fsamp,
        grid_channel_counts,
        grid_coordinates=coordinates,
    )
    return make_json_safe(
        {
            "bad_channels_per_grid": [
                np.asarray(m, dtype=int).tolist() for m in result.bad_channel_masks
            ],
            "artifact_regions": _mask_to_regions(result.artifact_mask),
            "artifact_samples": int(np.asarray(result.artifact_mask).sum()),
            "total_samples": int(data.shape[1]),
            "fsamp": fsamp,
            "grid_names": grid_names,
        }
    )
