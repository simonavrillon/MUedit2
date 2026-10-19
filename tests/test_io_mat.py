"""Importing a MATLAB ``signal`` struct (.mat v5 and v7.3)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import numpy as np
import pytest

from muedit.errors import SignalLoadError
from muedit.io import load_signal
from tests._io_files import Expected, check_signal, write_mat, write_mat_signal

N_SAMPLES = 3001


def _signals() -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(0)
    return rng.normal(size=(64, N_SAMPLES)) * 50.0, rng.normal(size=(2, N_SAMPLES))


@pytest.mark.parametrize("version", ["v5", "v7.3"])
def test_loads_mat(tmp_path: Path, version: str) -> None:
    data, aux = _signals()
    sig = load_signal(str(write_mat_signal(tmp_path / "rec.mat", version, data, aux)))
    check_signal(
        sig,
        Expected(
            shape=(64, N_SAMPLES),
            fsamp=2048.0,
            grid_names=["GR08MM1305"],
            muscle_names=["ta"],
            # v7.3 stores a cell array of names as references, which the writer above leaves out.
            aux_names=["Force", "Trigger"] if version == "v5" else None,
            metadata={
                "device_name": "Quattrocento",
                "software_versions": "MATLAB",
                "units": "mV",
                "recording_type": "continuous",
            },
            # Scaled in float64 and rounded once to float32.
            data=data.astype(np.float32),
            aux_rows={0: aux[0].astype(np.float32), 1: aux[1].astype(np.float32)},
        ),
    )


_DECOMPOSITION = {"Pulsetrain": np.zeros((1, 10)), "Dischargetimes": np.zeros((1, 3))}


def _not_mat(path: Path) -> Path:
    path.write_bytes(b"not a MATLAB file" * 10)
    return path


_BAD_FILES: dict[str, tuple[Callable[[Path], Path], str]] = {
    **{
        f"no-signal-struct-{v}": (
            lambda p, v=v: write_mat(p, v, "emg", {"data": np.zeros((4, 10))}),
            "Key 'signal' not found",
        )
        for v in ("v5", "v7.3")
    },
    **{
        f"a-decomposition-{v}": (
            lambda p, v=v: write_mat(p, v, "signal", _DECOMPOSITION),
            "open it in edit mode",
        )
        for v in ("v5", "v7.3")
    },
    "not-a-mat-file": (_not_mat, "Failed to load MAT"),
}


@pytest.mark.parametrize("case", list(_BAD_FILES))
def test_refuses_bad_mat(tmp_path: Path, case: str) -> None:
    write, message = _BAD_FILES[case]
    with pytest.raises(SignalLoadError, match=message):
        load_signal(str(write(tmp_path / "rec.mat")))
