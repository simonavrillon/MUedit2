"""Importing OT Bioelettronica archives (``io/_otb.py``): OTB+ (OTBioLab+) and OTB4 (OTBioLab 2.6)."""

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
    OTB4_FEEDBACK,
    OTB_FSAMP,
    OTB_N_SAMPLES,
    OTB_PLUS_N_EMG,
    OTB_PLUS_SIPS,
    Expected,
    Write,
    check_signal,
    otb4_frames,
    otb_plus_frames,
    write_not_an_archive,
    write_otb4,
    write_otb_plus,
)

# ── OTB+ ─────────────────────────────────────────────────────────────────────

#: Quattrocento counts to mV (16 bits over 5 V at gain 150), whatever serial follows the name.
QUATTROCENTO_MV = 0.00050863


def _otb_plus_synthetic() -> Expected:
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


_OTB_PLUS_FILES: dict[str, tuple[Write, Expected, str]] = {
    "synthetic": (lambda _, tmp: write_otb_plus(tmp / "rec.otb+"), _otb_plus_synthetic(), ""),
    "two-signal-files": (
        lambda _, tmp: write_otb_plus(tmp / "rec.otb+", extra_sig=True),
        _otb_plus_synthetic(),
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


def _otb_plus_without(member: str) -> Callable[[Path], Path]:
    def write(tmp_path: Path) -> Path:
        source = write_otb_plus(tmp_path / "full.otb+")
        path = tmp_path / "rec.otb+"
        with tarfile.open(source) as full, tarfile.open(path, "w") as tar:
            for info in full.getmembers():
                if not info.name.endswith(member):
                    tar.addfile(info, full.extractfile(info))
        return path

    return write


def _otb_plus_ragged_signal(tmp_path: Path) -> Path:
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


_OTB_PLUS_BAD_FILES: dict[str, tuple[Callable[[Path], Path], str]] = {
    "not-an-archive": (
        lambda tmp: write_not_an_archive(tmp, "rec.otb+"),
        "Failed to extract OTB\\+ file",
    ),
    "no-signal-file": (_otb_plus_without(".sig"), r"No \.sig file"),
    "no-xml-of-its-signal": (
        _otb_plus_without("_03.xml"),
        r"Could not find XML file: 20230130115031_03\.xml",
    ),
    "signal-not-whole-frames": (_otb_plus_ragged_signal, "into 130 channels"),
}


class TestOtbPlus:
    """OTB+ archives (OTBioLab+)."""

    @pytest.mark.parametrize(
        "case",
        [pytest.param(c, marks=pytest.mark.data) if c == "sample" else c for c in _OTB_PLUS_FILES],
    )
    def test_loads_otb_plus(
        self,
        request: pytest.FixtureRequest,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
        case: str,
    ) -> None:
        write, expected, warning = _OTB_PLUS_FILES[case]
        with caplog.at_level(logging.WARNING, logger="muedit.io._otb"):
            check_signal(load_signal(str(write(request, tmp_path))), expected)
        assert warning in caplog.text

    @pytest.mark.parametrize("case", list(_OTB_PLUS_BAD_FILES))
    def test_refuses_bad_otb_plus(self, tmp_path: Path, case: str) -> None:
        write, message = _OTB_PLUS_BAD_FILES[case]
        with pytest.raises(SignalLoadError, match=message):
            load_signal(str(write(tmp_path)))


# ── OTB4 ─────────────────────────────────────────────────────────────────────

OTB4_N_EMG = 128


def _otb4_feedback_rows() -> dict[int, np.ndarray]:
    """The 10 Hz feedback traces on the EMG clock: linear between samples, then held."""
    t = np.arange(OTB_N_SAMPLES) / OTB_FSAMP
    t_in = np.arange(len(OTB4_FEEDBACK)) / 10.0
    return {
        1 + col: np.interp(t, t_in, OTB4_FEEDBACK[:, col]).astype(np.float32) for col in range(2)
    }


def _otb4_synthetic(device: str) -> Expected:
    novecento = device == "Novecento+"
    if novecento:
        # 24-bit counts in int32 files, one per stream, scaled in float32.
        scale = float(np.float32(5 / 2**24 * 1000 / 256))
        grids = otb4_frames(np.int32, 128).T.astype(np.float32) * scale
        aux = otb4_frames(np.int32, 1)[:, 0].astype(np.float32) * scale
    else:
        # 16-bit counts, every track in one int16 file, scaled in float64.
        scale = 5 / 2**16 * 1000 / 256
        frames = otb4_frames(np.int16, 130).astype(np.float64)
        grids = (frames[:, :OTB4_N_EMG].T * scale).astype(np.float32)
        aux = (frames[:, OTB4_N_EMG] * scale).astype(np.float32)
    return Expected(
        shape=(OTB4_N_EMG, OTB_N_SAMPLES),
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
            "gains": [256.0] * OTB4_N_EMG,
            "emg_hpf": [10.0] * OTB4_N_EMG,
            "emg_lpf": [512.0] * OTB4_N_EMG,
            "aux_gains": [256.0, 1.0, 1.0],
            "aux_hpf": [10.0 if novecento else "n/a", "n/a", "n/a"],
        },
        data=grids,
        aux_rows={0: aux, **_otb4_feedback_rows()},
    )


def _otb4_sample(fixture: str, expected: Expected) -> tuple[Callable[..., Path], Expected]:
    return (lambda request, _: request.getfixturevalue(fixture)), expected


_OTB4_FILES: dict[str, tuple[Write, Expected]] = {
    "quattrocento": (
        lambda _, tmp: write_otb4(tmp / "q.otb4", "Quattrocento"),
        _otb4_synthetic("Quattrocento"),
    ),
    "novecento": (
        lambda _, tmp: write_otb4(tmp / "n.otb4", "Novecento+"),
        _otb4_synthetic("Novecento+"),
    ),
    "sample-quattrocento": _otb4_sample(
        "otb4_file",
        Expected(
            shape=(128, 93_952),
            fsamp=2048.0,
            grid_names=["GR04MM1305"] * 2,
            metadata={"device_name": "Quattrocento", "acquisition_date": "2026-03-26T14:31:26"},
        ),
    ),
    "sample-novecento": _otb4_sample(
        "novecento_otb4_file",
        Expected(
            shape=(384, 57_400),
            fsamp=2000.0,
            grid_names=["HD08MM1305"] * 6,
            metadata={"device_name": "Novecento+", "acquisition_date": None},
        ),
    ),
    "sample-trapezoid": _otb4_sample(
        "passiflore_otb4_file",
        Expected(
            shape=(384, 705_536),
            fsamp=2048.0,
            grid_names=["GR08MM1305"] * 6,
            metadata={"acquisition_date": "2026-01-29T09:40:31"},
        ),
    ),
}


def _otb4_without(device: str, member: str) -> Callable[[Path], Path]:
    def write(tmp_path: Path) -> Path:
        source = write_otb4(tmp_path / "full.otb4", device)
        path = tmp_path / "rec.otb4"
        with tarfile.open(source) as full, tarfile.open(path, "w") as tar:
            for info in full.getmembers():
                if info.name != member:
                    tar.addfile(info, full.extractfile(info))
        return path

    return write


_OTB4_BAD_FILES: dict[str, tuple[Callable[[Path], Path], str]] = {
    "not-an-archive": (
        lambda tmp: write_not_an_archive(tmp, "rec.otb4"),
        "Failed to extract OTB4 file",
    ),
    "no-track-list": (_otb4_without("Quattrocento", "Tracks_000.xml"), r"No Tracks_000\.xml"),
    "no-signal-file": (
        _otb4_without("Quattrocento", "20260326143126.sig"),
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


class TestOtb4:
    """OTB4 archives (OTBioLab 2.6): Novecento+ and the other devices."""

    @pytest.mark.parametrize(
        "case",
        [
            pytest.param(c, marks=pytest.mark.data) if c.startswith("sample") else c
            for c in _OTB4_FILES
        ],
    )
    def test_loads_otb4(self, request: pytest.FixtureRequest, tmp_path: Path, case: str) -> None:
        write, expected = _OTB4_FILES[case]
        check_signal(load_signal(str(write(request, tmp_path))), expected)

    @pytest.mark.parametrize("case", list(_OTB4_BAD_FILES))
    def test_refuses_bad_otb4(self, tmp_path: Path, case: str) -> None:
        write, message = _OTB4_BAD_FILES[case]
        with pytest.raises(SignalLoadError, match=message):
            load_signal(str(write(tmp_path)))
