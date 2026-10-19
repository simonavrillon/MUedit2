"""Importing an OT Bioelettronica OTB4 archive (OTBioLab 2.6), Novecento+ and other devices."""

from __future__ import annotations

import tarfile
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pytest

from muedit.errors import SignalLoadError
from muedit.io import load_signal
from tests._io_files import (
    OTB4_FEEDBACK,
    OTB_FSAMP,
    OTB_N_SAMPLES,
    Expected,
    Write,
    check_signal,
    otb4_frames,
    write_not_an_archive,
    write_otb4,
)

N_EMG = 128


def _feedback_rows() -> dict[int, np.ndarray]:
    """The 10 Hz feedback traces on the EMG clock: linear between samples, then held."""
    t = np.arange(OTB_N_SAMPLES) / OTB_FSAMP
    t_in = np.arange(len(OTB4_FEEDBACK)) / 10.0
    return {
        1 + col: np.interp(t, t_in, OTB4_FEEDBACK[:, col]).astype(np.float32) for col in range(2)
    }


def _synthetic(device: str) -> Expected:
    novecento = device == "Novecento+"
    if novecento:
        # 24-bit counts in int32 files, one per stream, scaled in float32.
        scale = np.float32(5 / 2**24 * 1000 / 256)
        grids = otb4_frames(np.int32, 128).T.astype(np.float32) * scale
        aux = otb4_frames(np.int32, 1)[:, 0].astype(np.float32) * scale
    else:
        # 16-bit counts, every track in one int16 file, scaled in float64.
        scale = 5 / 2**16 * 1000 / 256
        frames = otb4_frames(np.int16, 130).astype(np.float64)
        grids = (frames[:, :N_EMG].T * scale).astype(np.float32)
        aux = (frames[:, N_EMG] * scale).astype(np.float32)
    return Expected(
        shape=(N_EMG, OTB_N_SAMPLES),
        fsamp=OTB_FSAMP,
        grid_names=["HD08MM1305" if novecento else "GR04MM1305"] * 2,
        # The control track is left out; the feedback track of int32 samples is skipped.
        aux_names=[
            "AUX Force" if novecento else "Force",
            "Trapezoidal track - Performed Path",
            "Trapezoidal track - Original Path",
        ],
        metadata={
            "acquisition_date": "2026-03-26T14:31:26",
            "manufacturer": "OT Bioelettronica",
            "device_name": device,
            "software_versions": "OTBIOLAB26",
            "hardware_filters": ["HP: 10 Hz, LP: Fsamp/4"],
            "units": "mV",
            "gains": [256.0] * N_EMG,
            "emg_hpf": [10.0] * N_EMG,
            "emg_lpf": [512.0] * N_EMG,
            "aux_gains": [256.0, 1.0, 1.0],
            "aux_hpf": [10.0 if novecento else "n/a", "n/a", "n/a"],
        },
        data=grids,
        aux_rows={0: aux, **_feedback_rows()},
    )


def _sample(fixture: str, expected: Expected) -> tuple[Callable[..., Path], Expected]:
    return (lambda request, _: request.getfixturevalue(fixture)), expected


_FILES: dict[str, tuple[Write, Expected]] = {
    "quattrocento": (
        lambda _, tmp: write_otb4(tmp / "q.otb4", "Quattrocento"),
        _synthetic("Quattrocento"),
    ),
    "novecento": (
        lambda _, tmp: write_otb4(tmp / "n.otb4", "Novecento+"),
        _synthetic("Novecento+"),
    ),
    "sample-quattrocento": _sample(
        "otb4_file",
        Expected(
            shape=(128, 93_952),
            fsamp=2048.0,
            grid_names=["GR04MM1305"] * 2,
            metadata={"device_name": "Quattrocento", "acquisition_date": "2026-03-26T14:31:26"},
        ),
    ),
    "sample-novecento": _sample(
        "novecento_otb4_file",
        Expected(
            shape=(384, 57_400),
            fsamp=2000.0,
            grid_names=["HD08MM1305"] * 6,
            metadata={"device_name": "Novecento+", "acquisition_date": None},
        ),
    ),
    "sample-trapezoid": _sample(
        "passiflore_otb4_file",
        Expected(
            shape=(384, 705_536),
            fsamp=2048.0,
            grid_names=["GR08MM1305"] * 6,
            metadata={"acquisition_date": "2026-01-29T09:40:31"},
        ),
    ),
}


@pytest.mark.parametrize(
    "case",
    [pytest.param(c, marks=pytest.mark.data) if c.startswith("sample") else c for c in _FILES],
)
def test_loads_otb4(request: pytest.FixtureRequest, tmp_path: Path, case: str) -> None:
    write, expected = _FILES[case]
    check_signal(load_signal(str(write(request, tmp_path))), expected)


def _without(device: str, member: str) -> Callable[[Path], Path]:
    def write(tmp_path: Path) -> Path:
        source = write_otb4(tmp_path / "full.otb4", device)
        path = tmp_path / "rec.otb4"
        with tarfile.open(source) as full, tarfile.open(path, "w") as tar:
            for info in full.getmembers():
                if info.name != member:
                    tar.addfile(info, full.extractfile(info))
        return path

    return write


_BAD_FILES: dict[str, tuple[Callable[[Path], Path], str]] = {
    "not-an-archive": (
        lambda tmp: write_not_an_archive(tmp, "rec.otb4"),
        "Failed to extract OTB4 file",
    ),
    "no-track-list": (_without("Quattrocento", "Tracks_000.xml"), r"No Tracks_000\.xml"),
    "no-signal-file": (
        _without("Quattrocento", "20260326143126.sig"),
        r"Cannot reshape FB\.sig into 130 channels",
    ),
    "a-stream-file-missing": (
        lambda tmp: write_otb4(tmp / "rec.otb4", "Novecento+", missing_signal=True),
        r"missing Aux\.sig, the signal file of 1 track",
    ),
    "no-emg-track": (
        lambda tmp: write_otb4(tmp / "rec.otb4", "Quattrocento", emg=False),
        "holds no EMG track",
    ),
}


@pytest.mark.parametrize("case", list(_BAD_FILES))
def test_refuses_bad_otb4(tmp_path: Path, case: str) -> None:
    write, message = _BAD_FILES[case]
    with pytest.raises(SignalLoadError, match=message):
        load_signal(str(write(tmp_path)))
