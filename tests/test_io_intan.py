"""Importing an Intan RHD recording, in each of its three save layouts."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import numpy as np
import pytest

from muedit.errors import SignalLoadError
from muedit.io import load_signal
from tests._io_files import (
    INTAN_FSAMP,
    INTAN_GRID,
    INTAN_N_CH,
    INTAN_N_PORTS,
    INTAN_N_SAMPLES,
    Expected,
    Write,
    check_signal,
    intan_signals,
    write_grid_sidecar,
    write_intan_header,
    write_intan_per_channel,
    write_intan_per_signal_type,
    write_intan_traditional,
)


def _folder(tmp_path: Path, name: str) -> Path:
    folder = tmp_path / name
    folder.mkdir()
    write_grid_sidecar(folder, [INTAN_GRID], ["ta", "gm"])
    return folder


def _per_channel(tmp_path: Path) -> Path:
    folder = _folder(tmp_path, "per_channel")
    write_intan_per_channel(folder, *intan_signals())
    return folder


def _per_signal_type(tmp_path: Path) -> Path:
    folder = _folder(tmp_path, "per_signal_type")
    write_intan_per_signal_type(folder, *intan_signals())
    return folder


def _traditional(tmp_path: Path) -> Path:
    path = _folder(tmp_path, "traditional") / "recording.rhd"
    write_intan_traditional(path, *intan_signals())
    return path


def _long_header(tmp_path: Path) -> Path:
    """A header past the first 64 KiB read: it is parsed from a growing prefix."""
    path = _folder(tmp_path, "long_header") / "recording.rhd"
    write_intan_traditional(path, *intan_signals(), notes=("n" * 50_000, "", ""))
    return path


def _synthetic(every_aux_sample: bool) -> Expected:
    amp, aux, digital = intan_signals()
    n_emg = INTAN_N_PORTS * INTAN_N_CH
    aux_scaled = (aux[0] * 37.4e-6).astype(np.float32)
    return Expected(
        shape=(n_emg, INTAN_N_SAMPLES),
        fsamp=INTAN_FSAMP,
        grid_names=[INTAN_GRID] * INTAN_N_PORTS,
        muscle_names=["ta", "gm"],
        aux_names=["A-AUX1", "B-AUX1", "DIGITAL-IN-01"],
        metadata={
            "manufacturer": "Intan Technologies",
            "units": "mV",
            "aux_units": "V",
            "aux_kinds": ["analog", "analog", "digital"],
            "powerline_freq": 50.0,
            "hardware_filters": ["HP: 20.00 Hz, LP: 500.00 Hz, Notch: 50 Hz"],
            "emg_hpf": [20.0] * n_emg,
            "emg_lpf": [500.0] * n_emg,
            "electrode_impedances": [52.5] + [None] * (n_emg - 1),
            "acquisition_date": None,
        },
        # Counts scaled in float64 (0.195 uV per bit) and rounded once to float32.
        data=(amp.astype(np.float64) * 0.195e-3).astype(np.float32),
        aux_rows={
            # Aux inputs are sampled every 4th amplifier sample; layouts that store only
            # those samples hold each one for the next three.
            0: aux_scaled if every_aux_sample else np.repeat(aux_scaled[::4], 4),
            2: digital[0].astype(np.float32),
        },
    )


def _sample() -> Expected:
    return Expected(
        shape=(128, 2_584_832),
        fsamp=10000.0,
        grid_names=["INTAN64-1305"] * 2,
        muscle_names=[],
        metadata={"manufacturer": "Intan Technologies", "units": "mV"},
    )


_FILES: dict[str, tuple[Write, Expected]] = {
    "per-channel": (lambda _, tmp: _per_channel(tmp), _synthetic(every_aux_sample=True)),
    "per-signal-type": (lambda _, tmp: _per_signal_type(tmp), _synthetic(every_aux_sample=False)),
    "traditional": (lambda _, tmp: _traditional(tmp), _synthetic(every_aux_sample=False)),
    "long-header": (lambda _, tmp: _long_header(tmp), _synthetic(every_aux_sample=False)),
    "sample": (lambda request, _: request.getfixturevalue("intan_dir"), _sample()),
}


@pytest.mark.parametrize(
    "case",
    [pytest.param(c, marks=pytest.mark.data) if c == "sample" else c for c in _FILES],
)
def test_loads_intan(request: pytest.FixtureRequest, tmp_path: Path, case: str) -> None:
    write, expected = _FILES[case]
    check_signal(load_signal(str(write(request, tmp_path))), expected)


def _not_rhd(tmp_path: Path) -> Path:
    path = tmp_path / "bogus.rhd"
    path.write_bytes(b"\x00" * 4096)
    return path


def _header_only(tmp_path: Path) -> Path:
    folder = _folder(tmp_path, "empty")
    write_intan_header(folder / "info.rhd")
    return folder


def _without_time(tmp_path: Path) -> Path:
    folder = _per_channel(tmp_path)
    (folder / "time.dat").unlink()
    return folder


def _missing_channel(tmp_path: Path) -> Path:
    folder = _per_channel(tmp_path)
    (folder / "amp-B-031.dat").unlink()
    return folder


def _truncated(tmp_path: Path) -> Path:
    path = _traditional(tmp_path)
    with open(path, "ab") as handle:
        handle.write(b"\x00\x01")
    return path


def _unnamed_grids(tmp_path: Path) -> Path:
    folder = _per_channel(tmp_path)
    (folder / "muedit_grids.json").unlink()
    return folder


_BAD_FILES: dict[str, tuple[Callable[[Path], Path], str]] = {
    "not-an-rhd-file": (_not_rhd, "magic number"),
    "no-data-files": (_header_only, "no amplifier data files"),
    "no-time-file": (_without_time, r"Missing time\.dat"),
    "a-channel-file-missing": (_missing_channel, r"amp-B-031\.dat is missing"),
    "truncated": (_truncated, "not a whole number"),
    "grids-not-named": (_unnamed_grids, "does not record one"),
}


@pytest.mark.parametrize("case", list(_BAD_FILES))
def test_refuses_bad_intan(tmp_path: Path, case: str) -> None:
    write, message = _BAD_FILES[case]
    with pytest.raises(SignalLoadError, match=message):
        load_signal(str(write(tmp_path)))
