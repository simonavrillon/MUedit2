"""Data models for the MUedit decomposition pipeline.

All public classes are plain Python dataclasses with ``to_dict`` / ``from_mapping``
helpers to facilitate JSON serialisation and cache cloning without pulling in a
heavier validation library.

Classes
-------
SignalImport
    Raw EMG signal and metadata as loaded from a recording file.
LoadedDecomposition
    Decomposition result loaded from a saved ``.npz`` or ``.mat`` file,
    used as the starting point for the interactive editing stage.
DecompositionSignalExport
    EMG data paired with its pulse trains and discharge times, ready for
    saving or BIDS export.
DecompositionExport
    Full decomposition output including per-grid SIL scores, electrode
    coordinates, and a lightweight preview payload for the frontend.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, cast

import numpy as np

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _as_2d_float_array(value: Any) -> np.ndarray:
    """Cast *value* to a 2-D float64 NumPy array.

    A 1-D input is reshaped to ``(1, n)``.  A 0-D (scalar) input returns an
    empty ``(0, 0)`` array.

    Args:
        value: Any value acceptable by :func:`numpy.asarray`.

    Returns:
        2-D ``float64`` array.
    """
    arr = np.asarray(value, dtype=float)
    if arr.ndim == 1:
        arr = arr.reshape(1, -1)
    if arr.ndim == 0:
        return np.zeros((0, 0), dtype=float)
    return arr


def _ensure_channel_matrix(value: Any, n_samples: int) -> np.ndarray:
    """Return a ``(n_channels, n_samples)`` float matrix from *value*.

    * ``None`` or empty → returns a ``(0, n_samples)`` zero matrix.
    * Columns already match *n_samples* → returned as-is.
    * Too many columns → truncated.
    * Too few columns → zero-padded on the right.

    Args:
        value: Source data (array-like or None).
        n_samples: Target number of samples (columns).

    Returns:
        2-D ``float64`` array with exactly *n_samples* columns.
    """
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


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------

@dataclass
class SignalImport:
    """Raw EMG signal and associated metadata as loaded from a recording file.

    Attributes:
        data: EMG data array of shape ``(n_channels, n_samples)``.
        fsamp: Sampling frequency in Hz.
        gridname: List of grid/electrode-array names, one per grid.
        muscle: Target muscle name(s), one per grid.
        device_name: Recording system name (e.g. ``"Quattrocento"``), or None.
        auxiliary: Non-EMG auxiliary channels ``(n_aux, n_samples)``; empty if none.
        auxiliaryname: Names of auxiliary channels, one per row of *auxiliary*.
        emgnotgrid: EMG channels not belonging to any grid ``(n_ch, n_samples)``.
        metadata: Free-form key/value metadata from the file header
            (gains, filter settings, etc.).
    """

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
        """Construct a :class:`SignalImport` from a plain dictionary.

        Handles type coercion, string-to-list normalisation, and optional
        field defaults so that callers can pass raw loader output without
        pre-processing.

        Args:
            payload: Dictionary with keys matching the dataclass field names.
                Missing keys receive default values.

        Returns:
            Populated :class:`SignalImport` instance.
        """
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
        """Return a deep copy of this instance.

        Returns:
            New :class:`SignalImport` with independent NumPy array copies.
        """
        return SignalImport.from_mapping(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary suitable for JSON or cache storage.

        All NumPy arrays are copied so mutations to the returned dict do not
        affect this instance.

        Returns:
            Dictionary with the same keys as the constructor parameters.
        """
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
    """Decomposition state loaded from a ``.npz`` or ``.mat`` file.

    This is the starting point for the interactive editing stage.  The frontend
    sends the saved file to ``/api/v1/edit/load`` which returns this structure as
    JSON so the user can inspect and modify discharge times.

    Attributes:
        pulse_trains_full: Per-MU pulse trains as nested Python lists
            ``[[float, ...], ...]`` of length *total_samples*.
        distime_all: Discharge-time indices for each MU ``[[int, ...], ...]``.
        fsamp: Sampling frequency in Hz, or None if unknown.
        grid_names: Names of the grids that produced the motor units.
        total_samples: Total signal length in samples.
        mu_grid_index: Grid index (0-based) for each motor unit.
        rois: Analysis windows as ``[(start, end), ...]`` in samples.
        parameters: Decomposition parameter snapshot (from
            :class:`~muedit.decomp.pipeline.DecompositionParameters`).
        muscle: Target muscle names, one per grid.
    """

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
        """Serialise to a JSON-safe dictionary.

        Returns:
            Dictionary with all fields converted to plain Python types.
        """
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
    """EMG signal paired with its decomposition output.

    Attributes:
        data: Preprocessed EMG array ``(n_channels, n_samples)``.
        fsamp: Sampling frequency in Hz.
        pulse_t: Pulse-train matrix ``(n_MU, n_samples)``.
        discharge_times: Per-MU discharge-time arrays (variable length).
    """

    data: np.ndarray
    fsamp: float
    pulse_t: np.ndarray
    discharge_times: list[np.ndarray]

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a dictionary using MATLAB-compatible key names.

        Returns:
            Dictionary with keys ``data``, ``fsamp``, ``PulseT``,
            ``Dischargetimes``.
        """
        return {
            "data": self.data,
            "fsamp": float(self.fsamp),
            "PulseT": self.pulse_t,
            "Dischargetimes": np.array(self.discharge_times, dtype=object),
        }


@dataclass
class DecompositionExport:
    """Full decomposition output, including preview data for the frontend.

    Attributes:
        signal: EMG + pulse-train export bundle.
        parameters: Decomposition parameter snapshot.
        grid_names: Grid/electrode-array names.
        sil: Silhouette scores keyed by window index.
        discard_channels: Per-grid bad-channel masks (0 = good, 1 = bad).
        coordinates: Per-grid electrode spatial coordinates.
        mu_grid_index: Grid index for each motor unit.
        preview: Lightweight payload used by the frontend to render plots.
    """

    signal: DecompositionSignalExport
    parameters: dict[str, Any]
    grid_names: list[str]
    sil: dict[int, list[float]]
    discard_channels: list[np.ndarray]
    coordinates: list[np.ndarray]
    mu_grid_index: list[int]
    preview: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe dictionary.

        Returns:
            Nested dictionary mirroring all dataclass fields.
        """
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
