"""Server-side edit session: the reference copy of a loaded decomposition.

The browser only ever holds a display copy of the pulse trains. Everything that
is written to disk comes from here, so untouched motor units come out exactly as
they were read and recomputed ones keep the precision they were computed in.

Motor units are addressed by their stable id (``g<grid>_mu<n>``), never by
position: positions change when duplicates or flagged units are removed.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

import numpy as np

from muedit.models import FloatArray

# Newest recomputed versions kept per motor unit, besides revision 0. Undo only
# ever needs the previous version, reset only needs revision 0.
KEEP_VERSIONS = 3


class UnknownMuError(KeyError):
    """The session has no motor unit with this id."""


class RowUnavailableError(LookupError):
    """The requested revision of a motor unit's row has been discarded."""


@dataclass
class EditSession:
    """Reference pulse trains of one open decomposition, addressed by motor-unit id."""

    source_path: str
    n_samples: int
    # uid -> {revision: row}. Revision 0 is the row as read from the file (a
    # read-only view of the loaded matrix, no copy); each recomputation adds one.
    _versions: dict[str, dict[int, FloatArray]]
    _latest: dict[str, int]
    _nbytes_base: int
    _extra_bytes: int = 0  # recomputed rows only; rows shared by a duplicate are not recounted
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    @classmethod
    def from_matrix(cls, source_path: str, matrix: FloatArray, mu_uids: list[str]) -> EditSession:
        """Wrap ``matrix`` (one row per motor unit, in ``mu_uids`` order) without copying it."""
        if matrix.ndim != 2 or matrix.shape[0] != len(mu_uids):
            raise ValueError(
                f"matrix has shape {matrix.shape} but {len(mu_uids)} motor-unit ids were given"
            )
        if len(set(mu_uids)) != len(mu_uids):
            raise ValueError("motor-unit ids must be unique")
        frozen = np.ascontiguousarray(matrix).view()
        frozen.flags.writeable = False
        return cls(
            source_path=source_path,
            n_samples=int(frozen.shape[1]),
            _versions={uid: {0: frozen[i]} for i, uid in enumerate(mu_uids)},
            _latest=dict.fromkeys(mu_uids, 0),
            _nbytes_base=int(frozen.nbytes),
        )

    @property
    def mu_uids(self) -> list[str]:
        with self._lock:
            return list(self._versions)

    @property
    def nbytes(self) -> int:
        """Resident size: the loaded matrix plus the recomputed rows kept on top of it."""
        with self._lock:
            return self._nbytes_base + self._extra_bytes

    def row(self, uid: str, revision: int = 0) -> FloatArray:
        """The row of ``uid`` at ``revision`` (read-only)."""
        with self._lock:
            versions = self._versions.get(uid)
            if versions is None:
                raise UnknownMuError(uid)
            row = versions.get(revision)
        if row is None:
            raise RowUnavailableError(f"{uid} revision {revision}")
        return row

    def add_version(self, uid: str, values: FloatArray) -> int:
        """Store a recomputed row for ``uid`` and return its revision number."""
        if values.shape != (self.n_samples,):
            raise ValueError(f"row has shape {values.shape}, expected ({self.n_samples},)")
        frozen = np.array(values, dtype=np.float64, copy=True)
        frozen.flags.writeable = False
        with self._lock:
            versions = self._versions.get(uid)
            if versions is None:
                raise UnknownMuError(uid)
            revision = self._latest[uid] + 1
            versions[revision] = frozen
            self._latest[uid] = revision
            self._extra_bytes += int(frozen.nbytes)
            recomputed = sorted(rev for rev in versions if rev != 0)
            for stale in recomputed[:-KEEP_VERSIONS]:
                self._extra_bytes -= int(versions.pop(stale).nbytes)
        return revision

    def duplicate(self, source_uid: str, source_revision: int, new_uid: str) -> None:
        """Register ``new_uid`` as a copy of a row of ``source_uid`` (shared, not copied)."""
        row = self.row(source_uid, source_revision)
        with self._lock:
            if new_uid in self._versions:
                raise ValueError(f"motor-unit id already exists: {new_uid}")
            self._versions[new_uid] = {0: row}
            self._latest[new_uid] = 0

    def assemble(self, uids: list[str], revisions: dict[str, int] | None = None) -> FloatArray:
        """Rows for ``uids`` (at the given revisions, default 0) as one float64 matrix."""
        revisions = revisions or {}
        out = np.empty((len(uids), self.n_samples), dtype=np.float64)
        for i, uid in enumerate(uids):
            out[i, :] = self.row(uid, int(revisions.get(uid, 0)))
        return out
