"""Min/max pyramids: the envelope of a long series at any zoom, read from a few small arrays."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from muedit.io.store import ArrayStore
from muedit.models import FloatArray, resident_nbytes

#: Samples per bin of the finest level; each level above has ``LEVEL_RATIO`` times more.
BASE_FACTOR = 16
LEVEL_RATIO = 4
#: A level is kept while it still has this many bins; coarser views reduce it further.
MIN_LEVEL_BINS = 64

#: ``read(rows, start, end)``: the full-resolution samples of ``rows`` over ``[start, end)``.
Reader = Callable[[slice, int, int], FloatArray]


def pyramid_factors(n_samples: int) -> list[int]:
    """Bin sizes 16, 64, 256, … of the levels a series of ``n_samples`` gets."""
    factors: list[int] = []
    factor = BASE_FACTOR
    while n_samples // factor >= MIN_LEVEL_BINS:
        factors.append(factor)
        factor *= LEVEL_RATIO
    return factors


def _bin_reduce(x: FloatArray, size: int, op: Callable[..., FloatArray]) -> FloatArray:
    """``op`` over consecutive bins of ``size`` along the last axis; the last bin may be short."""
    n = x.shape[-1]
    full = n // size
    out = np.empty((*x.shape[:-1], -(-n // size)), dtype=x.dtype)
    out[..., :full] = op(x[..., : full * size].reshape(*x.shape[:-1], full, size), axis=-1)
    if n % size:
        out[..., full] = op(x[..., full * size :], axis=-1)
    return out


@dataclass
class MinMaxPyramid:
    """Per-row min and max of a ``(rows, samples)`` series over bins of each factor.

    ``levels[i]`` is float32 ``(2, rows, ceil(samples / factors[i]))``: minima, then maxima.
    """

    n_samples: int
    factors: list[int]
    levels: list[FloatArray]

    @classmethod
    def allocate(cls, store: ArrayStore, name: str, n_rows: int, n_samples: int) -> MinMaxPyramid:
        """Empty levels for ``n_rows`` rows, one ``<name>-<factor>`` array each in ``store``."""
        factors = pyramid_factors(n_samples)
        levels = [
            store.allocate(f"{name}-{f}", (2, n_rows, -(-n_samples // f)), np.float32)
            for f in factors
        ]
        return cls(n_samples, factors, levels)

    def write(self, row: int, samples: FloatArray) -> None:
        """Fill rows ``row, row + 1, …`` of every level from their full-length ``samples``."""
        rows = slice(row, row + samples.shape[0])
        mins = maxs = np.asarray(samples, dtype=np.float32)
        size = BASE_FACTOR
        for level in self.levels:
            # Each level's bins are whole groups of the level below, so it reduces that one.
            mins = _bin_reduce(mins, size, np.min)
            maxs = _bin_reduce(maxs, size, np.max)
            level[0, rows] = mins
            level[1, rows] = maxs
            size = LEVEL_RATIO

    def seal(self, store: ArrayStore) -> MinMaxPyramid:
        """The same pyramid with its levels sealed in ``store``."""
        return MinMaxPyramid(self.n_samples, self.factors, [store.seal(x) for x in self.levels])

    @property
    def nbytes(self) -> int:
        """Heap bytes of the levels (0 when they are memory-mapped)."""
        return sum(resident_nbytes(x) for x in self.levels)


@dataclass
class SeriesView:
    """A viewport of a series: ``samples`` when it has no more samples than bins, else an envelope."""

    start: int
    end: int
    factor: int  # samples per bin of the source the envelope was reduced from; 1 = raw samples
    samples: FloatArray | None = None  # (rows, end - start)
    mins: FloatArray | None = None  # (rows, bins)
    maxs: FloatArray | None = None


#: Samples of a row reduced at once by ``envelope``.
ENVELOPE_BLOCK = 1 << 22


def envelope(read: Callable[[int, int], FloatArray], start: int, end: int, bins: int) -> SeriesView:
    """``[start, end)`` of one row in ``bins`` bins, reduced from the samples ``read`` returns.

    Exact: each bin covers its own samples only. The row is read a block at a time, so a
    zoomed-out view of a long recording costs a few megabytes.
    """
    span = end - start
    if span <= bins:
        return SeriesView(start, end, 1, samples=np.asarray(read(start, end), np.float32)[None])
    edges = start + (np.arange(bins + 1, dtype=np.int64) * span) // bins
    mins = np.empty(bins, dtype=np.float32)
    maxs = np.empty(bins, dtype=np.float32)
    first = 0
    while first < bins:
        stop = int(np.searchsorted(edges, edges[first] + ENVELOPE_BLOCK, side="right")) - 1
        stop = min(max(stop, first + 1), bins)
        block = np.asarray(read(int(edges[first]), int(edges[stop])), np.float32)
        at = edges[first:stop] - edges[first]
        mins[first:stop] = np.minimum.reduceat(block, at)
        maxs[first:stop] = np.maximum.reduceat(block, at)
        first = stop
    return SeriesView(start, end, span // bins, mins=mins[None], maxs=maxs[None])


def view(
    pyramid: MinMaxPyramid, read: Reader, rows: slice, start: int, end: int, bins: int
) -> SeriesView:
    """``[start, end)`` of ``rows`` in ``bins`` bins, from the coarsest level fine enough for them.

    An output bin takes every level bin it overlaps, so it holds all of its samples and at
    most one level bin (``≤ span / bins`` samples) of its neighbours on each side.
    """
    span = end - start
    if span <= bins:
        return SeriesView(start, end, 1, samples=np.asarray(read(rows, start, end), np.float32))
    per_bin = span // bins
    fine_enough = [i for i, f in enumerate(pyramid.factors) if f <= per_bin]
    if fine_enough:
        factor = pyramid.factors[fine_enough[-1]]
        level = pyramid.levels[fine_enough[-1]]
        first, last = start // factor, -(-end // factor)
        mins_src, maxs_src = level[0, rows, first:last], level[1, rows, first:last]
    else:
        factor, first = 1, start
        mins_src = maxs_src = np.asarray(read(rows, start, end), np.float32)
    # Output bins span at least ``factor`` samples, so their first level bins strictly increase.
    edges = start + (np.arange(bins + 1, dtype=np.int64) * span) // bins
    starts = edges[:-1] // factor - first
    mins = np.minimum.reduceat(mins_src, starts, axis=1)
    maxs = np.maximum.reduceat(maxs_src, starts, axis=1)
    # A bin ending inside a level bin also needs that one, which reduceat gave to the next bin.
    split = edges[1:] % factor != 0
    if split.any():
        shared = edges[1:][split] // factor - first
        mins[:, split] = np.minimum(mins[:, split], mins_src[:, shared])
        maxs[:, split] = np.maximum(maxs[:, split], maxs_src[:, shared])
    return SeriesView(start, end, factor, mins=mins, maxs=maxs)
