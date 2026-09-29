"""Decomposition files: the app .npz schema (save + load), MUedit .mat loading,
and signal-context normalization."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from itertools import pairwise
from pathlib import Path
from typing import Any, NamedTuple

import h5py
import numpy as np
import scipy.io
from numpy.typing import ArrayLike, DTypeLike

from muedit.io.mat import mat73_read, parse_text_list
from muedit.io.npz import NpzArchive, NpzWriter
from muedit.io.store import ArrayStore, RamStore, copy_into, sample_blocks
from muedit.models import BoolArray, EditSignalContext, FloatArray, IntArray, LoadedDecomposition
from muedit.signal.artifact_mask import intervals_to_mask, mask_to_intervals

logger = logging.getLogger(__name__)

#: Layout of the .npz files ``save_decomposition_npz`` writes; files without the key are v1.
SCHEMA_VERSION = 2
SCHEMA_KEY = "schema_version"

# BIDS-facing metadata fields a loader may attach to the signal context. Single
# source of truth shared with the API edit-signal cache so the two never drift.
LOADER_BIDS_META_KEYS: tuple[str, ...] = (
    "manufacturer",
    "device_name",
    "powerline_freq",
    "gains",
    "emg_hpf",
    "emg_lpf",
    "aux_gains",
    "aux_hpf",
    "aux_lpf",
    "aux_units",
    "hardware_filters",
    "units",
    "recording_type",
    "software_filters",
    "software_versions",
)
#: Block of a compressed (schema v1) member inflated at once.
COMPRESSED_BLOCK_BYTES = 8 * 1024 * 1024
# MATLAB signal fields that hold full-length samples, read separately and by slice.
_BULK_SIGNAL_FIELDS = frozenset({"data", "auxiliary"})


class DecompositionLoad(NamedTuple):
    """Normalized decomposition artifact fields extracted from a .npz or .mat file."""

    pulse_trains: Any
    distime_raw: Any
    fsamp: float | None
    total_samples: int | None
    grid_names: list[str]
    mu_grid_index: list[int]
    parameters: dict[str, Any]
    rois: list[tuple[int, int]]
    muscles: list[str]
    sil: list[float]
    one_row_per_mu: bool = False  # schema v2: no per-grid cell layout to unpack


def pack_csr(
    rows: Sequence[ArrayLike], dtype: DTypeLike, tail: tuple[int, ...] = ()
) -> tuple[np.ndarray, np.ndarray]:
    """``(values, offsets)``: row ``i`` is ``values[offsets[i]:offsets[i + 1]]``."""
    parts = [np.asarray(row, dtype=dtype).reshape((-1, *tail)) for row in rows]
    offsets = np.zeros(len(parts) + 1, dtype=np.int64)
    np.cumsum([len(part) for part in parts], out=offsets[1:])
    values = np.concatenate(parts) if parts else np.zeros((0, *tail), dtype=dtype)
    return values, offsets


def unpack_csr(values: np.ndarray, offsets: np.ndarray) -> list[np.ndarray]:
    """The rows ``pack_csr`` packed, as views of ``values``."""
    return [values[int(a) : int(b)] for a, b in pairwise(offsets)]


def _clean_spike_times(times: ArrayLike) -> IntArray:
    """Sorted, unique, non-negative sample indices as int32."""
    arr = np.asarray(times, dtype=np.int64).reshape(-1)
    arr = np.unique(arr[arr >= 0])
    if arr.size and arr[-1] > np.iinfo(np.int32).max:
        raise ValueError(f"spike time {int(arr[-1])} does not fit in int32")
    return arr.astype(np.int32)


def _json_default(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    return str(value)


def _json_array(value: Any) -> np.ndarray:
    """``value`` as JSON in a 0-d unicode array, which needs no pickle."""
    return np.array(json.dumps(value, default=_json_default))


def save_decomposition_npz(
    out_path: str | Path,
    pulse_trains: FloatArray | None,
    distimes: Sequence[ArrayLike],
    fsamp: float,
    grid_names: list[str],
    mu_grid_index: list[int],
    muscles: list[str],
    parameters: dict[str, Any],
    total_samples: int,
    *,
    sil: ArrayLike | None = None,
    sil_by_window: Mapping[int, ArrayLike] | None = None,
    adaptive_losses: Any = None,
    rois: Sequence[tuple[int, int]] | None = None,
    artifact_mask: BoolArray | None = None,
    emg_data: FloatArray | None = None,
    discard_channels: Sequence[IntArray] | None = None,
    coordinates: Sequence[FloatArray] | None = None,
) -> None:
    """Save a decomposition in the app's pickle-free, memory-mappable .npz schema v2."""
    spikes, spike_offsets = pack_csr([_clean_spike_times(d) for d in distimes], np.int32)
    with NpzWriter(out_path) as npz:
        npz.add(SCHEMA_KEY, np.int64(SCHEMA_VERSION))
        npz.add("fsamp", np.float64(fsamp))
        npz.add("total_samples", np.int64(total_samples))
        npz.add("spike_times", spikes)
        npz.add("spike_offsets", spike_offsets)
        npz.add("mu_grid_index", np.asarray(mu_grid_index, dtype=np.int16))
        npz.add("grid_names", _json_array(list(grid_names)))
        npz.add("muscle", _json_array(list(muscles)))
        npz.add("parameters", _json_array(parameters))
        # Without IPTs (spikes only) the loader draws binary trains from the spike times.
        if pulse_trains is not None:
            npz.add("pulse_trains", pulse_trains, np.float32)
        if sil is not None:
            npz.add("sil", np.asarray(sil, dtype=np.float64).reshape(-1))
        if sil_by_window is not None:
            keys = sorted(sil_by_window)
            values, offsets = pack_csr([sil_by_window[k] for k in keys], np.float64)
            npz.add("sil_keys", np.asarray(keys, dtype=np.int64))
            npz.add("sil_by_window", values)
            npz.add("sil_by_window_offsets", offsets)
        if adaptive_losses is not None:
            npz.add("adaptive_losses", _json_array(adaptive_losses))
        if rois is not None:
            npz.add("rois", np.asarray(rois, dtype=np.int64).reshape(-1, 2))
        if artifact_mask is not None:
            npz.add("artifact_intervals", mask_to_intervals(np.asarray(artifact_mask, bool)))
        if emg_data is not None:
            npz.add("emg_data", emg_data, np.float32)
        if discard_channels is not None:
            values, offsets = pack_csr(discard_channels, np.uint8)
            npz.add("discard_channels", values)
            npz.add("discard_channel_offsets", offsets)
        if coordinates is not None:
            values, offsets = pack_csr(coordinates, np.float32, tail=(2,))
            npz.add("coordinates", values)
            npz.add("coordinate_offsets", offsets)


