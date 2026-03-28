"""Decomposition I/O normalization helpers and serialization utilities."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import h5py
import numpy as np
import scipy.io

from muedit.io.factory import LoaderFactory
from muedit.models import LoadedDecomposition, SignalImport

DecompositionLoadTuple = tuple[
    Any,
    Any,
    float | None,
    int | None,
    list[str],
    list[int],
    dict[str, Any],
    list[tuple[int, int]],
    list[str],
]


def load_signal(filepath: str) -> dict[str, Any]:
    """Load a raw signal file and normalize it to the internal mapping shape."""
    return LoaderFactory.load_signal(filepath).to_dict()


def clone_signal(signal: dict[str, Any]) -> dict[str, Any]:
    """Deep-copy a signal mapping through validated model serialization."""
    return SignalImport.from_mapping(signal).clone().to_dict()


def normalize_distimes(raw: Any) -> list[list[int]]:
    """Normalize discharge-time payloads into ``list[list[int]]`` format."""
    def _is_scalar_number(value: Any) -> bool:
        return isinstance(value, (int, float, np.integer, np.floating))

    def _collect_positive_ints(value: Any) -> list[int]:
        if value is None:
            return []
        out: list[int]
        if isinstance(value, np.ndarray):
            if value.dtype == object:
                out = []
                for item in value.flatten().tolist():
                    out.extend(_collect_positive_ints(item))
                return out
            try:
                flat = value.astype(int).flatten().tolist()
            except (TypeError, ValueError):
                out = []
                for item in value.flatten().tolist():
                    out.extend(_collect_positive_ints(item))
                return out
            return [int(v) for v in flat if int(v) >= 0]
        if isinstance(value, (list, tuple)):
            out = []
            for item in value:
                out.extend(_collect_positive_ints(item))
            return out
        try:
            val = int(value)
        except (TypeError, ValueError):
            return []
        return [val] if val >= 0 else []

    if raw is None:
        return []
    if isinstance(raw, np.ndarray):
        if raw.dtype == object:
            flat_items = raw.flatten().tolist()
            if raw.ndim == 1 and flat_items and all(_is_scalar_number(x) for x in flat_items):
                vals = [int(v) for v in flat_items if int(v) >= 0]
                return [sorted(set(vals))]
            return normalize_distimes(raw.tolist())
        arr = raw.astype(int)
        if arr.ndim <= 1:
            return [[int(v) for v in arr if v >= 0]]
        return [[int(v) for v in row if v >= 0] for row in arr]
    if isinstance(raw, (list, tuple)):
        if raw and all(_is_scalar_number(x) for x in raw):
            vals = [int(v) for v in raw if int(v) >= 0]
            return [sorted(set(vals))]
        result: list[list[int]] = []
        for item in raw:
            if item is None:
                result.append([])
            else:
                result.append(sorted(set(_collect_positive_ints(item))))
        return result
    return []


def first_non_none(*values: Any) -> Any:
    """Return the first argument that is not ``None``."""
    for value in values:
        if value is not None:
            return value
    return None


def build_pulse_trains_from_distimes(
    distimes: list[list[int]], total_samples: int
) -> np.ndarray:
    """Create a binary pulse-train matrix from discharge-time indices."""
    nmu = len(distimes)
    pulses = np.zeros((nmu, total_samples), dtype=float)
    for idx, times in enumerate(distimes):
        if not times:
            continue
        t = np.asarray(times, dtype=int)
        t = t[(t >= 0) & (t < total_samples)]
        pulses[idx, t] = 1.0
    return pulses


def _parse_grid_names(gnames: Any) -> list[str]:
    """Convert grid-name payloads from various storage formats to strings."""
    return _parse_text_list(gnames)


def _parse_mu_grid_index(raw: Any) -> list[int]:
    """Normalize MU-to-grid assignment payloads to a flat integer list."""
    if raw is None:
        return []
    return [int(x) for x in np.array(raw).flatten().tolist()]


def _parse_muscles(raw: Any) -> list[str]:
    """Normalize muscle metadata to a non-empty list of labels."""
    return _parse_text_list(raw)


def _parse_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="ignore")
    if isinstance(value, str):
        return value
    if isinstance(value, np.ndarray):
        if value.size == 0:
            return ""
        if value.dtype.kind in {"S", "U"}:
            return "".join(str(x) for x in value.flatten().tolist()).strip()
        if np.issubdtype(value.dtype, np.integer):
            return "".join(chr(int(c)) for c in value.flatten().tolist() if int(c) != 0).strip()
    return str(value).strip()


def _parse_text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (str, bytes)):
        text = _parse_text(value)
        return [text] if text else []
    if isinstance(value, np.ndarray):
        if value.ndim == 0:
            text = _parse_text(value.item())
            return [text] if text else []
        if value.dtype == object:
            out: list[str] = []
            for item in value.flatten().tolist():
                out.extend(_parse_text_list(item))
            return out
        if value.dtype.kind in {"S", "U", "i", "u"}:
            text = _parse_text(value)
            return [text] if text else []
        return [t for t in (_parse_text(item) for item in value.flatten().tolist()) if t]
    if isinstance(value, (list, tuple)):
        out: list[str] = []
        for item in value:
            out.extend(_parse_text_list(item))
        return out
    text = _parse_text(value)
    return [text] if text else []


def _mat73_read(node: h5py.Dataset | h5py.Group, h5file: h5py.File) -> Any:
    if isinstance(node, h5py.Group):
        return {key: _mat73_read(node[key], h5file) for key in node.keys()}

    raw = node[()]
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="ignore")
    if np.isscalar(raw):
        if isinstance(raw, np.bytes_):
            return raw.tobytes().decode("utf-8", errors="ignore")
        return raw.item() if hasattr(raw, "item") else raw

    arr = np.asarray(raw)
    if arr.dtype.kind == "O":
        refs = arr.flatten().tolist()
        values: list[Any] = []
        for ref in refs:
            if isinstance(ref, h5py.Reference) and ref:
                values.append(_mat73_read(h5file[ref], h5file))
            else:
                values.append(ref)
        if arr.size == 1:
            return values[0]
        obj = np.empty((arr.size,), dtype=object)
        obj[:] = values
        return obj.reshape(arr.shape)
    return arr


def _get_case_insensitive(mapping: dict[str, Any], *names: str) -> Any:
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
    if not isinstance(pulse, np.ndarray):
        return pulse
    if pulse.ndim != 2:
        return pulse
    if pulse.shape[0] > pulse.shape[1]:
        return pulse.T
    return pulse


def _load_mat73_decomp(filepath: str) -> DecompositionLoadTuple:
    with h5py.File(filepath, "r") as h5f:
        def read_root(name: str) -> Any:
            return _mat73_read(h5f[name], h5f) if name in h5f else None

        signal = read_root("signal")
        if not isinstance(signal, dict):
            signal = {}

        preview_block = read_root("preview")
        if not isinstance(preview_block, dict):
            preview_block = {}

        pulse_trains = _get_case_insensitive(signal, "Pulsetrain")
        if pulse_trains is None:
            pulse_trains = np.array([])
        pulse_trains = _normalize_pulse_matrix(pulse_trains)

        distime_raw = _get_case_insensitive(signal, "Dischargetimes")

        fsamp_val = _get_case_insensitive(signal, "fsamp")
        fsamp = float(fsamp_val) if fsamp_val is not None else None

        data_block = _get_case_insensitive(signal, "data")
        total_samples = _infer_total_samples_from_pulse(pulse_trains)
        if total_samples is None and isinstance(data_block, np.ndarray):
            data_norm = data_block.T if data_block.ndim == 2 and data_block.shape[0] > data_block.shape[1] else data_block
            total_samples = int(data_norm.shape[1]) if data_norm.ndim == 2 else None

        rois_raw = _get_case_insensitive(preview_block, "rois")
        rois: list[tuple[int, int]] = []
        if rois_raw is not None:
            rois = [
                (int(r[0]), int(r[1])) if isinstance(r, (list, tuple)) else r
                for r in rois_raw
            ]

        gnames = first_non_none(
            read_root("grid_names"),
            _get_case_insensitive(signal, "gridname"),
        )
        grid_names = _parse_grid_names(gnames) if gnames is not None else ["Grid 1"]

        mu_grid_index = _parse_mu_grid_index(read_root("mu_grid_index"))

        params_raw = read_root("parameters")
        if isinstance(params_raw, np.ndarray) and params_raw.dtype == object and params_raw.size == 1:
            params_raw = params_raw.item()
        parameters = params_raw if isinstance(params_raw, dict) else {}

        muscles = _parse_muscles(_get_case_insensitive(signal, "muscle"))
        if not muscles:
            muscles = _parse_muscles(
                parameters.get("target_muscle") if isinstance(parameters, dict) else None
            )

        return (
            pulse_trains,
            distime_raw,
            fsamp,
            total_samples,
            grid_names,
            mu_grid_index,
            parameters,
            rois,
            muscles,
        )


def _load_npz_decomp(filepath: str) -> DecompositionLoadTuple:
    """Load decomposition artifacts saved in MUedit NPZ format."""
    data = np.load(filepath, allow_pickle=True)
    pulse_trains = data.get("pulse_trains", np.array([]))
    distime_raw = first_non_none(data.get("discharge_times"), data.get("distime"))
    fsamp_val = data.get("fsamp")
    fsamp = float(fsamp_val) if fsamp_val is not None else None
    total_samples = int(pulse_trains.shape[1]) if hasattr(pulse_trains, "shape") else None

    gnames = first_non_none(data.get("grid_names"), data.get("grid_name"))
    grid_names = _parse_grid_names(gnames) if gnames is not None else []
    mu_grid_index = _parse_mu_grid_index(data.get("mu_grid_index"))

    parameters_raw = data.get("parameters")
    if (
        isinstance(parameters_raw, np.ndarray)
        and parameters_raw.dtype == object
        and parameters_raw.size == 1
    ):
        parameters_raw = parameters_raw.item()
    parameters = parameters_raw if isinstance(parameters_raw, dict) else {}
    muscles = _parse_muscles(
        first_non_none(
            data.get("muscle_names"), data.get("muscle"), data.get("target_muscle")
        )
    )
    if not muscles:
        muscles = _parse_muscles(
            parameters.get("target_muscle") if isinstance(parameters, dict) else None
        )

    return (
        pulse_trains,
        distime_raw,
        fsamp,
        total_samples,
        grid_names,
        mu_grid_index,
        parameters,
        [],
        muscles,
    )


def _load_mat_decomp(filepath: str) -> DecompositionLoadTuple:
    """Load decomposition artifacts from MATLAB MAT structures."""
    try:
        mat = scipy.io.loadmat(filepath, simplify_cells=True)
    except NotImplementedError:
        if h5py.is_hdf5(filepath):
            return _load_mat73_decomp(filepath)
        raise
    except ValueError:
        if h5py.is_hdf5(filepath):
            return _load_mat73_decomp(filepath)
        raise
    except TypeError:
        mat = scipy.io.loadmat(filepath)

    signal = mat.get("signal")
    if signal is None:
        signal = {}
    if not isinstance(signal, dict):
        signal = {}

    preview_block = mat.get("preview")
    if not isinstance(preview_block, dict):
        preview_block = {}

    pulse_trains = _get_case_insensitive(signal, "Pulsetrain")
    if pulse_trains is None:
        pulse_trains = np.array([])
    pulse_trains = _normalize_pulse_matrix(pulse_trains)

    distime_raw = _get_case_insensitive(signal, "Dischargetimes")

    fsamp_val = _get_case_insensitive(signal, "fsamp")
    fsamp = float(fsamp_val) if fsamp_val is not None else None

    data_block = _get_case_insensitive(signal, "data")
    total_samples = _infer_total_samples_from_pulse(pulse_trains)
    if total_samples is None and isinstance(data_block, np.ndarray):
        data_norm = data_block.T if data_block.ndim == 2 and data_block.shape[0] > data_block.shape[1] else data_block
        total_samples = int(data_norm.shape[1]) if data_norm.ndim == 2 else None

    rois_raw = _get_case_insensitive(preview_block, "rois")
    rois: list[tuple[int, int]] = []
    if rois_raw is not None:
        rois = [
            (int(r[0]), int(r[1])) if isinstance(r, (list, tuple)) else r
            for r in rois_raw
        ]

    gnames = first_non_none(mat.get("grid_names"), _get_case_insensitive(signal, "gridname"))
    grid_names = _parse_grid_names(gnames) if gnames is not None else ["Grid 1"]

    mu_grid_index = _parse_mu_grid_index(mat.get("mu_grid_index"))

    params_raw = mat.get("parameters")
    parameters = params_raw if isinstance(params_raw, dict) else {}

    muscles = _parse_muscles(_get_case_insensitive(signal, "muscle"))
    if not muscles:
        muscles = _parse_muscles(
            parameters.get("target_muscle") if isinstance(parameters, dict) else None
        )

    return (
        pulse_trains,
        distime_raw,
        fsamp,
        total_samples,
        grid_names,
        mu_grid_index,
        parameters,
        rois,
        muscles,
    )


def _coerce_pulse_matrix(value: Any) -> np.ndarray | None:
    if isinstance(value, np.ndarray):
        if value.dtype == object:
            return None
        arr = np.asarray(value, dtype=float)
        if arr.ndim == 1:
            arr = arr.reshape(1, -1)
        if arr.ndim != 2:
            return None
        return _normalize_pulse_matrix(arr)
    if isinstance(value, (list, tuple)):
        try:
            arr = np.asarray(value, dtype=float)
        except (TypeError, ValueError):
            return None
        if arr.ndim == 1:
            arr = arr.reshape(1, -1)
        if arr.ndim != 2:
            return None
        return _normalize_pulse_matrix(arr)
    return None


def _extract_grid_pulse_blocks(raw: Any) -> list[np.ndarray]:
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


def _extract_grid_distime_blocks(raw: Any, expected_grids: int | None = None) -> list[list[list[int]]]:
    # MATLAB schema target:
    #   Dischargetimes: G x nMU cell array
    # where each row is one grid and each cell is one MU spike-index vector.
    def _parse_cell_mu_vector(cell: Any) -> list[int]:
        if cell is None:
            return []
        out: list[int]
        if isinstance(cell, np.ndarray):
            if cell.dtype == object:
                out = []
                for item in cell.flatten().tolist():
                    out.extend(_parse_cell_mu_vector(item))
                return sorted(set(v for v in out if v >= 0))
            try:
                vals = cell.astype(int).flatten().tolist()
            except (TypeError, ValueError):
                out = []
                for item in cell.flatten().tolist():
                    out.extend(_parse_cell_mu_vector(item))
                return sorted(set(v for v in out if v >= 0))
            return sorted(set(int(v) for v in vals if int(v) >= 0))
        if isinstance(cell, (list, tuple)):
            out = []
            for item in cell:
                out.extend(_parse_cell_mu_vector(item))
            return sorted(set(v for v in out if v >= 0))
        try:
            v = int(cell)
        except (TypeError, ValueError):
            return []
        return [v] if v >= 0 else []

    # Handle ngrid=1: single-grid payloads are commonly represented as a flat
    # list/object-array of MU vectors (MAT simplify_cells and NPZ object arrays).
    if expected_grids == 1 and isinstance(raw, (list, tuple, np.ndarray)):
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
                    mu_vec = _parse_cell_mu_vector(cell)
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
    if isinstance(raw, np.ndarray) and raw.dtype == object:
        squeezed = np.squeeze(raw)
        if isinstance(squeezed, np.ndarray):
            if squeezed.ndim == 0:
                return [squeezed.item()]
            if squeezed.ndim == 1:
                return squeezed.tolist()
            # Fallback: keep first axis grouping.
            return [row for row in squeezed]
        return [squeezed]
    if isinstance(raw, (list, tuple)):
        return list(raw)
    return []


def _unpack_gridwise_decomposition(
    pulse_trains: Any,
    distime_raw: Any,
    mu_grid_index: list[int],
) -> tuple[Any, Any, list[int]]:
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
            # Align discharge-time ordering to pulse-train MU ordering per grid.
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

    # Preserve explicit MU-to-grid assignment when provided by the file.
    # Only infer when it is absent.
    if not mu_grid_index:
        if inferred_from_pulse:
            mu_grid_index = inferred_from_pulse
        elif inferred_from_distime:
            mu_grid_index = inferred_from_distime

    return pulse_trains, distime_raw, mu_grid_index


def _infer_total_samples_from_pulse(pulse_trains: Any) -> int | None:
    matrix = _coerce_pulse_matrix(pulse_trains)
    if matrix is not None and matrix.ndim == 2 and matrix.size > 0:
        return int(matrix.shape[1])
    blocks = _extract_grid_pulse_blocks(pulse_trains)
    if blocks:
        return int(blocks[0].shape[1])
    return None


def load_decomposition_file(filepath: str) -> dict[str, Any]:
    """Load a decomposition file (.npz or .mat) into API-ready normalized output."""
    ext = Path(filepath).suffix.lower()

    if ext == ".npz":
        (
            pulse_trains,
            distime_raw,
            fsamp,
            total_samples,
            grid_names,
            mu_grid_index,
            parameters,
            rois,
            muscles,
        ) = _load_npz_decomp(filepath)
    elif ext == ".mat":
        (
            pulse_trains,
            distime_raw,
            fsamp,
            total_samples,
            grid_names,
            mu_grid_index,
            parameters,
            rois,
            muscles,
        ) = _load_mat_decomp(filepath)
    else:
        raise ValueError("Unsupported decomposition format. Expected .mat or .npz")

    pulse_trains, distime_raw, mu_grid_index = _unpack_gridwise_decomposition(
        pulse_trains,
        distime_raw,
        mu_grid_index,
    )

    distimes = normalize_distimes(distime_raw)

    if not grid_names:
        grid_names = [
            f"Grid {i + 1}" for i in range(max(mu_grid_index) + 1 if mu_grid_index else 1)
        ]
    if mu_grid_index and len(mu_grid_index) != len(distimes):
        mu_grid_index = [0] * len(distimes)

    total_samples = int(
        total_samples or (pulse_trains.shape[1] if hasattr(pulse_trains, "shape") else 0)
    )
    if total_samples <= 0 and distimes:
        max_spike = max((max(d) for d in distimes if d), default=-1)
        total_samples = max_spike + 1 if max_spike >= 0 else 0

    pulse_matrix_candidate = _coerce_pulse_matrix(pulse_trains)
    has_pulse_matrix = (
        pulse_matrix_candidate is not None
        and int(pulse_matrix_candidate.size) > 0
        and int(pulse_matrix_candidate.shape[1]) == int(total_samples)
    )

    def _distimes_from_pulse_matrix(matrix: np.ndarray) -> list[list[int]]:
        return [np.flatnonzero(np.asarray(row) != 0).astype(int).tolist() for row in matrix]

    def _shift_distimes(values: list[list[int]], shift: int, limit: int) -> list[list[int]]:
        shifted: list[list[int]] = []
        for row in values:
            adj = [int(v) + shift for v in row]
            adj = [v for v in adj if 0 <= v < limit]
            shifted.append(sorted(set(adj)))
        return shifted

    if has_pulse_matrix:
        pulse_matrix = np.asarray(pulse_matrix_candidate, dtype=float)
        if not distimes:
            distimes = _distimes_from_pulse_matrix(pulse_matrix)
    else:
        pulse_matrix = build_pulse_trains_from_distimes(distimes, int(total_samples))

    # MATLAB decomposition exports are 1-based by convention.
    # Convert to 0-based for internal indexing while preserving MU order.
    if ext == ".mat" and distimes:
        has_zero_index = any(any(int(v) == 0 for v in row) for row in distimes)
        if not has_zero_index:
            distimes = _shift_distimes(distimes, -1, int(total_samples))

    if not mu_grid_index or len(mu_grid_index) != len(distimes):
        mu_grid_index = [0] * len(distimes)

    pulse_trains_full = [list(map(float, row)) for row in pulse_matrix.tolist()]

    norm_rois: list[tuple[int, int]] = []
    for item in rois:
        if isinstance(item, (list, tuple)) and len(item) == 2:
            norm_rois.append((int(item[0]), int(item[1])))

    loaded = LoadedDecomposition(
        pulse_trains_full=pulse_trains_full,
        distime_all=distimes,
        fsamp=fsamp,
        grid_names=grid_names,
        total_samples=int(total_samples or 0),
        mu_grid_index=mu_grid_index,
        rois=norm_rois,
        parameters=parameters,
        muscle=muscles,
    )
    return loaded.to_dict()


def _parse_emgmask_cells(raw: Any) -> list[np.ndarray]:
    items = _top_level_cell_items(raw)
    masks: list[np.ndarray] = []
    for item in items:
        if item is None:
            masks.append(np.array([], dtype=int))
            continue
        if isinstance(item, np.ndarray):
            if item.dtype == object:
                vals = normalize_distimes(item)
                flat = [int(v) for row in vals for v in row]
                masks.append(np.asarray(flat, dtype=int))
            else:
                try:
                    masks.append(np.asarray(item, dtype=int).flatten())
                except (TypeError, ValueError):
                    masks.append(np.array([], dtype=int))
            continue
        if isinstance(item, (list, tuple)):
            flat = normalize_distimes(item)
            masks.append(np.asarray([int(v) for row in flat for v in row], dtype=int))
            continue
        try:
            masks.append(np.asarray([int(item)], dtype=int))
        except (TypeError, ValueError):
            masks.append(np.array([], dtype=int))
    return masks


def load_decomposition_signal_context(filepath: str) -> dict[str, Any] | None:
    """Best-effort extraction of raw EMG context embedded in decomposition files."""
    ext = Path(filepath).suffix.lower()
    if ext != ".mat":
        return None

    signal: dict[str, Any] = {}
    top: dict[str, Any] = {}
    try:
        try:
            mat = scipy.io.loadmat(filepath, simplify_cells=True)
        except TypeError:
            mat = scipy.io.loadmat(filepath)
        top = mat if isinstance(mat, dict) else {}
        candidate = top.get("signal")
        signal = candidate if isinstance(candidate, dict) else {}
    except (NotImplementedError, ValueError):
        if not h5py.is_hdf5(filepath):
            return None
        with h5py.File(filepath, "r") as h5f:
            if "signal" in h5f:
                sig = _mat73_read(h5f["signal"], h5f)
                signal = sig if isinstance(sig, dict) else {}
            for key in ("fsamp", "grid_names", "EMGmask", "emgmask"):
                if key in h5f:
                    top[key] = _mat73_read(h5f[key], h5f)

    data = _get_case_insensitive(signal, "data")
    if not isinstance(data, np.ndarray) or data.size == 0:
        return None
    data_arr = np.asarray(data, dtype=float)
    if data_arr.ndim == 1:
        data_arr = data_arr.reshape(1, -1)
    if data_arr.ndim != 2:
        return None
    if data_arr.shape[0] > data_arr.shape[1]:
        data_arr = data_arr.T

    fsamp_val = first_non_none(
        _get_case_insensitive(signal, "fsamp"),
        top.get("fsamp"),
    )
    fsamp = float(fsamp_val) if fsamp_val is not None else None

    grid_names_raw = first_non_none(
        _get_case_insensitive(signal, "gridname"),
        top.get("grid_names"),
    )
    grid_names = _parse_grid_names(grid_names_raw)

    emgmask_raw = first_non_none(
        _get_case_insensitive(signal, "EMGmask", "emgmask"),
        top.get("EMGmask"),
        top.get("emgmask"),
    )
    emgmask = _parse_emgmask_cells(emgmask_raw)

    return {
        "data": data_arr,
        "fsamp": fsamp,
        "grid_names": grid_names,
        "emgmask": emgmask,
    }
