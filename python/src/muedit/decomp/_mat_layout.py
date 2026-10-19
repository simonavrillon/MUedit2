"""Normalizers for MATLAB-style decomposition payloads: cell arrays, matrices of either
orientation and case-insensitive struct fields, as .mat and schema v1 .npz files hold them."""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

from muedit.models import IntArray, spike_array

logger = logging.getLogger(__name__)


def normalize_spike_times(raw: Any) -> list[IntArray]:
    """One ``spike_array`` per MU from a list or object array of per-MU payloads."""
    if isinstance(raw, np.ndarray) and raw.dtype == object:
        raw = raw.tolist()
    if not isinstance(raw, (list, tuple)):
        return []
    return [spike_array(item) for item in raw]


def first_non_none(*values: Any) -> Any:
    """Return the first argument that is not ``None``."""
    for value in values:
        if value is not None:
            return value
    return None


def get_case_insensitive(mapping: Any, *names: str) -> Any:
    """Return the first value found under any of ``names`` in ``mapping``, case-insensitively."""
    if not isinstance(mapping, dict):
        return None
    for name in names:
        if name in mapping:
            return mapping[name]
    lower_map = {str(k).lower(): v for k, v in mapping.items()}
    for name in names:
        key = str(name).lower()
        if key in lower_map:
            return lower_map[key]
    return None


def normalize_pulse_matrix(pulse_trains: Any) -> Any:
    """Orient a 2-D pulse matrix so rows are MUs and columns are samples."""
    if not isinstance(pulse_trains, np.ndarray):
        return pulse_trains
    if pulse_trains.ndim != 2:
        return pulse_trains
    if pulse_trains.shape[0] > pulse_trains.shape[1]:
        return pulse_trains.T
    return pulse_trains


def unwrap_parameters(raw: Any) -> dict[str, Any]:
    """Unwrap a single-element object ndarray and return it as a dict."""
    if isinstance(raw, np.ndarray) and raw.dtype == object and raw.size == 1:
        raw = raw.item()
    return raw if isinstance(raw, dict) else {}


def samples_in(shape: tuple[int, ...]) -> int | None:
    """Samples in a 2-D ``(channels, samples)`` array of either orientation."""
    return int(max(shape)) if len(shape) == 2 else None


def coerce_pulse_matrix(value: Any) -> np.ndarray | None:
    """Coerce a value into a 2-D float pulse matrix (float input is not copied), or None."""
    if isinstance(value, np.ndarray):
        if value.dtype == object:
            return None
        arr = value if value.dtype.kind == "f" else np.asarray(value, dtype=np.float32)
        if arr.ndim == 1:
            arr = arr.reshape(1, -1)
        if arr.ndim != 2:
            return None
        return normalize_pulse_matrix(arr)
    return None


def _extract_grid_pulse_blocks(raw: Any) -> list[np.ndarray]:
    """Split per-grid pulse-train cell arrays into a list of 2-D arrays."""
    # Handle ngrid=1: scipy.io simplifies a {1×1 cell} to a bare numeric matrix.
    block = coerce_pulse_matrix(raw)
    if block is not None and block.size > 0:
        return [block]
    blocks: list[np.ndarray] = []
    for item in _top_level_cell_items(raw):
        block = coerce_pulse_matrix(item)
        if block is not None and block.size > 0:
            blocks.append(block)
    return blocks


def _extract_grid_spike_time_blocks(
    raw: Any, expected_grids: int | None = None
) -> list[list[IntArray]]:
    """Split per-grid discharge-time cell arrays into one list of MU spike arrays per grid."""

    if expected_grids == 1 and isinstance(raw, (list, tuple, np.ndarray)):
        if isinstance(raw, np.ndarray) and raw.ndim > 1:
            raw = np.squeeze(raw)
        all_mu = normalize_spike_times(raw)
        return [all_mu] if all_mu else []

    blocks: list[list[IntArray]] = []
    if isinstance(raw, np.ndarray) and raw.dtype == object:
        arr = np.asarray(raw, dtype=object)
        if arr.ndim == 2:
            rows: list[list[Any]]
            if expected_grids and arr.shape[0] == expected_grids:
                rows = [arr[g, :].tolist() for g in range(arr.shape[0])]
            elif expected_grids and arr.shape[1] == expected_grids:
                rows = [arr[:, g].tolist() for g in range(arr.shape[1])]
            else:
                rows = [arr[g, :].tolist() for g in range(arr.shape[0])]

            for row in rows:
                mu_lists = [mu for mu in map(spike_array, row) if mu.size]
                if mu_lists:
                    blocks.append(mu_lists)
            return blocks

    items = _top_level_cell_items(raw)
    for item in items:
        mu_lists = [mu for mu in normalize_spike_times(item) if mu.size]
        if mu_lists:
            blocks.append(mu_lists)
    return blocks


def _top_level_cell_items(raw: Any) -> list[Any]:
    """Flatten an object ndarray or sequence into a list of top-level cell items."""
    if isinstance(raw, np.ndarray) and raw.dtype == object:
        squeezed = np.squeeze(raw)
        if squeezed.ndim == 0:
            return [squeezed.item()]
        if squeezed.ndim == 1:
            return squeezed.tolist()
        return list(squeezed)
    if isinstance(raw, (list, tuple)):
        return list(raw)
    return []


