"""Importing an OT Bioelettronica OTB+ archive (OTBioLab+)."""

from __future__ import annotations

import logging
import tarfile
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pytest

from muedit.errors import SignalLoadError
from muedit.io import load_signal
from tests._io_files import (
    OTB_FSAMP,
    OTB_N_SAMPLES,
    OTB_PLUS_N_EMG,
    OTB_PLUS_SIPS,
    Expected,
    Write,
    check_signal,
    otb_plus_frames,
    write_not_an_archive,
    write_otb_plus,
)

#: Quattrocento counts to mV (16 bits over 5 V at gain 150), whatever serial follows the name.
QUATTROCENTO_MV = 0.00050863


def _synthetic() -> Expected:
    frames = otb_plus_frames().astype(np.float64)
    long_sip, short_sip, _ = OTB_PLUS_SIPS.values()
    return Expected(
        shape=(OTB_PLUS_N_EMG, OTB_N_SAMPLES),
        fsamp=OTB_FSAMP,
        # Ordered by input: IN 1 is grid 1, MULTIPLE IN 1 grid 3.
        grid_names=["GR08MM1305"] * 2,
        muscle_names=["Right Tibialis Anterior", "Right Soleus"],
        # The control adapter is left out; of the .sip traces, the empty one is skipped.
        aux_names=["AUX Force", "trapezoidalpath_1", "trapezoidalpath_2"],
        metadata={
            "acquisition_date": "2023-01-30T11:50:31",  # from the .sig file name
            "manufacturer": "OT Bioelettronica",
            "device_name": "QUATTROCENTO; ID=Unknown",
            "software_versions": "OTBioLab+",
            "hardware_filters": ["HP: 10, LP: 500"],
            "units": "mV",
            "gains": [150.0] * OTB_PLUS_N_EMG,
            "emg_hpf": [10.0] * OTB_PLUS_N_EMG,
            "emg_lpf": [500.0] * OTB_PLUS_N_EMG,
            "aux_gains": [0.5, 1.0, 1.0],
            "aux_hpf": [0.0, "n/a", "n/a"],
            "aux_lpf": [100.0, "n/a", "n/a"],
        },
        data=(frames[:, :OTB_PLUS_N_EMG].T * QUATTROCENTO_MV).astype(np.float32),
        aux_rows={
            0: (frames[:, OTB_PLUS_N_EMG] * QUATTROCENTO_MV).astype(np.float32),
            # A longer trace is cut to the EMG; a shorter one holds its last value.
            1: long_sip[:OTB_N_SAMPLES].astype(np.float32),
            2: np.concatenate([short_sip, np.full(OTB_N_SAMPLES - 3, 10.0)]).astype(np.float32),
        },
    )


_FILES: dict[str, tuple[Write, Expected, str]] = {
    "synthetic": (lambda _, tmp: write_otb_plus(tmp / "rec.otb+"), _synthetic(), ""),
    "two-signal-files": (
        lambda _, tmp: write_otb_plus(tmp / "rec.otb+", extra_sig=True),
        _synthetic(),
        "holds 2 .sig files; loading 20230130115031_03.sig, ignoring zz_extra.sig",
    ),
    "sample": (
        lambda request, _: request.getfixturevalue("otb_plus_file"),
        Expected(
            shape=(384, 461_952),
            fsamp=2048.0,
            grid_names=["GR08MM1305"] * 6,
            metadata={
                "acquisition_date": "2023-01-30T11:50:31",
                "manufacturer": "OT Bioelettronica",
                "software_versions": "OTBioLab+",
                "units": "mV",
            },
        ),
        "",
    ),
}


@pytest.mark.parametrize(
    "case",
    [pytest.param(c, marks=pytest.mark.data) if c == "sample" else c for c in _FILES],
)
def test_loads_otb_plus(
    request: pytest.FixtureRequest, tmp_path: Path, caplog: pytest.LogCaptureFixture, case: str
) -> None:
    write, expected, warning = _FILES[case]
    with caplog.at_level(logging.WARNING, logger="muedit.io._otb"):
        check_signal(load_signal(str(write(request, tmp_path))), expected)
    assert warning in caplog.text


def _without(member: str) -> Callable[[Path], Path]:
    def write(tmp_path: Path) -> Path:
        source = write_otb_plus(tmp_path / "full.otb+")
        path = tmp_path / "rec.otb+"
        with tarfile.open(source) as full, tarfile.open(path, "w") as tar:
            for info in full.getmembers():
                if not info.name.endswith(member):
                    tar.addfile(info, full.extractfile(info))
        return path

    return write


def _ragged_signal(tmp_path: Path) -> Path:
    source = write_otb_plus(tmp_path / "full.otb+")
    path = tmp_path / "rec.otb+"
    with tarfile.open(source) as full, tarfile.open(path, "w") as tar:
        for info in full.getmembers():
            data = full.extractfile(info).read()  # type: ignore[union-attr]
            if info.name.endswith(".sig"):
                data += b"\x00\x01"
            info.size = len(data)
            staging = tmp_path / "member"
            staging.write_bytes(data)
            tar.add(staging, arcname=info.name)
    return path


_BAD_FILES: dict[str, tuple[Callable[[Path], Path], str]] = {
    "not-an-archive": (
        lambda tmp: write_not_an_archive(tmp, "rec.otb+"),
        "Failed to extract OTB\\+ file",
    ),
    "no-signal-file": (_without(".sig"), r"No \.sig file"),
    "no-xml-of-its-signal": (
        _without("_03.xml"),
        r"Could not find XML file: 20230130115031_03\.xml",
    ),
    "signal-not-whole-frames": (_ragged_signal, "into 130 channels"),
}


@pytest.mark.parametrize("case", list(_BAD_FILES))
def test_refuses_bad_otb_plus(tmp_path: Path, case: str) -> None:
    write, message = _BAD_FILES[case]
    with pytest.raises(SignalLoadError, match=message):
        load_signal(str(write(tmp_path)))