def normalize_distimes(raw: Any) -> list[list[int]]:
    """Normalize discharge-time payloads into ``list[list[int]]`` format."""
    if isinstance(raw, np.ndarray) and raw.dtype == object:
        return normalize_distimes(raw.tolist())
    if isinstance(raw, (list, tuple)):
        result: list[list[int]] = []
        for item in raw:
            if item is None:
                result.append([])
            else:
                result.append(sorted(set(_collect_positive_ints(item))))
        return result
    return []


def _collect_positive_ints(value: Any) -> list[int]:
    """Flatten one per-MU payload element into a list of non-negative ints."""
    if isinstance(value, np.ndarray):
        return [int(v) for v in value.astype(int).flatten().tolist() if int(v) >= 0]
    if isinstance(value, (list, tuple)):
        return [int(v) for v in value if int(v) >= 0]
    v = int(value)
    return [v] if v >= 0 else []


def first_non_none(*values: Any) -> Any:
    """Return the first argument that is not ``None``."""
    for value in values:
        if value is not None:
            return value
    return None


def build_pulse_trains_from_distimes(distimes: list[list[int]], total_samples: int) -> np.ndarray:
    """Create a binary float32 pulse-train matrix from discharge-time indices."""
    nmu = len(distimes)
    pulses = np.zeros((nmu, total_samples), dtype=np.float32)
    for idx, times in enumerate(distimes):
        if not times:
            continue
        t = np.asarray(times, dtype=int)
        t = t[(t >= 0) & (t < total_samples)]
        pulses[idx, t] = 1.0
    return pulses


def save_editlog(
    editlog_path: Path,
    mu_uids: list[str],
    edit_history: list[dict[str, Any]],
    artifact_times: list[list[int]] | None = None,
) -> None:
    """Write a JSON editlog sidecar alongside a decomposition artifact."""
    payload: dict[str, Any] = {"mu_uids": mu_uids, "history": edit_history}
    if artifact_times:
        payload["artifact_times"] = artifact_times
    with editlog_path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)


def _parse_mu_grid_index(raw: Any) -> list[int]:
    """Normalize MU-to-grid assignment payloads to a flat integer list."""
    if raw is None:
        return []
    return [int(x) for x in np.array(raw).flatten().tolist()]


def _get_case_insensitive(mapping: Any, *names: str) -> Any:
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


def _normalize_pulse_matrix(pulse: Any) -> Any:
    """Orient a 2-D pulse matrix so rows are MUs and columns are samples."""
    if not isinstance(pulse, np.ndarray):
        return pulse
    if pulse.ndim != 2:
        return pulse
    if pulse.shape[0] > pulse.shape[1]:
        return pulse.T
    return pulse


