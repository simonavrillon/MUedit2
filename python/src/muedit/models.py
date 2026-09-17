"""Data models for the MUedit decomposition pipeline."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, TypeAlias, cast

import numpy as np
from numpy.typing import NDArray

# Named array types. They fix the element type only; NumPy annotations cannot
# express shapes, so parameter names and docstrings still say what the axes are.

FloatArray: TypeAlias = NDArray[np.floating[Any]]
"""Real-valued data: EMG samples, pulse trains, separation filters, whitening matrices."""

IntArray: TypeAlias = NDArray[np.integer[Any]]
"""Integer data: sample indices (discharge times, peaks), spike rasters, 0/1 discard flags."""

BoolArray: TypeAlias = NDArray[np.bool_]
"""Boolean masks: artifact samples, bad channels."""


def _as_2d_float_array(value: Any) -> FloatArray:
    """Cast value to a 2-D float64 NumPy array, reshaping 1-D input to (1, n)."""
    arr = np.asarray(value, dtype=float)
    if arr.ndim == 1:
        arr = arr.reshape(1, -1)
    if arr.ndim == 0:
        return np.zeros((0, 0), dtype=float)
    return arr


def _as_name_list(value: str | Iterable[Any] | None) -> list[str]:
    """Normalize a name or sequence of names to a list of strings."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(x) for x in value]


def _ensure_channel_matrix(value: Any, n_samples: int) -> FloatArray:
    """Return a (n_channels, n_samples) float matrix, zero-padding or truncating as needed."""
    if value is None:
        return np.zeros((0, n_samples), dtype=float)
    arr = _as_2d_float_array(value)
    if arr.size == 0:
        return np.zeros((0, n_samples), dtype=float)
    if arr.shape[1] == n_samples:
        return arr
    if arr.shape[1] > n_samples:
        return arr[:, :n_samples]
    pad = np.zeros((arr.shape[0], n_samples - arr.shape[1]), dtype=float)
    return np.hstack([arr, pad])


