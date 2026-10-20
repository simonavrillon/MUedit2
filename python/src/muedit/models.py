"""Data models shared by the loaders, the decomposition pipeline and the editing stage."""

from __future__ import annotations

import mmap
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
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


def spike_array(values: Any) -> IntArray:
    """Discharge times as sorted, unique, non-negative int32 sample indices."""
    arr = np.asarray(values if values is not None else [], dtype=np.int64).reshape(-1)
    arr = np.unique(arr[arr >= 0])
    if arr.size and arr[-1] > np.iinfo(np.int32).max:
        raise ValueError(f"spike time {int(arr[-1])} does not fit in int32")
    return arr.astype(np.int32)


def _as_2d_float_array(value: Any) -> FloatArray:
    """Cast value to a 2-D float array, reshaping 1-D input to (1, n).

    float32 input (loader output, memory-mapped or not) is kept without a copy;
    anything else becomes float64.
    """
    arr = np.asarray(value)
    if arr.dtype != np.float32:
        arr = arr.astype(np.float64, copy=False)
    if arr.ndim == 1:
        arr = arr.reshape(1, -1)
    if arr.ndim == 0:
        return np.zeros((0, 0), dtype=float)
    return arr


def resident_nbytes(arr: np.ndarray) -> int:
    """Heap bytes ``arr`` holds: 0 for a view of a memory-mapped file, whose pages the OS manages."""
    base: Any = arr
    while isinstance(base, np.ndarray):
        base = base.base
    return 0 if isinstance(base, mmap.mmap) else int(arr.nbytes)


def _readonly(arr: np.ndarray) -> np.ndarray:
    """A non-writable view of ``arr``: no data copy, and writes through it raise."""
    view = arr.view()
    view.flags.writeable = False
    return view


def as_name_list(value: str | Iterable[Any] | None) -> list[str]:
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
    pad = np.zeros((arr.shape[0], n_samples - arr.shape[1]), dtype=arr.dtype)
    return np.hstack([arr, pad])


@dataclass
class SignalImport:
    """Raw EMG signal and associated metadata as loaded from a recording file."""

    data: FloatArray
    fsamp: float
    grid_names: list[str] = field(default_factory=list)
    muscle_names: list[str] = field(default_factory=list)
    aux_data: FloatArray = field(default_factory=lambda: np.zeros((0, 0), dtype=float))
    aux_names: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def build(
        cls,
        *,
        data: Any,
        fsamp: Any = 0.0,
        grid_names: str | Iterable[Any] | None = None,
        muscle_names: str | Iterable[Any] | None = None,
        aux_data: Any = None,
        aux_names: str | Iterable[Any] | None = None,
        metadata: Any = None,
    ) -> SignalImport:
        """Construct from raw loader values, coercing types and filling defaults.

        ``data`` becomes a 2-D float array (a 1-D input is one channel), a missing
        ``fsamp`` becomes 0.0, a single name becomes a one-item list, and
        ``aux_data`` is padded or truncated to the EMG sample count.
        """
        data_arr = _as_2d_float_array(data)
        n_samples = int(data_arr.shape[1]) if data_arr.ndim == 2 else 0
        return cls(
            data=data_arr,
            fsamp=float(fsamp) if fsamp is not None else 0.0,
            grid_names=as_name_list(grid_names),
            muscle_names=as_name_list(muscle_names),
            aux_data=_ensure_channel_matrix(aux_data, n_samples),
            aux_names=as_name_list(aux_names),
            metadata=dict(cast(dict[str, Any], metadata)) if isinstance(metadata, dict) else {},
        )

    def readonly_view(self) -> SignalImport:
        """Share the arrays read-only; lists and top-level metadata are independent copies."""
        return SignalImport(
            data=_readonly(self.data),
            fsamp=self.fsamp,
            grid_names=list(self.grid_names),
            muscle_names=list(self.muscle_names),
            aux_data=_readonly(self.aux_data),
            aux_names=list(self.aux_names),
            metadata=dict(self.metadata),
        )

    def without_data(self) -> SignalImport:
        """``readonly_view`` without the EMG samples, so a holder does not keep the recording alive."""
        n_samples = self.data.shape[1] if self.data.ndim == 2 else 0
        return replace(self.readonly_view(), data=np.zeros((0, n_samples)))

    @property
    def nbytes(self) -> int:
        """Heap size of the EMG and auxiliary arrays; memory-mapped ones count as 0."""
        return resident_nbytes(self.data) + resident_nbytes(self.aux_data)


@dataclass
class EditSignalContext:
    """EMG embedded in a decomposition file, kept for editing and BIDS export.

    Built by ``load_decomposition`` and held in the API edit cache, its EMG usually
    memory-mapped from the edit session's store.
    """

    data: FloatArray  # (n_channels, n_samples); (0, 0) when the file holds only a mask
    fsamp: float  # 0.0 when the file does not record it
    grid_names: list[str] = field(default_factory=list)
    discard_channels: list[IntArray] = field(
        default_factory=list
    )  # per grid, 1 = discarded channel
    coordinates: list[FloatArray] = field(default_factory=list)  # per grid, (n_channels, 2)
    ied: list[float] | None = None  # per-grid inter-electrode distance, mm
    aux_data: FloatArray | None = None  # (n_aux, n_samples)
    aux_names: list[str] = field(default_factory=list)
    artifact_mask: BoolArray | None = None  # (n_samples,)
    # Loader BIDS fields (decomposition_file.LOADER_BIDS_META_KEYS) the file recorded.
    loader_meta: dict[str, Any] = field(default_factory=dict)
    # Schema v1 .npz files embed the EMG already notch- and bandpass-filtered, not raw.
    prefiltered: bool = False
    # The target's segments the run decomposed with (target channel, units, settings, segments).
    target_segments: dict[str, Any] | None = None

    @property
    def nbytes(self) -> int:
        """Heap bytes of all arrays in the context; memory-mapped ones count 0."""
        arrays = [
            self.data,
            self.aux_data,
            self.artifact_mask,
            *self.discard_channels,
            *self.coordinates,
        ]
        return sum(resident_nbytes(a) for a in arrays if a is not None)


@dataclass
class LoadedDecomposition:
    """Decomposition state loaded from a .npz or .mat file for the interactive editing stage."""

    pulse_trains: FloatArray = field(default_factory=lambda: np.zeros((0, 0)))  # (n_mu, T)
    spike_times: list[IntArray] = field(default_factory=list)  # one spike_array per MU
    fsamp: float = 0.0  # 0.0 when the file does not record it
    grid_names: list[str] = field(default_factory=list)
    total_samples: int = 0
    mu_grid_index: list[int] = field(default_factory=list)
    rois: list[tuple[int, int]] = field(default_factory=list)
    parameters: dict[str, Any] = field(default_factory=dict)
    muscle_names: list[str] = field(default_factory=list)
    sil: list[float] = field(default_factory=list)