def _unwrap_parameters(raw: Any) -> dict[str, Any]:
    """Unwrap a single-element object ndarray and return it as a dict."""
    if isinstance(raw, np.ndarray) and raw.dtype == object and raw.size == 1:
        raw = raw.item()
    return raw if isinstance(raw, dict) else {}


def _resolve_muscles(primary_raw: Any, parameters: dict[str, Any]) -> list[str]:
    """Resolve target muscle names from a primary payload or the parameters fallback."""
    muscles = parse_text_list(primary_raw)
    if not muscles:
        muscles = parse_text_list(parameters.get("target_muscle"))
    return muscles


def _parse_rois(rois_raw: Any) -> list[tuple[int, int]]:
    """Parse ``preview.rois`` into a list of ``(start, end)`` sample pairs."""
    if rois_raw is None or np.asarray(rois_raw).size == 0:
        return []
    try:
        arr = np.asarray(rois_raw, dtype=int).reshape(-1, 2)
    except (TypeError, ValueError):
        logger.warning(
            "ROI data could not be reshaped into (start, end) pairs; ignoring ROIs (raw shape: %s)",
            np.asarray(rois_raw).shape,
        )
        return []
    return [(int(start), int(end)) for start, end in arr]


def _samples_in(shape: tuple[int, ...]) -> int | None:
    """Samples in a 2-D ``(channels, samples)`` array of either orientation."""
    return int(max(shape)) if len(shape) == 2 else None


def _extract_decomp_fields(
    signal: dict[str, Any],
    preview_block: dict[str, Any],
    edition: Any,
    top: dict[str, Any],
    data_samples: int | None,
) -> DecompositionLoad:
    """Extract decomposition fields from pre-parsed MAT-derived dicts."""
    source = edition if isinstance(edition, dict) else signal
    pulse_trains = _get_case_insensitive(source, "Pulsetrain")
    if pulse_trains is None:
        pulse_trains = np.array([])
    if isinstance(pulse_trains, np.ndarray) and pulse_trains.dtype != object:
        pulse_trains = _normalize_pulse_matrix(pulse_trains)
    distime_raw = _get_case_insensitive(source, "Dischargetimes")

    fsamp_val = first_non_none(
        _get_case_insensitive(signal, "fsamp"),
        top.get("fsamp"),
    )
    fsamp = float(np.asarray(fsamp_val).ravel()[0]) if fsamp_val is not None else None

    total_samples = _infer_total_samples_from_pulse(pulse_trains)
    if total_samples is None:
        total_samples = data_samples

    rois = _parse_rois(_get_case_insensitive(preview_block, "rois"))

    gnames = first_non_none(top.get("grid_names"), _get_case_insensitive(signal, "gridname"))
    grid_names = parse_text_list(gnames) if gnames is not None else ["Grid 1"]

    mu_grid_index = _parse_mu_grid_index(top.get("mu_grid_index"))

    parameters = _unwrap_parameters(top.get("parameters"))
    muscles = _resolve_muscles(_get_case_insensitive(signal, "muscle"), parameters)

    return DecompositionLoad(
        pulse_trains=pulse_trains,
        distime_raw=distime_raw,
        fsamp=fsamp,
        total_samples=total_samples,
        grid_names=grid_names,
        mu_grid_index=mu_grid_index,
        parameters=parameters,
        rois=rois,
        muscles=muscles,
        sil=[],
    )


def _mat73_field(group: h5py.Group, name: str) -> str | None:
    """The key of ``group`` that matches ``name`` case-insensitively."""
    return next((key for key in group if key.lower() == name), None)


def _mat73_signal(h5f: h5py.File) -> dict[str, Any]:
    """The ``signal`` struct of a v7.3 file without its full-length sample fields."""
    group = h5f.get("signal")
    if not isinstance(group, h5py.Group):
        return {}
    return {
        key: mat73_read(group[key], h5f) for key in group if key.lower() not in _BULK_SIGNAL_FIELDS
    }


def _mat73_decomp(h5f: h5py.File) -> DecompositionLoad:
    """Decomposition fields of a MATLAB v7.3 (HDF5) file; the EMG samples are not read."""

    def read_root(name: str) -> Any:
        return mat73_read(h5f[name], h5f) if name in h5f else None

    preview_block = read_root("preview")
    if not isinstance(preview_block, dict):
        preview_block = {}
    top = {
        "grid_names": read_root("grid_names"),
        "mu_grid_index": read_root("mu_grid_index"),
        "parameters": read_root("parameters"),
        "fsamp": read_root("fsamp"),
    }
    data_samples = None
    group = h5f.get("signal")
    if isinstance(group, h5py.Group):
        key = _mat73_field(group, "data")
        if key is not None and isinstance(group[key], h5py.Dataset):
            data_samples = _samples_in(group[key].shape)
    return _extract_decomp_fields(
        _mat73_signal(h5f), preview_block, read_root("edition"), top, data_samples
    )


