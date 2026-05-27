"""Data models for the MUedit decomposition pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, cast

import numpy as np


def _as_2d_float_array(value: Any) -> np.ndarray:
    """Cast value to a 2-D float64 NumPy array, reshaping 1-D input to (1, n)."""
    arr = np.asarray(value, dtype=float)
    if arr.ndim == 1:
        arr = arr.reshape(1, -1)
    if arr.ndim == 0:
        return np.zeros((0, 0), dtype=float)
    return arr


def _ensure_channel_matrix(value: Any, n_samples: int) -> np.ndarray:
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

    data: np.ndarray
    fsamp: float
    gridname: list[str] = field(default_factory=list)
    muscle: list[str] = field(default_factory=list)
    device_name: str | None = None
    auxiliary: np.ndarray = field(default_factory=lambda: np.zeros((0, 0), dtype=float))
    auxiliaryname: list[str] = field(default_factory=list)
    emgnotgrid: np.ndarray = field(default_factory=lambda: np.zeros((0, 0), dtype=float))
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, payload: dict[str, Any]) -> SignalImport:
        """Construct a SignalImport from a plain dictionary with type coercion and defaults."""
        data = _as_2d_float_array(payload.get("data", np.zeros((0, 0), dtype=float)))
        n_samples = int(data.shape[1]) if data.ndim == 2 else 0
        fsamp_raw = payload.get("fsamp", 0.0)
        fsamp = float(fsamp_raw) if fsamp_raw is not None else 0.0

        gridname = payload.get("gridname") or []
        muscle = payload.get("muscle") or []
        auxiliaryname = payload.get("auxiliaryname") or []
        if isinstance(gridname, str):
            gridname = [gridname]
        if isinstance(muscle, str):
            muscle = [muscle]
        if isinstance(auxiliaryname, str):
            auxiliaryname = [auxiliaryname]
        metadata_raw = payload.get("metadata")
        metadata = cast(dict[str, Any], metadata_raw) if isinstance(metadata_raw, dict) else {}

        return cls(
            data=data,
            fsamp=fsamp,
            gridname=[str(x) for x in list(gridname)],
            muscle=[str(x) for x in list(muscle)],
            device_name=payload.get("device_name"),
            auxiliary=_ensure_channel_matrix(payload.get("auxiliary"), n_samples),
            auxiliaryname=[str(x) for x in list(auxiliaryname)],
            emgnotgrid=_ensure_channel_matrix(payload.get("emgnotgrid"), n_samples),
            metadata=dict(metadata),
        )

    def clone(self) -> SignalImport:
        """Return a deep copy of this instance with independent NumPy array copies."""
        return SignalImport.from_mapping(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary suitable for JSON or cache storage."""
        return {
            "data": self.data.copy(),
            "fsamp": float(self.fsamp),
            "gridname": list(self.gridname),
            "muscle": list(self.muscle),
            "device_name": self.device_name,
            "auxiliary": self.auxiliary.copy(),
            "auxiliaryname": list(self.auxiliaryname),
            "emgnotgrid": self.emgnotgrid.copy(),
            "metadata": dict(self.metadata),
        }


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
        }


@dataclass
class DecompositionSignalExport:
    """EMG signal paired with its decomposition output."""

    data: np.ndarray
    fsamp: float
    pulse_t: np.ndarray
    discharge_times: list[np.ndarray]

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a dictionary using MATLAB-compatible key names."""
        return {
            "data": self.data,
            "fsamp": float(self.fsamp),
            "PulseT": self.pulse_t,
            "Dischargetimes": np.array(self.discharge_times, dtype=object),
        }


@dataclass
class DecompositionExport:
    """Full decomposition output including per-grid SIL scores and a frontend preview payload."""

    signal: DecompositionSignalExport
    parameters: dict[str, Any]
    grid_names: list[str]
    sil: dict[int, list[float]]
    discard_channels: list[np.ndarray]
    coordinates: list[np.ndarray]
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
