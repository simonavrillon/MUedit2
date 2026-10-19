"""Importing an Open Ephys GUI binary-format recording."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import numpy as np
import pytest

from muedit.errors import SignalLoadError
from muedit.io import load_signal
from tests._io_files import (
    OE_ADC_STEP,
    OE_EMG_STEP,
    OE_FSAMP,
    OE_N_ADC,
    OE_N_EMG,
    OE_N_SAMPLES,
    OE_STREAM,
    Expected,
    Write,
    check_signal,
    openephys_raw,
    write_grid_sidecar,
    write_openephys_record_node,
    write_openephys_recording,
)


def _synthetic(grid_names: list[str], muscle_names: list[str]) -> Expected:
    raw = openephys_raw().astype(np.float64)
    ttl1 = np.zeros(OE_N_SAMPLES, np.float32)
    ttl1[1000:2500] = 1
    ttl3 = np.zeros(OE_N_SAMPLES, np.float32)
    ttl3[4000:] = 1
    return Expected(
        shape=(OE_N_EMG, OE_N_SAMPLES),
        fsamp=OE_FSAMP,
        grid_names=grid_names,
        muscle_names=muscle_names,
        aux_names=["ADC1", "ADC2", "TTL1", "TTL3"],
        metadata={
            "acquisition_date": "2026-08-17T16:31:21.641+00:00",
            "manufacturer": "Open Ephys",
            "device_name": "Acquisition Board",
            "software_versions": "Open Ephys GUI 1.0.2",
            "hardware_filters": ["HP: 2.50 Hz, LP: 7600.00 Hz, Notch: none"],
            "units": "mV",
            "emg_hpf": [2.5] * OE_N_EMG,
            "emg_lpf": [7600.0] * OE_N_EMG,
            "aux_units": "V",
            "aux_kinds": ["analog", "analog", "digital", "digital"],
        },
        # Ephys counts to mV, ADC counts to V, in float64, rounded once to float32.
        data=(raw[:, :OE_N_EMG].T * (OE_EMG_STEP * 1e-3)).astype(np.float32),
        aux_rows={
            0: (raw[:, OE_N_EMG] * OE_ADC_STEP).astype(np.float32),
            OE_N_ADC: ttl1,
            OE_N_ADC + 1: ttl3,
        },
    )


def _sidecar_on_record_node(tmp_path: Path) -> Path:
    """One sidecar beside settings.xml names the grids of every recording below it."""
    recording = write_openephys_record_node(tmp_path)
    write_grid_sidecar(recording.parent.parent, ["GR10MM0804"], ["ta"])
    return recording


_FILES: dict[str, tuple[Write, Expected]] = {
    # 64 ephys channels and no headstage list: the Intan 64-channel adapter.
    "recording-folder": (
        lambda _, tmp: write_openephys_record_node(tmp),
        _synthetic(["INTAN64-1305"], []),
    ),
    "structure-file": (
        lambda _, tmp: write_openephys_record_node(tmp) / "structure.oebin",
        _synthetic(["INTAN64-1305"], []),
    ),
    # Two 32-channel headstages in settings.xml: one Myomatrix thread each.
    "headstages": (
        lambda _, tmp: write_openephys_record_node(tmp, headstages=True),
        _synthetic(["MYOMNP-1x32"] * 2, []),
    ),
    "grid-sidecar": (
        lambda _, tmp: _sidecar_on_record_node(tmp),
        _synthetic(["GR10MM0804"] * 2, ["ta", "ta"]),
    ),
    "sample": (
        lambda request, _: request.getfixturevalue("openephys_dir"),
        Expected(
            shape=(64, 440_076),
            fsamp=20000.0,
            grid_names=["MYOMNP-1x32"] * 2,
            muscle_names=["biceps brachii lateral", "biceps brachii medial"],
            metadata={
                "manufacturer": "Open Ephys",
                "device_name": "Acquisition Board",
                "software_versions": "Open Ephys GUI 1.0.2",
                "units": "mV",
            },
        ),
    ),
}


@pytest.mark.parametrize(
    "case",
    [pytest.param(c, marks=pytest.mark.data) if c == "sample" else c for c in _FILES],
)
def test_loads_openephys(request: pytest.FixtureRequest, tmp_path: Path, case: str) -> None:
    write, expected = _FILES[case]
    check_signal(load_signal(str(write(request, tmp_path))), expected)


def _no_structure_file(tmp_path: Path) -> Path:
    folder = tmp_path / "recording"
    folder.mkdir()
    return folder


def _bad_structure_file(tmp_path: Path) -> Path:
    recording = write_openephys_record_node(tmp_path)
    (recording / "structure.oebin").write_text("{not json", encoding="utf-8")
    return recording


def _no_data_file(tmp_path: Path) -> Path:
    recording = write_openephys_record_node(tmp_path)
    (recording / "continuous" / OE_STREAM / "continuous.dat").unlink()
    return recording


def _truncated(tmp_path: Path) -> Path:
    recording = write_openephys_record_node(tmp_path)
    with open(recording / "continuous" / OE_STREAM / "continuous.dat", "ab") as handle:
        handle.write(b"\x00\x01")
    return recording


def _grids_cover_too_many(tmp_path: Path) -> Path:
    recording = write_openephys_record_node(tmp_path)
    write_grid_sidecar(recording, ["GR10MM0804"] * 4, [])
    return recording


def _unknown_channel_count(tmp_path: Path) -> Path:
    recording = tmp_path / "experiment1" / "rec"
    write_openephys_recording(recording, openephys_raw(), ttl=[], n_ephys=40)
    return recording


_BAD_FILES: dict[str, tuple[Callable[[Path], Path], str]] = {
    "no-structure-file": (_no_structure_file, r"structure\.oebin"),
    "unreadable-structure-file": (_bad_structure_file, "Cannot read Open Ephys structure file"),
    "no-data-file": (_no_data_file, r"Missing continuous\.dat"),
    "truncated": (_truncated, "not a whole number"),
    "grids-cover-too-many-channels": (_grids_cover_too_many, "cover 128 electrodes"),
    "grids-not-known": (_unknown_channel_count, "records one"),
}


@pytest.mark.parametrize("case", list(_BAD_FILES))
def test_refuses_bad_openephys(tmp_path: Path, case: str) -> None:
    write, message = _BAD_FILES[case]
    with pytest.raises(SignalLoadError, match=message):
        load_signal(str(write(tmp_path)))