def _mat5_decomp(mat: dict[str, Any]) -> DecompositionLoad:
    """Decomposition fields of a MATLAB v5 file read by ``scipy.io.loadmat``."""
    signal = mat.get("signal")
    if not isinstance(signal, dict):
        signal = {}
    preview_block = mat.get("preview")
    if not isinstance(preview_block, dict):
        preview_block = {}
    data_block = _get_case_insensitive(signal, "data")
    data_samples = _samples_in(data_block.shape) if isinstance(data_block, np.ndarray) else None
    return _extract_decomp_fields(signal, preview_block, mat.get("edition"), mat, data_samples)


def _npz_v1_decomp(data: NpzArchive) -> DecompositionLoad:
    """Decomposition fields of a schema v1 .npz (object arrays, compressed)."""
    pulse_trains = data.get("pulse_trains")
    if pulse_trains is None:
        pulse_trains = np.array([])
    distime_raw = data.get("discharge_times")
    fsamp_val = data.get("fsamp")
    fsamp = float(np.asarray(fsamp_val).ravel()[0]) if fsamp_val is not None else None
    stored_total = data.get("total_samples")
    if stored_total is not None and int(stored_total) > 0:
        total_samples = int(stored_total)
    elif getattr(pulse_trains, "ndim", 0) == 2:
        total_samples = int(pulse_trains.shape[1])
    else:
        total_samples = None

    grid_names_raw = data.get("grid_names")
    grid_names = parse_text_list(grid_names_raw) if grid_names_raw is not None else []
    mu_grid_index = _parse_mu_grid_index(data.get("mu_grid_index"))

    parameters = _unwrap_parameters(data.get("parameters"))
    muscles = _resolve_muscles(
        first_non_none(data.get("muscle"), data.get("muscle_names")), parameters
    )

    sil_raw = data.get("sil")
    sil = np.asarray(sil_raw, dtype=float).flatten().tolist() if sil_raw is not None else []

    return DecompositionLoad(
        pulse_trains=pulse_trains,
        distime_raw=distime_raw,
        fsamp=fsamp,
        total_samples=total_samples,
        grid_names=grid_names,
        mu_grid_index=mu_grid_index,
        parameters=parameters,
        rois=_parse_rois(data.get("rois")),
        muscles=muscles,
        sil=sil,
    )


def _json_member(data: NpzArchive, key: str, default: Any) -> Any:
    raw = data.get(key)
    return json.loads(str(raw)) if raw is not None else default


def _mapped_member(data: NpzArchive, key: str) -> np.ndarray | None:
    """A member memory-mapped in place when it is stored uncompressed, else read."""
    if key not in data:
        return None
    mapped = data.memmap(key)
    return mapped if mapped is not None else data.get(key)


def _npz_v2_decomp(data: NpzArchive) -> DecompositionLoad:
    """Decomposition fields of a schema v2 .npz; the pulse trains stay memory-mapped."""
    version = int(np.asarray(data.get(SCHEMA_KEY)))
    if version > SCHEMA_VERSION:
        raise ValueError(
            f"Decomposition file schema v{version} is newer than this MUedit (v{SCHEMA_VERSION})"
        )
    spikes = data.get("spike_times")
    offsets = data.get("spike_offsets")
    distimes = (
        [row.tolist() for row in unpack_csr(spikes, offsets)]
        if spikes is not None and offsets is not None
        else []
    )
    parameters = _json_member(data, "parameters", {})
    sil_raw = data.get("sil")
    return DecompositionLoad(
        pulse_trains=_mapped_member(data, "pulse_trains"),
        distime_raw=distimes,
        fsamp=float(np.asarray(data.get("fsamp"))),
        total_samples=int(np.asarray(data.get("total_samples"))),
        grid_names=[str(g) for g in _json_member(data, "grid_names", [])],
        mu_grid_index=_parse_mu_grid_index(data.get("mu_grid_index")),
        parameters=parameters if isinstance(parameters, dict) else {},
        rois=_parse_rois(data.get("rois")),
        muscles=_resolve_muscles(_json_member(data, "muscle", []), parameters),
        sil=np.asarray(sil_raw, dtype=float).tolist() if sil_raw is not None else [],
        one_row_per_mu=True,
    )