def unpack_gridwise_decomposition(
    pulse_trains: Any,
    spike_times_raw: Any,
    mu_grid_index: list[int],
) -> tuple[Any, Any, list[int]]:
    """Stack per-grid pulse-train and discharge-time blocks and infer MU-to-grid indices."""
    pulse_blocks = _extract_grid_pulse_blocks(pulse_trains)
    spike_time_blocks = _extract_grid_spike_time_blocks(
        spike_times_raw,
        expected_grids=(len(pulse_blocks) if pulse_blocks else None),
    )

    inferred_from_pulse: list[int] = []
    if pulse_blocks:
        n_samples = pulse_blocks[0].shape[1]
        if all(block.shape[1] == n_samples for block in pulse_blocks):
            pulse_trains = np.vstack(pulse_blocks)
            inferred_from_pulse = [
                g_idx
                for g_idx, block in enumerate(pulse_blocks)
                for _ in range(int(block.shape[0]))
            ]

    inferred_from_spike_times: list[int] = []
    if spike_time_blocks:
        flat_spike_times: list[IntArray] = []
        if pulse_blocks:
            for g_idx, pblock in enumerate(pulse_blocks):
                expected_mu = int(pblock.shape[0])
                mu_lists = spike_time_blocks[g_idx] if g_idx < len(spike_time_blocks) else []
                aligned = list(mu_lists[:expected_mu])
                if len(aligned) < expected_mu:
                    aligned.extend(spike_array([]) for _ in range(expected_mu - len(aligned)))
                flat_spike_times.extend(aligned)
                inferred_from_spike_times.extend([g_idx] * expected_mu)
        else:
            for g_idx, mu_lists in enumerate(spike_time_blocks):
                flat_spike_times.extend(mu_lists)
                inferred_from_spike_times.extend([g_idx] * len(mu_lists))
        spike_times_raw = flat_spike_times

    if not mu_grid_index:
        if inferred_from_pulse:
            mu_grid_index = inferred_from_pulse
        elif inferred_from_spike_times:
            mu_grid_index = inferred_from_spike_times

    return pulse_trains, spike_times_raw, mu_grid_index


def infer_total_samples_from_pulse(pulse_trains: Any) -> int | None:
    """Infer the total sample count from a pulse-train matrix or cell layout."""
    matrix = coerce_pulse_matrix(pulse_trains)
    if matrix is not None and matrix.ndim == 2 and matrix.size > 0:
        return int(matrix.shape[1])
    blocks = _extract_grid_pulse_blocks(pulse_trains)
    if blocks:
        return int(blocks[0].shape[1])
    return None


def parse_discard_channels(raw: Any) -> list[np.ndarray]:
    """Parse discard-channel cell arrays into per-grid binary channel masks."""

    def _parse_mask_item(item: Any) -> np.ndarray:
        """Flatten one cell into a 1-D int array, preserving order and length."""
        if item is None:
            return np.array([], dtype=int)
        if isinstance(item, np.ndarray):
            if item.dtype == object:
                return np.array([int(v) for v in item.flatten().tolist()], dtype=int)
            try:
                return np.asarray(item, dtype=int).flatten()
            except (TypeError, ValueError):
                logger.warning(
                    "EMGmask ndarray could not be coerced to int (dtype=%s, "
                    "shape=%s); treating as empty mask.",
                    item.dtype,
                    item.shape,
                )
                return np.array([], dtype=int)
        if isinstance(item, (list, tuple)):
            try:
                return np.asarray([int(v) for v in item], dtype=int)
            except (TypeError, ValueError):
                logger.warning(
                    "Discard-channel list/tuple could not be coerced to int (len=%d); "
                    "treating as empty mask.",
                    len(item),
                )
                return np.array([], dtype=int)
        try:
            return np.asarray([int(item)], dtype=int)
        except (TypeError, ValueError):
            logger.warning(
                "EMGmask scalar could not be coerced to int (type=%s); treating as empty mask.",
                type(item).__name__,
            )
            return np.array([], dtype=int)

    items = _top_level_cell_items(raw)
    if not items and isinstance(raw, np.ndarray) and raw.dtype != object:
        if raw.ndim <= 1:
            return [_parse_mask_item(raw)]
        if raw.ndim == 2:
            return [_parse_mask_item(raw[i]) for i in range(raw.shape[0])]

    return [_parse_mask_item(item) for item in items]


def parse_signal_coordinates(raw: Any) -> list[np.ndarray]:
    """Parse signal.coordinates into a list of (n_channels, 2) float arrays, one per grid."""
    if raw is None:
        return []
    arr = np.asarray(raw)
    if arr.dtype == object:
        grids = [np.asarray(c, dtype=float) for c in arr.flatten()]
    elif arr.ndim == 3:
        grids = [arr[i].astype(float) for i in range(arr.shape[0])]
    elif arr.ndim == 2:
        grids = [arr.astype(float)]
    else:
        return []
    # (2, n_chan) → (n_chan, 2)
    return [c.T if c.shape[0] == 2 and c.shape[1] > 2 else c for c in grids if c.ndim == 2]


def parse_signal_ied(raw: Any) -> list[float] | None:
    """Parse signal.IED into a flat list of per-grid mm values."""
    if raw is None:
        return None
    arr = np.asarray(raw, dtype=float).flatten()
    return arr.tolist() if arr.size else None
