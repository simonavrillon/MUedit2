"""Delay-embedded batches read from a (channels, samples) array, for streamed decomposition passes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import numpy as np

from muedit.models import BoolArray, FloatArray, IntArray
from muedit.signal.decomp_primitives import extend_signal


class SampleSource(Protocol):
    """A ``(channels, samples)`` array readable by sample range (ndarray, memmap, row selection)."""

    @property
    def shape(self) -> tuple[int, ...]: ...

    def __getitem__(self, key: Any) -> Any: ...


@dataclass(frozen=True)
class RowSelection:
    """Some rows of a ``(channels, samples)`` array, copied only when a sample range is read."""

    data: FloatArray
    rows: IntArray

    @property
    def shape(self) -> tuple[int, int]:
        return (len(self.rows), self.data.shape[1])

    def __getitem__(self, key: tuple[slice, slice]) -> FloatArray:
        row_key, col_key = key
        if row_key != slice(None):
            raise IndexError("RowSelection only supports [:, start:stop] reads")
        return self.data[self.rows, col_key]


class StreamedExtender:
    """Extended samples of a source read by range, so batches can be visited in any order."""

    def __init__(
        self,
        source: SampleSource,
        ex_factor: int,
        offset: FloatArray | None = None,
        dtype: type[np.floating[Any]] = np.float64,
        samples_first: bool = False,
        artifact_mask: BoolArray | None = None,
    ) -> None:
        self.source = source
        self.ex_factor = max(1, int(ex_factor))
        self.dtype = dtype
        self.offset = None if offset is None else np.asarray(offset).astype(dtype)[:, None]
        self.samples_first = samples_first
        self.artifact_mask = artifact_mask
        self.n_channels = int(source.shape[0])
        self.n_samples = int(source.shape[1])
        self.n_extended = self.n_channels * self.ex_factor

    def read(self, start: int, stop: int) -> FloatArray:
        """Extended samples ``[start, stop)``: ``(n, n_ext)`` if samples-first, else ``(n_ext, n)``."""
        # Each read fetches its own ex_factor - 1 samples of look-back, so every sample is
        # complete except the recording start, which keeps extend_signal's zero padding.
        lo = max(0, start - (self.ex_factor - 1))
        raw = np.array(self.source[:, lo:stop], dtype=self.dtype)
        if self.offset is not None:
            raw -= self.offset
        if self.samples_first:
            return extend_signal(raw.T, self.ex_factor, samples_first=True)[start - lo :]
        return extend_signal(raw, self.ex_factor)[:, start - lo : stop - lo]

    @property
    def first_complete(self) -> int:
        """First sample whose look-back lies entirely inside the recording."""
        return self.ex_factor - 1

    def complete(self, start: int, stop: int) -> BoolArray:
        """Per-sample flag: False where the look-back reaches before the recording start."""
        return np.arange(start, stop) >= self.first_complete

    def mask(self, start: int, stop: int) -> BoolArray | None:
        """Extended artifact mask of samples ``[start, stop)`` (see ``extend_mask``), or None."""
        if self.artifact_mask is None:
            return None
        lo = max(0, start - (self.ex_factor - 1))
        raw = np.zeros(stop - lo, dtype=bool)
        part = self.artifact_mask[lo:stop]
        raw[: part.size] = part
        return extend_mask(raw, self.ex_factor)[start - lo :]


def extend_mask(mask: BoolArray, ex_factor: int) -> BoolArray:
    """Mask of the extended samples: sample ``t`` is masked if any of ``t - ex_factor + 1 .. t`` is."""
    out = np.array(mask, dtype=bool)
    for m in range(1, ex_factor):
        out[m:] |= mask[:-m]
    return out
