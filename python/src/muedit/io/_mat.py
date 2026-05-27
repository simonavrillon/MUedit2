"""MAT file loaders (.mat v5 and v7.3 HDF5) for MUedit."""

from __future__ import annotations

from typing import Any

import h5py
import numpy as np
import scipy.io


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
            flat = value.flatten()
            try:
                if flat.size > 0 and np.max(flat) < 256:
                    return "".join(chr(int(c)) for c in flat if int(c) != 0).strip()
            except Exception:
                pass
            try:
                if value.dtype == np.uint16:
                    return flat.tobytes().decode("utf-16-le", errors="ignore").strip("\x00").strip()
            except Exception:
                pass
            try:
                if value.dtype == np.uint32:
                    return flat.tobytes().decode("utf-32-le", errors="ignore").strip("\x00").strip()
            except Exception:
                pass
            return ""
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


def _parse_numeric_array(value: Any, *, default_cols: int = 0) -> np.ndarray:
    if value is None:
        return np.zeros((0, default_cols), dtype=float)
    arr = np.asarray(value, dtype=float)
    if arr.ndim == 0:
        return np.zeros((0, default_cols), dtype=float)
    if arr.ndim == 1:
        return arr.reshape(1, -1)
    return arr


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


def _align_to_n_samples(arr: np.ndarray, n_samples: int) -> np.ndarray:
    if arr.ndim == 2 and arr.size > 0 and arr.shape[1] != n_samples and arr.shape[0] == n_samples:
        return arr.T
    return arr


def _has_decomposition_markers(field_names: set[str]) -> bool:
    lower = {str(name).lower() for name in field_names}
    has_pulse = any(
        k in lower for k in {"pulset", "pulsetrain", "pulse_train", "pulse_t", "pulse_trains"}
    )
    has_distime = any(
        k in lower for k in {"dischargetimes", "discharge_times", "distime", "distimes"}
    )
    return has_pulse and has_distime


def _raise_if_decomposition_signal_fields(field_names: set[str]) -> None:
    if _has_decomposition_markers(field_names):
        raise ValueError(
            "MAT file contains decomposition fields (pulse trains + discharge times) "
            "inside 'signal'; load it through decomposition loader."
        )


def _load_mat73_signal(path: str) -> dict[str, Any]:
    with h5py.File(path, "r") as h5f:
        if "signal" not in h5f:
            raise ValueError("Key 'signal' not found in MAT file.")
        signal_group = h5f["signal"]
        if not isinstance(signal_group, h5py.Group):
            raise ValueError("MAT v7.3 field 'signal' is not a struct group.")
        _raise_if_decomposition_signal_fields(set(signal_group.keys()))

        def read_field(name: str, default: Any = None) -> Any:
            return _mat73_read(signal_group[name], h5f) if name in signal_group else default

        data = _parse_numeric_array(read_field("data"))
        if data.ndim == 2 and data.shape[0] > data.shape[1]:
            data = data.T
        n_samples = data.shape[1] if data.ndim == 2 else 0

        fsamp_raw = read_field("fsamp", 0.0)
        try:
            fsamp = float(np.asarray(fsamp_raw).squeeze())
        except (TypeError, ValueError):
            fsamp = 0.0

        auxiliary = _align_to_n_samples(
            _parse_numeric_array(read_field("auxiliary"), default_cols=n_samples),
            n_samples,
        )
        emgnotgrid = _align_to_n_samples(
            _parse_numeric_array(read_field("emgnotgrid"), default_cols=n_samples),
            n_samples,
        )

        return {
            "data": data,
            "fsamp": fsamp,
            "gridname": _parse_text_list(read_field("gridname")),
            "muscle": _parse_text_list(read_field("muscle")),
            "device_name": _parse_text(read_field("device_name")) or None,
            "auxiliary": auxiliary,
            "auxiliaryname": _parse_text_list(read_field("auxiliaryname")),
            "emgnotgrid": emgnotgrid,
        }


def load_mat(filepath):
    """Load legacy MATLAB signal struct into MUedit signal dictionary format."""
    try:
        mat = scipy.io.loadmat(filepath, struct_as_record=False, squeeze_me=True)
        if "signal" in mat:
            signal_struct = mat["signal"]
            signal = {}
            field_names = set()
            if hasattr(signal_struct, "_fieldnames"):
                field_names = set(getattr(signal_struct, "_fieldnames") or [])
            _raise_if_decomposition_signal_fields(field_names)

            def get_attr(obj, name, default=None):
                if hasattr(obj, name):
                    return getattr(obj, name)
                return default

            data = get_attr(signal_struct, "data")
            n_samples = data.shape[1] if data is not None else 0
            signal["data"] = data
            signal["fsamp"] = get_attr(signal_struct, "fsamp")
            signal["gridname"] = get_attr(signal_struct, "gridname") or []
            signal["muscle"] = get_attr(signal_struct, "muscle") or []
            signal["device_name"] = get_attr(signal_struct, "device_name", None)
            signal["auxiliary"] = get_attr(
                signal_struct, "auxiliary", np.zeros((0, n_samples))
            )
            signal["auxiliaryname"] = get_attr(signal_struct, "auxiliaryname", [])
            signal["emgnotgrid"] = get_attr(
                signal_struct, "emgnotgrid", np.zeros((0, n_samples))
            )

            return signal
        else:
            raise ValueError("Key 'signal' not found in MAT file.")
    except NotImplementedError as exc:
        if "matlab v7.3" not in str(exc).lower() and not h5py.is_hdf5(filepath):
            raise OSError(f"Failed to load MAT file: {exc}") from exc
        return _load_mat73_signal(filepath)
    except ValueError as exc:
        if h5py.is_hdf5(filepath):
            return _load_mat73_signal(filepath)
        raise OSError(f"Failed to load MAT file: {exc}") from exc
    except (OSError, TypeError, KeyError) as exc:
        raise OSError(f"Failed to load MAT file: {exc}") from exc
