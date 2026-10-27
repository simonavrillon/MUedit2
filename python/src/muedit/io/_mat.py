"""MAT file loaders (.mat v5 and v7.3 HDF5) for MUedit."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import replace
from typing import Any

import h5py
import numpy as np
import scipy.io

from muedit.errors import SignalLoadError
from muedit.models import SignalImport
from muedit.storage import ArrayStore, RamStore, sample_blocks, store_signal


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
            if flat.size > 0 and np.max(flat) < 256:
                try:
                    return "".join(chr(int(c)) for c in flat if int(c) != 0).strip()
                except ValueError:
                    pass  # negative codes: not a char array, try the wide encodings
            # errors="ignore" means these decodes cannot raise.
            if value.dtype == np.uint16:
                return flat.tobytes().decode("utf-16-le", errors="ignore").strip("\x00").strip()
            if value.dtype == np.uint32:
                return flat.tobytes().decode("utf-32-le", errors="ignore").strip("\x00").strip()
            return ""
    return str(value).strip()


def parse_text_list(value: Any) -> list[str]:
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
                out.extend(parse_text_list(item))
            return out
        if value.dtype.kind in {"S", "U", "i", "u"}:
            text = _parse_text(value)
            return [text] if text else []
        return [t for t in (_parse_text(item) for item in value.flatten().tolist()) if t]
    if isinstance(value, (list, tuple)):
        items: list[str] = []
        for item in value:
            items.extend(parse_text_list(item))
        return items
    text = _parse_text(value)
    return [text] if text else []


def parse_name_list(value: Any) -> list[str]:
    """``parse_text_list`` stripped, without blank names: muscles, grids, labels from any source."""
    return [name for name in (text.strip() for text in parse_text_list(value)) if name]


def _parse_numeric_array(value: Any, *, default_cols: int = 0) -> np.ndarray:
    if value is None:
        return np.zeros((0, default_cols), dtype=float)
    arr = np.asarray(value, dtype=float)
    if arr.ndim == 0:
        return np.zeros((0, default_cols), dtype=float)
    if arr.ndim == 1:
        return arr.reshape(1, -1)
    return arr


def mat73_read(node: h5py.Dataset | h5py.Group, h5file: h5py.File) -> Any:
    if isinstance(node, h5py.Group):
        return {key: mat73_read(node[key], h5file) for key in node}

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
                values.append(mat73_read(h5file[ref], h5file))
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


#: The fields a MUedit decomposition adds to ``signal``.
_DECOMPOSITION_FIELDS = {"Pulsetrain", "Dischargetimes"}


def _check_not_decomposition(field_names: Iterable[str]) -> None:
    if set(field_names) >= _DECOMPOSITION_FIELDS:
        raise SignalLoadError(
            "MAT file is a MUedit decomposition (signal.Pulsetrain + signal.Dischargetimes); "
            "open it in edit mode."
        )


def _signal_import(data: Any, aux_data: Any, read_field: Callable[[str], Any]) -> SignalImport:
    """The ``SignalImport`` of a MATLAB ``signal`` struct whose other fields ``read_field`` returns."""
    try:
        fsamp = float(np.asarray(read_field("fsamp")).squeeze())
    except (TypeError, ValueError):
        fsamp = 0.0
    return SignalImport.build(
        data=data,
        fsamp=fsamp,
        grid_names=parse_text_list(read_field("gridname")),
        muscle_names=parse_text_list(read_field("muscle")),
        aux_data=aux_data,
        aux_names=parse_text_list(read_field("auxiliaryname")),
        metadata={
            "device_name": _parse_text(read_field("device_name")) or None,
            "software_versions": "MATLAB",
            "units": "mV",
            "recording_type": "continuous",
            "software_filters": "n/a",
        },
    )


def _read_mat73_emg(node: h5py.Dataset, store: ArrayStore) -> np.ndarray:
    """A numeric ``(samples, channels)`` v7.3 dataset as ``(channels, samples)`` float32, by slices."""
    n_samples, n_channels = node.shape
    data = store.allocate("emg", (n_channels, n_samples), np.float32)
    for start, stop in sample_blocks(n_samples, n_channels):
        data[:, start:stop] = node[start:stop, :].T
    return store.seal(data)


def _load_mat73(file_path: str, store: ArrayStore) -> SignalImport:
    """A v7.3 (HDF5) ``signal`` struct; numeric EMG is read in slices straight into ``store``."""
    with h5py.File(file_path, "r") as h5f:
        group = h5f.get("signal")
        if group is None:
            raise SignalLoadError("Key 'signal' not found in MAT file.")
        if not isinstance(group, h5py.Group):
            raise SignalLoadError("MAT v7.3 field 'signal' is not a struct group.")
        _check_not_decomposition(group.keys())

        def read_field(name: str) -> Any:
            return mat73_read(group[name], h5f) if name in group else None

        # MATLAB v7.3 (HDF5) stores 2D arrays transposed (column-major).
        # scipy.io.loadmat (v5) handles this internally; h5py does not.
        node = group.get("data")
        in_store = isinstance(node, h5py.Dataset) and node.ndim == 2 and node.dtype.kind in "fiu"
        data = (
            _read_mat73_emg(node, store) if in_store else _parse_numeric_array(read_field("data")).T
        )
        n_samples = data.shape[1]
        aux_data = _align_to_n_samples(
            _parse_numeric_array(read_field("auxiliary"), default_cols=n_samples), n_samples
        )
        signal = _signal_import(data, aux_data, read_field)
    if not in_store:
        return store_signal(signal, store)
    aux = store.allocate("aux", signal.aux_data.shape, np.float32)
    aux[...] = signal.aux_data
    return replace(signal, aux_data=store.seal(aux))


def _load_mat5(file_path: str, store: ArrayStore) -> SignalImport:
    """A v5 ``signal`` struct, read whole by scipy, then copied into ``store``."""
    try:
        mat = scipy.io.loadmat(file_path, struct_as_record=False, squeeze_me=True)
    except (OSError, ValueError, NotImplementedError, scipy.io.matlab.MatReadError) as exc:
        raise SignalLoadError(f"Failed to load MAT file: {exc}") from exc
    struct = mat.pop("signal", None)
    if struct is None:
        raise SignalLoadError("Key 'signal' not found in MAT file.")
    _check_not_decomposition(getattr(struct, "_fieldnames", None) or [])

    def read_field(name: str) -> Any:
        return getattr(struct, name, None)

    signal = _signal_import(read_field("data"), read_field("auxiliary"), read_field)
    del mat
    return store_signal(signal, store)


def load_mat(file_path: str, store: ArrayStore | None = None) -> SignalImport:
    """Load a MATLAB ``signal`` struct (v5 or v7.3) as a ``SignalImport``, written into ``store``."""
    store = store if store is not None else RamStore()
    if not h5py.is_hdf5(file_path):
        return _load_mat5(file_path, store)
    try:
        return _load_mat73(file_path, store)
    except OSError as exc:  # h5py's error for a corrupt or truncated file
        raise SignalLoadError(f"Failed to load MAT v7.3 file: {exc}") from exc
