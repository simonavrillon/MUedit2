"""Decomposition files: the app .npz schema, MUedit .mat loading, signal-context normalization."""

from __future__ import annotations

import json
import logging
import pickle
import zipfile
from collections.abc import Mapping, Sequence
from itertools import pairwise
from pathlib import Path
from typing import Any, NamedTuple

import h5py
import numpy as np
import scipy.io
from numpy.typing import ArrayLike, DTypeLike

from muedit.decomp._mat_layout import (
    coerce_pulse_matrix,
    first_non_none,
    get_case_insensitive,
    infer_total_samples_from_pulse,
    normalize_pulse_matrix,
    normalize_spike_times,
    parse_discard_channels,
    parse_signal_coordinates,
    parse_signal_ied,
    samples_in,
    unpack_gridwise_decomposition,
    unwrap_parameters,
)
from muedit.errors import MueditError
from muedit.io._mat import mat73_read, parse_name_list, parse_text_list
from muedit.io.npz import NpzArchive, NpzWriter, RowSource
from muedit.models import (
    BoolArray,
    EditSignalContext,
    FloatArray,
    IntArray,
    LoadedDecomposition,
    spike_array,
)
from muedit.signal.artifact_mask import intervals_to_mask, mask_to_intervals
from muedit.signal.grid import fallback_grid_name
from muedit.storage import ArrayStore, RamStore, copy_into, sample_blocks

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
    "aux_kinds",
    "grid_labels",
    "hardware_filters",
    "units",
    "recording_type",
    "software_filters",
    "software_versions",
    "placement_scheme",
    "placement_scheme_description",
    "reference_location",
    "ground_location",
    "acquisition_date",
    "electrode_impedances",
)
#: Block of a compressed (schema v1) member inflated at once.
COMPRESSED_BLOCK_BYTES = 8 * 1024 * 1024
# MATLAB signal fields that hold full-length samples, read separately and by slice.
_BULK_SIGNAL_FIELDS = frozenset({"data", "auxiliary"})


class DecompositionLoad(NamedTuple):
    """Normalized decomposition artifact fields extracted from a .npz or .mat file."""

    pulse_trains: Any
    spike_times_raw: Any
    fsamp: float | None
    total_samples: int | None
    grid_names: list[str]
    mu_grid_index: list[int]
    parameters: dict[str, Any]
    rois: list[tuple[int, int]]
    muscle_names: list[str]
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
    pulse_trains: FloatArray | RowSource | None,
    spike_times: Sequence[ArrayLike],
    fsamp: float,
    grid_names: list[str],
    mu_grid_index: list[int],
    muscle_names: list[str],
    parameters: dict[str, Any],
    total_samples: int,
    *,
    sil: ArrayLike | None = None,
    sil_by_window: Mapping[int, ArrayLike] | None = None,
    rois: Sequence[tuple[int, int]] | None = None,
    artifact_mask: BoolArray | None = None,
    emg_data: FloatArray | None = None,
    discard_channels: Sequence[IntArray] | None = None,
    coordinates: Sequence[FloatArray] | None = None,
    loader_meta: Mapping[str, Any] | None = None,
    target_segments: Mapping[str, Any] | None = None,
) -> None:
    """Save a decomposition in the app's pickle-free, memory-mappable .npz schema v2."""
    spikes, spike_offsets = pack_csr([spike_array(d) for d in spike_times], np.int32)
    with NpzWriter(out_path) as npz:
        npz.add(SCHEMA_KEY, np.int64(SCHEMA_VERSION))
        npz.add("fsamp", np.float64(fsamp))
        npz.add("total_samples", np.int64(total_samples))
        npz.add("spike_times", spikes)
        npz.add("spike_offsets", spike_offsets)
        npz.add("mu_grid_index", np.asarray(mu_grid_index, dtype=np.int16))
        npz.add("grid_names", _json_array(list(grid_names)))
        npz.add("muscle_names", _json_array(list(muscle_names)))
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
        if loader_meta is not None:
            npz.add("loader_meta", _json_array(_bids_meta(loader_meta)))
        if target_segments is not None:
            npz.add("target_segments", _json_array(dict(target_segments)))


