"""Application services for preview and QC windows."""

from __future__ import annotations

import struct
from typing import Any

import numpy as np
from fastapi import HTTPException, UploadFile
from fastapi.responses import Response

from muedit.api.cache import (
    PREVIEW_MOVING_AVG_MS,
    _envelope_bins,
    _get_qc_signal,
    _moving_average_ms,
    _raw_series_at_fs,
    _store_qc_signal,
    _store_upload_signal,
)
from muedit.api.common import as_float, as_int, make_json_safe, safe_unlink, save_upload_to_temp
from muedit.decomp.postprocess import downsample_vector
from muedit.decomp.signal_io import clone_signal, load_signal
from muedit.signal.filters import bandpass_signals
from muedit.utils import format_hdemg_signal


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
    upload_token = _store_upload_signal(loaded_signal)
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

    mean_abs = _moving_average_ms(
        np.mean(np.abs(data), axis=0), fsamp, PREVIEW_MOVING_AVG_MS
    )
    mean_abs_downsampled = downsample_vector(mean_abs, fsamp)

    grid_means = []
    channel_means = []
    ch_idx = 0
    for i in range(len(grid_names)):
        n_channels_grid = len(discard_channels[i])
        grid_data = data[ch_idx : ch_idx + n_channels_grid, :]
        grid_mean_abs = _moving_average_ms(
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
                if signal.get("auxiliary") is not None
                and signal["auxiliary"].size > 0
                else []
            ),
            "auxiliary_names": signal.get("auxiliaryname", []),
        }
    )


async def build_preview(file: UploadFile) -> dict[str, Any]:
    """Build preview payload from uploaded file contents."""
    tmp_path = await save_upload_to_temp(file)
    try:
        return _build_preview_core(tmp_path)
    finally:
        safe_unlink(tmp_path)


def build_preview_from_path(filepath: str) -> dict[str, Any]:
    """Build preview payload from a file path already available on disk."""
    return _build_preview_core(filepath)


def get_qc_window(payload: dict[str, Any]) -> Any:
    """Return channel-window QC data from cached signal, JSON or binary."""
    token = payload.get("upload_token")
    cached = _get_qc_signal(token)
    if cached is None:
        raise HTTPException(
            status_code=400,
            detail={
                "field": "upload_token",
                "reason": "Missing or expired QC cache; request /api/v1/preview first",
            },
        )

    grid_index = as_int(payload.get("grid_index"), "grid_index", default=0)
    start = as_int(payload.get("start"), "start", default=0)
    end = as_int(payload.get("end"), "end", default=0)
    target_points = as_int(payload.get("target_points"), "target_points", default=96)
    target_fs = as_float(payload.get("target_fs"), "target_fs", default=1000.0)
    representation = str(payload.get("representation") or "envelope").lower()
    channel_index_raw = payload.get("channel_index")

    data = cached["data"]
    fsamp = float(cached["fsamp"])
    offsets: list[int] = cached["channel_offsets"]
    masks: list[np.ndarray] = cached["discard_channels"]
    grid_names: list[str] = cached["grid_names"]

    if grid_index < 0 or grid_index >= len(offsets):
        raise HTTPException(status_code=400, detail="grid_index out of range")

    n_grid_ch = int(masks[grid_index].size)
    offset = int(offsets[grid_index])
    total_samples = int(data.shape[1])

    s = max(0, min(start, max(0, total_samples - 1)))
    e = max(s + 1, min(end if end > 0 else total_samples, total_samples))

    grid_block = data[offset : offset + n_grid_ch, s:e]

    if channel_index_raw is None:
        channels_payload = []
        for ch_idx in range(n_grid_ch):
            if representation == "raw":
                series = _raw_series_at_fs(grid_block[ch_idx], fsamp, target_fs)
                channels_payload.append({"channel_index": ch_idx, "series": series})
            else:
                mins, maxs = _envelope_bins(grid_block[ch_idx], target_points)
                channels_payload.append({"channel_index": ch_idx, "min": mins, "max": maxs})
        if representation == "raw":
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
        return make_json_safe(
            {
                "grid_index": grid_index,
                "grid_name": grid_names[grid_index] if grid_index < len(grid_names) else "",
                "start": s,
                "end": e,
                "total_samples": total_samples,
                "fsamp": fsamp,
                "representation": representation,
                "channels": channels_payload,
            }
        )

    channel_index = as_int(channel_index_raw, "channel_index", default=0)
    if channel_index < 0 or channel_index >= n_grid_ch:
        raise HTTPException(status_code=400, detail="channel_index out of range")
    if representation == "raw":
        series = _raw_series_at_fs(grid_block[channel_index], fsamp, target_fs)
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
    mins, maxs = _envelope_bins(grid_block[channel_index], target_points)
    return make_json_safe(
        {
            "grid_index": grid_index,
            "grid_name": grid_names[grid_index] if grid_index < len(grid_names) else "",
            "channel_index": channel_index,
            "start": s,
            "end": e,
            "total_samples": total_samples,
            "fsamp": fsamp,
            "representation": representation,
            "min": mins,
            "max": maxs,
        }
    )