def _coerce_pulse_matrix(value: Any) -> np.ndarray | None:
    """Coerce a value into a 2-D float pulse matrix (float input is not copied), or None."""
    if isinstance(value, np.ndarray):
        if value.dtype == object:
            return None
        arr = value if value.dtype.kind == "f" else np.asarray(value, dtype=np.float32)
        if arr.ndim == 1:
            arr = arr.reshape(1, -1)
        if arr.ndim != 2:
            return None
        return _normalize_pulse_matrix(arr)
    return None


def _extract_grid_pulse_blocks(raw: Any) -> list[np.ndarray]:
    """Split per-grid pulse-train cell arrays into a list of 2-D arrays."""
    # Handle ngrid=1: scipy.io simplifies a {1×1 cell} to a bare numeric matrix.
    block = _coerce_pulse_matrix(raw)
    if block is not None and block.size > 0:
        return [block]
    blocks: list[np.ndarray] = []
    for item in _top_level_cell_items(raw):
        block = _coerce_pulse_matrix(item)
        if block is not None and block.size > 0:
            blocks.append(block)
    return blocks


def _extract_grid_distime_blocks(
    raw: Any, expected_grids: int | None = None
) -> list[list[list[int]]]:
    """Split per-grid discharge-time cell arrays into nested lists of MU index lists."""

    def _mu_vector(cell: Any) -> list[int]:
        if cell is None:
            return []
        return sorted(set(_collect_positive_ints(cell)))

    if expected_grids == 1 and isinstance(raw, (list, tuple, np.ndarray)):
        if isinstance(raw, np.ndarray) and raw.ndim > 1:
            raw = np.squeeze(raw)
        all_mu = normalize_distimes(raw)
        return [all_mu] if all_mu else []

    blocks: list[list[list[int]]] = []
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
                mu_lists: list[list[int]] = []
                for cell in row:
                    mu_vec = _mu_vector(cell)
                    if mu_vec:
                        mu_lists.append(mu_vec)
                if mu_lists:
                    blocks.append(mu_lists)
            return blocks

    items = _top_level_cell_items(raw)
    for item in items:
        mu_lists = normalize_distimes(item)
        mu_lists = [mu for mu in mu_lists if mu]
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