def _bids_meta(meta: Mapping[str, Any]) -> dict[str, Any]:
    """The ``LOADER_BIDS_META_KEYS`` fields of ``meta``."""
    return {key: meta[key] for key in LOADER_BIDS_META_KEYS if key in meta}


def build_pulse_trains_from_spike_times(
    spike_times: Sequence[ArrayLike], total_samples: int
) -> np.ndarray:
    """Create a binary float32 pulse-train matrix from discharge-time indices."""
    pulses = np.zeros((len(spike_times), total_samples), dtype=np.float32)
    for idx, times in enumerate(spike_times):
        t = spike_array(times)
        pulses[idx, t[t < total_samples]] = 1.0
    return pulses


def save_editlog(
    editlog_path: Path,
    mu_uids: list[str],
    history: list[dict[str, Any]],
    artifact_times: Sequence[IntArray] | None = None,
) -> None:
    """Write a JSON editlog sidecar alongside a decomposition artifact."""
    payload: dict[str, Any] = {"mu_uids": mu_uids, "history": history}
    if artifact_times is not None and any(np.asarray(a).size for a in artifact_times):
        payload["artifact_times"] = [np.asarray(a).tolist() for a in artifact_times]
    with editlog_path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)


def _parse_mu_grid_index(raw: Any) -> list[int]:
    """Normalize MU-to-grid assignment payloads to a flat integer list; a negative grid raises."""
    if raw is None:
        return []
    values = [int(x) for x in np.array(raw).flatten().tolist()]
    if any(v < 0 for v in values):
        raise ValueError(f"mu_grid_index holds a negative grid index ({min(values)})")
    return values


def _resolve_muscle_names(primary_raw: Any, parameters: dict[str, Any]) -> list[str]:
    """Resolve target muscle names from a primary payload or the parameters fallback."""
    return parse_name_list(primary_raw) or parse_name_list(parameters.get("target_muscle"))


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