@dataclass
class SignalImport:
    """Raw EMG signal and associated metadata as loaded from a recording file."""

    data: FloatArray
    fsamp: float
    gridname: list[str] = field(default_factory=list)
    muscle: list[str] = field(default_factory=list)
    auxiliary: FloatArray = field(default_factory=lambda: np.zeros((0, 0), dtype=float))
    auxiliaryname: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def build(
        cls,
        *,
        data: Any,
        fsamp: Any = 0.0,
        gridname: str | Iterable[Any] | None = None,
        muscle: str | Iterable[Any] | None = None,
        auxiliary: Any = None,
        auxiliaryname: str | Iterable[Any] | None = None,
        metadata: Any = None,
    ) -> SignalImport:
        """Construct from raw loader values, coercing types and filling defaults.

        ``data`` becomes a 2-D float array (a 1-D input is one channel), a missing
        ``fsamp`` becomes 0.0, a single name becomes a one-item list, and
        ``auxiliary`` is padded or truncated to the EMG sample count.
        """
        data_arr = _as_2d_float_array(data)
        n_samples = int(data_arr.shape[1]) if data_arr.ndim == 2 else 0
        return cls(
            data=data_arr,
            fsamp=float(fsamp) if fsamp is not None else 0.0,
            gridname=_as_name_list(gridname),
            muscle=_as_name_list(muscle),
            auxiliary=_ensure_channel_matrix(auxiliary, n_samples),
            auxiliaryname=_as_name_list(auxiliaryname),
            metadata=dict(cast(dict[str, Any], metadata)) if isinstance(metadata, dict) else {},
        )

    @classmethod
    def from_mapping(cls, payload: dict[str, Any]) -> SignalImport:
        """Construct from a loader dictionary; see :meth:`build` for the coercion rules."""
        return cls.build(
            data=payload.get("data", np.zeros((0, 0), dtype=float)),
            fsamp=payload.get("fsamp", 0.0),
            gridname=payload.get("gridname"),
            muscle=payload.get("muscle"),
            auxiliary=payload.get("auxiliary"),
            auxiliaryname=payload.get("auxiliaryname"),
            metadata=payload.get("metadata"),
        )

    def clone(self) -> SignalImport:
        """Return a copy with independent arrays, lists and top-level metadata."""
        return SignalImport(
            data=self.data.copy(),
            fsamp=self.fsamp,
            gridname=list(self.gridname),
            muscle=list(self.muscle),
            auxiliary=self.auxiliary.copy(),
            auxiliaryname=list(self.auxiliaryname),
            metadata=dict(self.metadata),
        )

    @property
    def nbytes(self) -> int:
        """Resident size of the EMG and auxiliary arrays."""
        return int(self.data.nbytes + self.auxiliary.nbytes)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary suitable for JSON or cache storage."""
        return {
            "data": self.data.copy(),
            "fsamp": float(self.fsamp),
            "gridname": list(self.gridname),
            "muscle": list(self.muscle),
            "auxiliary": self.auxiliary.copy(),
            "auxiliaryname": list(self.auxiliaryname),
            "metadata": dict(self.metadata),
        }


def _optional_nbytes(value: np.ndarray | None) -> int:
    return int(value.nbytes) if value is not None else 0


@dataclass
class EditSignalContext:
    """Raw EMG embedded in a decomposition file, kept for editing and BIDS export.

    Built by ``load_decomposition_signal_context`` and held in the API edit cache.
    """

    data: FloatArray  # (n_channels, n_samples); (0, 0) when the file holds only a mask
    fsamp: float  # 0.0 when the file does not record it
    grid_names: list[str] = field(default_factory=list)
    emgmask: list[IntArray] = field(default_factory=list)  # per grid, 1 = discarded channel
    coordinates: list[FloatArray] = field(default_factory=list)  # per grid, (n_channels, 2)
    ied: list[float] | None = None  # per-grid inter-electrode distance, mm
    aux_data: FloatArray | None = None  # (n_aux, n_samples)
    aux_names: list[str] = field(default_factory=list)
    artifact_mask: BoolArray | None = None  # (n_samples,)
    # Loader BIDS fields (decomposition_file.LOADER_BIDS_META_KEYS) the file recorded.
    loader_meta: dict[str, Any] = field(default_factory=dict)

    def compact_copy(self) -> EditSignalContext:
        """Return an independent copy with EMG and auxiliary data as float32.

        Empty auxiliary data and artifact masks become ``None``.
        """
        aux = self.aux_data
        mask = self.artifact_mask
        return EditSignalContext(
            data=(
                np.array(self.data, dtype=np.float32)
                if self.data.size
                else np.zeros((0, 0), dtype=np.float32)
            ),
            fsamp=float(self.fsamp),
            grid_names=list(self.grid_names),
            emgmask=[np.array(m, dtype=int) for m in self.emgmask],
            coordinates=[np.array(c, dtype=float) for c in self.coordinates],
            ied=list(self.ied) if self.ied is not None else None,
            aux_data=np.array(aux, dtype=np.float32) if aux is not None and aux.size else None,
            aux_names=list(self.aux_names),
            artifact_mask=np.array(mask, dtype=bool) if mask is not None and mask.size else None,
            loader_meta=dict(self.loader_meta),
        )

    @property
    def nbytes(self) -> int:
        """Resident size of all arrays in the context."""
        return (
            int(self.data.nbytes)
            + _optional_nbytes(self.aux_data)
            + _optional_nbytes(self.artifact_mask)
            + sum(int(m.nbytes) for m in self.emgmask)
            + sum(int(c.nbytes) for c in self.coordinates)
        )


@dataclass
class LoadedDecomposition:
    """Decomposition state loaded from a .npz or .mat file for the interactive editing stage."""

    pulse_trains_full: list[list[float]] = field(default_factory=list)
    distime_all: list[list[int]] = field(default_factory=list)
    fsamp: float | None = None
    grid_names: list[str] = field(default_factory=list)
    total_samples: int = 0
    mu_grid_index: list[int] = field(default_factory=list)
    rois: list[tuple[int, int]] = field(default_factory=list)
    parameters: dict[str, Any] = field(default_factory=dict)
    muscle: list[str] = field(default_factory=list)
    sil: list[float] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe dictionary."""
        return {
            "pulse_trains_full": self.pulse_trains_full,
            "distime_all": self.distime_all,
            "fsamp": self.fsamp,
            "grid_names": list(self.grid_names),
            "total_samples": int(self.total_samples),
            "mu_grid_index": [int(x) for x in self.mu_grid_index],
            "rois": [(int(s), int(e)) for s, e in self.rois],
            "parameters": dict(self.parameters),
            "muscle": list(self.muscle),
            "sil": [float(x) for x in self.sil],
        }


@dataclass
class DecompositionSignalExport:
    """EMG signal paired with its decomposition output."""

    data: FloatArray
    fsamp: float
    pulse_t: FloatArray
    discharge_times: list[IntArray]

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a dictionary using MATLAB-compatible key names."""
        n_mu = len(self.discharge_times)
        distime_obj = np.empty(n_mu, dtype=object)
        for i, d in enumerate(self.discharge_times):
            distime_obj[i] = np.asarray(d)
        return {
            "data": self.data,
            "fsamp": float(self.fsamp),
            "PulseT": self.pulse_t,
            "Dischargetimes": distime_obj,
        }


@dataclass
class DecompositionExport:
    """Full decomposition output including per-grid SIL scores and a frontend preview payload."""

    signal: DecompositionSignalExport
    parameters: dict[str, Any]
    grid_names: list[str]
    sil: list[float]
    discard_channels: list[IntArray]
    coordinates: list[FloatArray]
    mu_grid_index: list[int]
    preview: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe dictionary."""
        return {
            "signal": self.signal.to_dict(),
            "parameters": dict(self.parameters),
            "grid_names": list(self.grid_names),
            "sil": self.sil,
            "discard_channels": self.discard_channels,
            "coordinates": self.coordinates,
            "mu_grid_index": [int(x) for x in self.mu_grid_index],
            "preview": self.preview,
        }