def _unpack_gridwise_decomposition(
    pulse_trains: Any,
    distime_raw: Any,
    mu_grid_index: list[int],
) -> tuple[Any, Any, list[int]]:
    """Stack per-grid pulse/distime blocks and infer MU-to-grid indices."""
    pulse_blocks = _extract_grid_pulse_blocks(pulse_trains)
    distime_blocks = _extract_grid_distime_blocks(
        distime_raw,
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

    inferred_from_distime: list[int] = []
    if distime_blocks:
        flat_distimes: list[list[int]] = []
        if pulse_blocks:
            for g_idx, pblock in enumerate(pulse_blocks):
                expected_mu = int(pblock.shape[0])
                mu_lists = distime_blocks[g_idx] if g_idx < len(distime_blocks) else []
                aligned = list(mu_lists[:expected_mu])
                if len(aligned) < expected_mu:
                    aligned.extend([[] for _ in range(expected_mu - len(aligned))])
                flat_distimes.extend(aligned)
                inferred_from_distime.extend([g_idx] * expected_mu)
        else:
            for g_idx, mu_lists in enumerate(distime_blocks):
                flat_distimes.extend(mu_lists)
                inferred_from_distime.extend([g_idx] * len(mu_lists))
        distime_raw = flat_distimes

    if not mu_grid_index:
        if inferred_from_pulse:
            mu_grid_index = inferred_from_pulse
        elif inferred_from_distime:
            mu_grid_index = inferred_from_distime

    return pulse_trains, distime_raw, mu_grid_index


def _infer_total_samples_from_pulse(pulse_trains: Any) -> int | None:
    """Infer the total sample count from a pulse-train matrix or cell layout."""
    matrix = _coerce_pulse_matrix(pulse_trains)
    if matrix is not None and matrix.ndim == 2 and matrix.size > 0:
        return int(matrix.shape[1])
    blocks = _extract_grid_pulse_blocks(pulse_trains)
    if blocks:
        return int(blocks[0].shape[1])
    return None


def _distimes_from_pulse_matrix(matrix: np.ndarray) -> list[list[int]]:
    """Derive discharge-time indices from non-zero entries in a pulse matrix."""
    return [np.flatnonzero(np.asarray(row) != 0).astype(int).tolist() for row in matrix]


def _shift_distimes(values: list[list[int]], shift: int, limit: int) -> list[list[int]]:
    """Shift all discharge times by a constant and clip to [0, limit)."""
    shifted: list[list[int]] = []
    for row in values:
        adj = [int(v) + shift for v in row]
        adj = [v for v in adj if 0 <= v < limit]
        shifted.append(sorted(set(adj)))
    return shifted


def _finish_decomposition(d: DecompositionLoad, one_based: bool) -> LoadedDecomposition:
    """Flatten per-grid layouts and fill what the file leaves out, for the edit stage."""
    if d.one_row_per_mu:
        pulse_trains, distimes, mu_grid_index = d.pulse_trains, d.distime_raw, d.mu_grid_index
    else:
        pulse_trains, distime_raw, mu_grid_index = _unpack_gridwise_decomposition(
            d.pulse_trains,
            d.distime_raw,
            d.mu_grid_index,
        )
        distimes = normalize_distimes(distime_raw)

    grid_names = d.grid_names
    if not grid_names:
        grid_names = [
            f"Grid {i + 1}" for i in range(max(mu_grid_index) + 1 if mu_grid_index else 1)
        ]

    total_samples = int(
        d.total_samples or (pulse_trains.shape[1] if getattr(pulse_trains, "ndim", 0) == 2 else 0)
    )
    if total_samples <= 0 and distimes:
        max_spike = max((max(x) for x in distimes if x), default=-1)
        total_samples = max_spike + 1 if max_spike >= 0 else 0

    if one_based and distimes:
        logger.info("Discharge times: 1-based (MAT export convention), shifting -1")
        distimes = _shift_distimes(distimes, -1, int(total_samples))

    pulse_matrix = _coerce_pulse_matrix(pulse_trains)
    if pulse_matrix is None or (pulse_matrix.size == 0 and distimes):
        pulse_matrix = build_pulse_trains_from_distimes(distimes, total_samples)
    if not distimes:
        distimes = _distimes_from_pulse_matrix(pulse_matrix)

    if not mu_grid_index or len(mu_grid_index) != len(distimes):
        mu_grid_index = [0] * len(distimes)

    return LoadedDecomposition(
        pulse_trains_full=pulse_matrix,
        distime_all=distimes,
        fsamp=d.fsamp,
        grid_names=grid_names,
        total_samples=int(total_samples or 0),
        mu_grid_index=mu_grid_index,
        rois=d.rois,
        parameters=d.parameters,
        muscle=d.muscles,
        sil=d.sil,
    )


def _parse_emgmask_cells(raw: Any) -> list[np.ndarray]:
    """Parse EMGmask cell arrays into per-grid binary channel masks."""

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
                    "EMGmask list/tuple could not be coerced to int (len=%d); "
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


def _parse_signal_coordinates(raw: Any) -> list[np.ndarray]:
    """Parse signal.coordinates into a list of (n_channels, 2) float arrays, one per grid."""
    if raw is None:
        return []
    arr = np.asarray(raw)
    if arr.dtype == object:
        result = []
        for c in arr.flatten():
            c_arr = np.asarray(c, dtype=float)
            if c_arr.ndim == 2 and c_arr.shape[0] == 2 and c_arr.shape[1] > 2:
                c_arr = c_arr.T  # (2, n_chan) → (n_chan, 2)
            if c_arr.ndim == 2:
                result.append(c_arr)
        return result
    if arr.ndim == 3:
        result = []
        for i in range(arr.shape[0]):
            c = arr[i].astype(float)
            if c.shape[0] == 2 and c.shape[1] > 2:
                c = c.T  # (2, n_chan) → (n_chan, 2)
            result.append(c)
        return result
    if arr.ndim == 2:
        c = arr.astype(float)
        if c.shape[0] == 2 and c.shape[1] > 2:
            c = c.T  # (2, n_chan) → (n_chan, 2)
        return [c]
    return []


def _parse_signal_ied(raw: Any) -> list[float] | None:
    """Parse signal.IED into a flat list of per-grid mm values."""
    if raw is None:
        return None
    arr = np.asarray(raw, dtype=float).flatten()
    return arr.tolist() if arr.size else None


def _npz_emg(data: NpzArchive, store: ArrayStore) -> FloatArray | None:
    """``emg_data`` as float32 ``(channels, samples)`` in ``store``, copied block by block."""
    info = data.info("emg_data")
    if info.dtype.kind not in "biuf" or len(info.shape) not in (1, 2) or 0 in info.shape:
        return None
    logical = info.shape if len(info.shape) == 2 else (1, info.shape[0])
    mapped = data.memmap("emg_data")
    if mapped is not None:
        emg = mapped.reshape(logical)
        return copy_into(store, "emg", emg.T if logical[0] > logical[1] else emg)
    rows, cols = info.stored_shape if len(info.shape) == 2 else logical
    # Channels are the shorter axis of the array as saved; the bytes may hold its transpose.
    transposed = info.fortran_order != (logical[0] > logical[1])
    out = store.allocate("emg", (cols, rows) if transposed else (rows, cols), np.float32)
    # A compressed member is inflated through a few block-sized buffers at a time.
    for start, block in data.row_blocks("emg_data", COMPRESSED_BLOCK_BYTES):
        if transposed:
            out[:, start : start + len(block)] = block.T
        else:
            out[start : start + len(block)] = block
    return store.seal(out)


def _npz_signal_context(
    data: NpzArchive, store: ArrayStore, total_samples: int | None
) -> EditSignalContext | None:
    """The EMG, channel masks and artifact mask a .npz embeds, if any."""
    v2 = SCHEMA_KEY in data
    emg = _npz_emg(data, store) if "emg_data" in data else None

    fsamp_val = data.get("fsamp")
    fsamp = float(np.asarray(fsamp_val).ravel()[0]) if fsamp_val is not None else None

    artifact_mask: BoolArray | None = None
    if v2:
        grid_names = [str(g) for g in _json_member(data, "grid_names", [])]
        emgmask: list[IntArray] = []
        discard = data.get("discard_channels")
        discard_offsets = data.get("discard_channel_offsets")
        if discard is not None and discard_offsets is not None:
            emgmask = [row.astype(int) for row in unpack_csr(discard, discard_offsets)]
        coordinates: list[FloatArray] = []
        coords = data.get("coordinates")
        coord_offsets = data.get("coordinate_offsets")
        if coords is not None and coord_offsets is not None:
            coordinates = [row.astype(float) for row in unpack_csr(coords, coord_offsets)]
        intervals = data.get("artifact_intervals")
        if intervals is not None and total_samples:
            artifact_mask = intervals_to_mask(intervals, total_samples)
    else:
        grid_names_raw = data.get("grid_names")
        grid_names = parse_text_list(grid_names_raw) if grid_names_raw is not None else []
        emgmask = _parse_emgmask_cells(data.get("discard_channels"))
        coordinates = _parse_signal_coordinates(data.get("coordinates"))
        mask_raw = data.get("artifact_mask")
        if mask_raw is not None:
            artifact_mask = np.asarray(mask_raw, dtype=bool)

    if emg is None and artifact_mask is None:
        return None

    return EditSignalContext(
        data=emg if emg is not None else np.zeros((0, 0), dtype=np.float32),
        fsamp=fsamp or 0.0,
        grid_names=grid_names,
        emgmask=emgmask,
        coordinates=coordinates,
        artifact_mask=artifact_mask,
        prefiltered=emg is not None and not v2,
    )


def _mat73_emg(h5f: h5py.File, store: ArrayStore) -> FloatArray | None:
    """``signal.data`` of a v7.3 file as float32 ``(channels, samples)``, read by slice."""
    group = h5f.get("signal")
    key = _mat73_field(group, "data") if isinstance(group, h5py.Group) else None
    if key is None:
        return None
    node = group[key]
    if not isinstance(node, h5py.Dataset) or node.dtype.kind not in "biuf":
        raw = mat73_read(node, h5f)
        if not isinstance(raw, np.ndarray) or raw.size == 0 or raw.ndim > 2:
            return None
        return copy_into(store, "emg", raw.reshape(1, -1) if raw.ndim == 1 else raw.T)
    if node.size == 0 or node.ndim not in (1, 2):
        return None
    if node.ndim == 1:
        return copy_into(store, "emg", node[()].reshape(1, -1))
    # HDF5 holds MATLAB's column-major (channels, samples) as (samples, channels).
    n_samples, n_channels = node.shape
    out = store.allocate("emg", (n_channels, n_samples), np.float32)
    for start, stop in sample_blocks(n_samples, n_channels):
        out[:, start:stop] = node[start:stop, :].T
    return store.seal(out)


def _mat_signal_context(
    signal: dict[str, Any], top: dict[str, Any], emg: FloatArray | None, store: ArrayStore
) -> EditSignalContext | None:
    """The EMG context of a MATLAB decomposition file, from its parsed ``signal`` struct."""
    if emg is None:
        return None

    fsamp_val = first_non_none(
        _get_case_insensitive(signal, "fsamp"),
        top.get("fsamp"),
    )
    fsamp = float(np.asarray(fsamp_val).ravel()[0]) if fsamp_val is not None else None

    grid_names_raw = first_non_none(
        _get_case_insensitive(signal, "gridname"),
        top.get("grid_names"),
    )
    grid_names = parse_text_list(grid_names_raw)

    emgmask_raw = first_non_none(
        _get_case_insensitive(signal, "EMGmask", "emgmask"),
        top.get("EMGmask"),
        top.get("emgmask"),
    )
    emgmask = _parse_emgmask_cells(emgmask_raw)

    coordinates_raw = _get_case_insensitive(signal, "coordinates")
    coordinates = _parse_signal_coordinates(coordinates_raw)

    ied_raw = first_non_none(
        _get_case_insensitive(signal, "IED", "ied"),
        top.get("IED"),
        top.get("ied"),
    )
    ied = _parse_signal_ied(ied_raw)

    aux_raw = _get_case_insensitive(signal, "auxiliary")
    aux_data: FloatArray | None = None
    if isinstance(aux_raw, np.ndarray) and aux_raw.size > 0:
        aux_arr = aux_raw.reshape(1, -1) if aux_raw.ndim == 1 else aux_raw
        if aux_arr.ndim == 2:
            if aux_arr.shape[0] > aux_arr.shape[1]:
                aux_arr = aux_arr.T
            aux_data = copy_into(store, "aux", aux_arr)

    aux_names = parse_text_list(_get_case_insensitive(signal, "auxiliaryname"))

    meta_raw = signal.get("metadata") or top.get("metadata") or {}
    meta = meta_raw if isinstance(meta_raw, dict) else {}

    return EditSignalContext(
        data=emg,
        fsamp=fsamp or 0.0,
        grid_names=grid_names,
        emgmask=emgmask,
        coordinates=coordinates,
        ied=ied,
        aux_data=aux_data,
        aux_names=aux_names,
        loader_meta={key: meta[key] for key in LOADER_BIDS_META_KEYS if key in meta},
    )


def _mat73_signal_context(h5f: h5py.File, store: ArrayStore) -> EditSignalContext | None:
    emg = _mat73_emg(h5f, store)
    if emg is None:
        return None
    signal = _mat73_signal(h5f)
    group = h5f.get("signal")
    aux_key = _mat73_field(group, "auxiliary") if isinstance(group, h5py.Group) else None
    if aux_key is not None:
        signal[aux_key] = mat73_read(group[aux_key], h5f)
    top = {
        key: mat73_read(h5f[key], h5f)
        for key in ("fsamp", "grid_names", "EMGmask", "emgmask", "metadata")
        if key in h5f
    }
    return _mat_signal_context(signal, top, emg, store)


def _mat5_signal_context(mat: dict[str, Any], store: ArrayStore) -> EditSignalContext | None:
    candidate = mat.get("signal")
    signal = candidate if isinstance(candidate, dict) else {}
    data = _get_case_insensitive(signal, "data")
    if not isinstance(data, np.ndarray) or data.size == 0 or data.ndim > 2:
        return None
    if data.dtype.kind not in "biuf":
        return None
    emg = copy_into(store, "emg", data.reshape(1, -1) if data.ndim == 1 else data)
    return _mat_signal_context(signal, mat, emg, store)


def load_decomposition(
    filepath: str, store: ArrayStore | None = None, *, with_signal: bool = True
) -> tuple[LoadedDecomposition, EditSignalContext | None]:
    """Read a .npz or .mat decomposition once: the decomposition, and its EMG context into ``store``."""
    store = store if store is not None else RamStore()
    ext = Path(filepath).suffix.lower()
    ctx: EditSignalContext | None = None
    if ext == ".npz":
        with NpzArchive(filepath) as data:
            d = _npz_v2_decomp(data) if SCHEMA_KEY in data else _npz_v1_decomp(data)
            if with_signal:
                ctx = _npz_signal_context(data, store, d.total_samples)
    elif ext == ".mat":
        if h5py.is_hdf5(filepath):
            with h5py.File(filepath, "r") as h5f:
                d = _mat73_decomp(h5f)
                if with_signal:
                    ctx = _mat73_signal_context(h5f, store)
        else:
            mat = scipy.io.loadmat(filepath, simplify_cells=True)
            d = _mat5_decomp(mat)
            if with_signal:
                ctx = _mat5_signal_context(mat, store)
    else:
        raise ValueError("Unsupported decomposition format. Expected .mat or .npz")
    return _finish_decomposition(d, one_based=ext == ".mat"), ctx


def load_decomposition_file(filepath: str) -> LoadedDecomposition:
    """Load a decomposition file (.npz or .mat) and normalize it for editing."""
    return load_decomposition(filepath, with_signal=False)[0]


def load_decomposition_signal_context(
    filepath: str, store: ArrayStore | None = None
) -> EditSignalContext | None:
    """The EMG context a decomposition file embeds, if any, with its EMG in ``store``."""
    return load_decomposition(filepath, store)[1]