def _extract_decomp_fields(
    signal: dict[str, Any],
    preview_block: dict[str, Any],
    edition: Any,
    top: dict[str, Any],
    data_samples: int | None,
) -> DecompositionLoad:
    """Extract decomposition fields from pre-parsed MAT-derived dicts."""
    source = edition if isinstance(edition, dict) else signal
    pulse_trains = get_case_insensitive(source, "Pulsetrain")
    if pulse_trains is None:
        pulse_trains = np.array([])
    if isinstance(pulse_trains, np.ndarray) and pulse_trains.dtype != object:
        pulse_trains = normalize_pulse_matrix(pulse_trains)
    spike_times_raw = get_case_insensitive(source, "Dischargetimes")

    fsamp_val = first_non_none(
        get_case_insensitive(signal, "fsamp"),
        top.get("fsamp"),
    )
    fsamp = _first_float(fsamp_val)

    total_samples = infer_total_samples_from_pulse(pulse_trains)
    if total_samples is None:
        total_samples = data_samples

    rois = _parse_rois(get_case_insensitive(preview_block, "rois"))

    gnames = first_non_none(top.get("grid_names"), get_case_insensitive(signal, "gridname"))
    grid_names = parse_text_list(gnames) if gnames is not None else [fallback_grid_name(0)]

    mu_grid_index = _parse_mu_grid_index(top.get("mu_grid_index"))

    parameters = unwrap_parameters(top.get("parameters"))
    muscle_names = _resolve_muscle_names(get_case_insensitive(signal, "muscle"), parameters)

    return DecompositionLoad(
        pulse_trains=pulse_trains,
        spike_times_raw=spike_times_raw,
        fsamp=fsamp,
        total_samples=total_samples,
        grid_names=grid_names,
        mu_grid_index=mu_grid_index,
        parameters=parameters,
        rois=rois,
        muscle_names=muscle_names,
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
            data_samples = samples_in(group[key].shape)
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
    data_block = get_case_insensitive(signal, "data")
    data_samples = samples_in(data_block.shape) if isinstance(data_block, np.ndarray) else None
    return _extract_decomp_fields(signal, preview_block, mat.get("edition"), mat, data_samples)


def _npz_v1_decomp(data: NpzArchive) -> DecompositionLoad:
    """Decomposition fields of a schema v1 .npz (object arrays, compressed)."""
    pulse_trains = data.get("pulse_trains")
    if pulse_trains is None:
        pulse_trains = np.array([])
    spike_times_raw = data.get("discharge_times")
    fsamp_val = data.get("fsamp")
    fsamp = _first_float(fsamp_val)
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

    parameters = unwrap_parameters(data.get("parameters"))
    muscle_names = _resolve_muscle_names(
        first_non_none(data.get("muscle_names"), data.get("muscle")), parameters
    )

    sil_raw = data.get("sil")
    sil = np.asarray(sil_raw, dtype=float).flatten().tolist() if sil_raw is not None else []

    return DecompositionLoad(
        pulse_trains=pulse_trains,
        spike_times_raw=spike_times_raw,
        fsamp=fsamp,
        total_samples=total_samples,
        grid_names=grid_names,
        mu_grid_index=mu_grid_index,
        parameters=parameters,
        rois=_parse_rois(data.get("rois")),
        muscle_names=muscle_names,
        sil=sil,
    )


def _first_float(raw: Any) -> float | None:
    """The first value of a scalar or array payload as a float, or ``None``."""
    return float(np.asarray(raw).ravel()[0]) if raw is not None else None


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
    spike_times = (
        [spike_array(row) for row in unpack_csr(spikes, offsets)]
        if spikes is not None and offsets is not None
        else []
    )
    parameters = _json_member(data, "parameters", {})
    sil_raw = data.get("sil")
    # v2 files written before the muscle_names rename hold "muscle"; read both.
    muscle_raw = _json_member(data, "muscle_names", None)
    if muscle_raw is None:
        muscle_raw = _json_member(data, "muscle", [])
    return DecompositionLoad(
        pulse_trains=_mapped_member(data, "pulse_trains"),
        spike_times_raw=spike_times,
        fsamp=float(np.asarray(data.get("fsamp"))),
        total_samples=int(np.asarray(data.get("total_samples"))),
        grid_names=[str(g) for g in _json_member(data, "grid_names", [])],
        mu_grid_index=_parse_mu_grid_index(data.get("mu_grid_index")),
        parameters=parameters if isinstance(parameters, dict) else {},
        rois=_parse_rois(data.get("rois")),
        muscle_names=_resolve_muscle_names(muscle_raw, parameters),
        sil=np.asarray(sil_raw, dtype=float).tolist() if sil_raw is not None else [],
        one_row_per_mu=True,
    )


def _spike_times_from_pulse_matrix(matrix: np.ndarray) -> list[IntArray]:
    """Derive discharge-time indices from non-zero entries in a pulse matrix."""
    return [spike_array(np.flatnonzero(np.asarray(row) != 0)) for row in matrix]


def _shift_spike_times(values: list[IntArray], shift: int, limit: int) -> list[IntArray]:
    """Shift all discharge times by a constant and clip to [0, limit)."""
    shifted = [row.astype(np.int64) + shift for row in values]
    return [spike_array(row[row < limit]) for row in shifted]


def _finish_decomposition(
    d: DecompositionLoad, one_based: bool, binary_trains: bool = True
) -> LoadedDecomposition:
    """Flatten per-grid layouts for editing; ``binary_trains`` draws missing trains from spikes."""
    if d.one_row_per_mu:
        pulse_trains, spike_times, mu_grid_index = (
            d.pulse_trains,
            d.spike_times_raw,
            d.mu_grid_index,
        )
    else:
        pulse_trains, spike_times_raw, mu_grid_index = unpack_gridwise_decomposition(
            d.pulse_trains,
            d.spike_times_raw,
            d.mu_grid_index,
        )
        spike_times = normalize_spike_times(spike_times_raw)

    grid_names = d.grid_names
    if not grid_names:
        ngrid = max(mu_grid_index) + 1 if mu_grid_index else 1
        grid_names = [fallback_grid_name(i) for i in range(ngrid)]

    total_samples = int(
        d.total_samples or (pulse_trains.shape[1] if getattr(pulse_trains, "ndim", 0) == 2 else 0)
    )
    if total_samples <= 0 and spike_times:
        max_spike = max((int(x[-1]) for x in spike_times if x.size), default=-1)
        total_samples = max_spike + 1 if max_spike >= 0 else 0

    if one_based and spike_times:
        logger.info("Discharge times: 1-based (MAT export convention), shifting -1")
        spike_times = _shift_spike_times(spike_times, -1, int(total_samples))

    pulse_matrix = coerce_pulse_matrix(pulse_trains)
    if pulse_matrix is None or (pulse_matrix.size == 0 and spike_times):
        pulse_matrix = (
            build_pulse_trains_from_spike_times(spike_times, total_samples)
            if binary_trains
            else np.zeros((0, 0), dtype=np.float32)
        )
    if not spike_times:
        spike_times = _spike_times_from_pulse_matrix(pulse_matrix)

    if not mu_grid_index or len(mu_grid_index) != len(spike_times):
        mu_grid_index = [0] * len(spike_times)

    return LoadedDecomposition(
        pulse_trains=pulse_matrix,
        spike_times=spike_times,
        fsamp=d.fsamp or 0.0,
        grid_names=grid_names,
        total_samples=int(total_samples or 0),
        mu_grid_index=mu_grid_index,
        rois=d.rois,
        parameters=d.parameters,
        muscle_names=d.muscle_names,
        sil=d.sil,
    )


def _samples_first(shape: tuple[int, ...], total_samples: int | None) -> bool:
    """Whether a saved ``(rows, cols)`` EMG is samples-first, judged by ``total_samples``."""
    rows, cols = shape
    if total_samples:
        # Columns first: a square array is channels-first, as the save writes it.
        if cols == total_samples:
            return False
        if rows == total_samples:
            return True
    # No ``total_samples`` match: channels are the shorter axis.
    return rows > cols


def _npz_emg(data: NpzArchive, store: ArrayStore, total_samples: int | None) -> FloatArray | None:
    """``emg_data`` as float32 ``(channels, samples)``, mapped in place if so stored, else in ``store``."""
    info = data.info("emg_data")
    if info.dtype.kind not in "biuf" or len(info.shape) not in (1, 2) or 0 in info.shape:
        return None
    logical = info.shape if len(info.shape) == 2 else (1, info.shape[0])
    samples_first = _samples_first(logical, total_samples)
    mapped = data.memmap("emg_data")
    if mapped is not None:
        emg = mapped.reshape(logical)
        emg = emg.T if samples_first else emg
        return emg if emg.dtype == np.float32 else copy_into(store, "emg", emg)
    rows, cols = info.stored_shape if len(info.shape) == 2 else logical
    # The bytes may hold the transpose of the array as saved.
    transposed = info.fortran_order != samples_first
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
    emg = _npz_emg(data, store, total_samples) if "emg_data" in data else None

    fsamp_val = data.get("fsamp")
    fsamp = _first_float(fsamp_val)

    artifact_mask: BoolArray | None = None
    loader_meta: dict[str, Any] = {}
    target_segments: dict[str, Any] | None = None
    if v2:
        stored_segments = _json_member(data, "target_segments", None)
        if isinstance(stored_segments, dict):
            target_segments = stored_segments
        grid_names = [str(g) for g in _json_member(data, "grid_names", [])]
        stored_meta = _json_member(data, "loader_meta", {})
        if isinstance(stored_meta, dict):
            loader_meta = _bids_meta(stored_meta)
        discard_channels: list[IntArray] = []
        discard = data.get("discard_channels")
        discard_offsets = data.get("discard_channel_offsets")
        if discard is not None and discard_offsets is not None:
            discard_channels = [row.astype(int) for row in unpack_csr(discard, discard_offsets)]
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
        discard_channels = parse_discard_channels(data.get("discard_channels"), grid_names)
        coordinates = parse_signal_coordinates(data.get("coordinates"))
        mask_raw = data.get("artifact_mask")
        if mask_raw is not None:
            artifact_mask = np.asarray(mask_raw, dtype=bool)

    if emg is None and artifact_mask is None and target_segments is None and not loader_meta:
        return None

    return EditSignalContext(
        data=emg if emg is not None else np.zeros((0, 0), dtype=np.float32),
        fsamp=fsamp or 0.0,
        grid_names=grid_names,
        discard_channels=discard_channels,
        coordinates=coordinates,
        artifact_mask=artifact_mask,
        loader_meta=loader_meta,
        prefiltered=emg is not None and not v2,
        target_segments=target_segments,
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
        get_case_insensitive(signal, "fsamp"),
        top.get("fsamp"),
    )
    fsamp = _first_float(fsamp_val)

    grid_names_raw = first_non_none(
        get_case_insensitive(signal, "gridname"),
        top.get("grid_names"),
    )
    grid_names = parse_text_list(grid_names_raw)

    discard_raw = first_non_none(
        get_case_insensitive(signal, "EMGmask", "emgmask"),
        top.get("EMGmask"),
        top.get("emgmask"),
    )
    discard_channels = parse_discard_channels(discard_raw, grid_names)

    coordinates_raw = get_case_insensitive(signal, "coordinates")
    coordinates = parse_signal_coordinates(coordinates_raw)

    ied_raw = first_non_none(
        get_case_insensitive(signal, "IED", "ied"),
        top.get("IED"),
        top.get("ied"),
    )
    ied = parse_signal_ied(ied_raw)

    aux_raw = get_case_insensitive(signal, "auxiliary")
    aux_data: FloatArray | None = None
    if isinstance(aux_raw, np.ndarray) and aux_raw.size > 0:
        aux_arr = aux_raw.reshape(1, -1) if aux_raw.ndim == 1 else aux_raw
        if aux_arr.ndim == 2:
            if aux_arr.shape[0] > aux_arr.shape[1]:
                aux_arr = aux_arr.T
            aux_data = copy_into(store, "aux", aux_arr)

    aux_names = parse_text_list(get_case_insensitive(signal, "auxiliaryname"))

    meta_raw = signal.get("metadata") or top.get("metadata") or {}
    meta = meta_raw if isinstance(meta_raw, dict) else {}

    return EditSignalContext(
        data=emg,
        fsamp=fsamp or 0.0,
        grid_names=grid_names,
        discard_channels=discard_channels,
        coordinates=coordinates,
        ied=ied,
        aux_data=aux_data,
        aux_names=aux_names,
        loader_meta=_bids_meta(meta),
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
    data = get_case_insensitive(signal, "data")
    if not isinstance(data, np.ndarray) or data.size == 0 or data.ndim > 2:
        return None
    if data.dtype.kind not in "biuf":
        return None
    emg = copy_into(store, "emg", data.reshape(1, -1) if data.ndim == 1 else data)
    return _mat_signal_context(signal, mat, emg, store)


class DecompositionFileError(MueditError):
    """A file that cannot be read as a decomposition: corrupt, truncated, or missing its fields."""

    field = "path"


_UNREADABLE = (
    zipfile.BadZipFile,
    OSError,
    EOFError,
    KeyError,
    ValueError,
    pickle.UnpicklingError,
    scipy.io.matlab.MatReadError,
)


def load_decomposition(
    file_path: str,
    store: ArrayStore | None = None,
    *,
    with_signal: bool = True,
    binary_trains: bool = True,
) -> tuple[LoadedDecomposition, EditSignalContext | None]:
    """Read a .npz or .mat decomposition and its EMG context (into ``store``) in one pass."""
    store = store if store is not None else RamStore()
    ext = Path(file_path).suffix.lower()
    if ext not in (".npz", ".mat"):
        raise DecompositionFileError("Unsupported decomposition format. Expected .mat or .npz")
    ctx: EditSignalContext | None = None
    try:
        if ext == ".npz":
            with NpzArchive(file_path) as data:
                d = _npz_v2_decomp(data) if SCHEMA_KEY in data else _npz_v1_decomp(data)
                if with_signal:
                    ctx = _npz_signal_context(data, store, d.total_samples)
        elif h5py.is_hdf5(file_path):
            with h5py.File(file_path, "r") as h5f:
                d = _mat73_decomp(h5f)
                if with_signal:
                    ctx = _mat73_signal_context(h5f, store)
        else:
            mat = scipy.io.loadmat(file_path, simplify_cells=True)
            d = _mat5_decomp(mat)
            if with_signal:
                ctx = _mat5_signal_context(mat, store)
        loaded = _finish_decomposition(d, one_based=ext == ".mat", binary_trains=binary_trains)
    except _UNREADABLE as exc:
        raise DecompositionFileError(
            f"Cannot read {Path(file_path).name}: {str(exc) or 'the file is not a decomposition'}"
        ) from exc
    return loaded, ctx
