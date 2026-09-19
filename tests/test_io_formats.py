"""Loader tests on the real sample recordings, one row per supported format."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from muedit.io import load_signal
from muedit.io.bids import export_bids_emg
from muedit.models import SignalImport
from muedit.signal.grid import format_hdemg_signal

# Not a whole number of seconds at any fixture's sample rate: EDF/BDF stores
# whole data records, so only a ragged count proves the reader trims the padding.
_EXPORT_SAMPLES = 19_700

_BIDS_METADATA_KEYS = (
    "device_name",
    "manufacturer",
    "gains",
    "emg_hpf",
    "emg_lpf",
    "hardware_filters",
    "recording_type",
    "coordinates",
    "ieds",
    "discard_channels",
    "emg_types",
)


@dataclass(frozen=True)
class Format:
    fixture: str
    n_channels: int
    fsamp: float
    grids: list[str] | int
    n_muscles: int | None
    metadata: dict[str, Any] = field(default_factory=dict)
    bids_metadata: bool = True


FORMATS = {
    "bdf": Format(
        "bdf_file",
        384,
        2000.0,
        ["HD08MM1305"] * 6,
        n_muscles=None,
        metadata={"bids_entity_label": "sub-1_ses-1_task-triangle_run-1"},
    ),
    "otb+": Format(
        "otb_plus_file",
        384,
        2048.0,
        ["GR08MM1305"] * 6,
        n_muscles=6,
        metadata={
            "manufacturer": "OT Bioelettronica",
            "software_versions": "OTBioLab+",
            "units": "uV",
        },
    ),
    "otb4": Format(
        "otb4_file",
        128,
        2048.0,
        ["GR04MM1305"] * 2,
        n_muscles=0,
        metadata={
            "manufacturer": "OT Bioelettronica",
            "device_name": "Quattrocento",
            "software_versions": "OTBIOLAB26",
            "units": "uV",
        },
    ),
    "intan": Format(
        "intan_dir",
        128,
        10000.0,
        2,
        n_muscles=0,
        metadata={
            "manufacturer": "Intan Technologies",
            "units": "mV",
            "intan_layout": "per_channel",
            "intan_ports": ["A", "B"],
        },
    ),
    "mat": Format(
        "real_emg_mat_file",
        64,
        2000.0,
        ["HD08MM1305"],
        n_muscles=1,
        metadata={"device_name": "Novecento", "software_versions": "MATLAB"},
        bids_metadata=False,
    ),
}


@pytest.fixture(
    scope="module",
    params=[pytest.param(name, marks=pytest.mark.data) for name in FORMATS],
)
def recording(request: pytest.FixtureRequest) -> tuple[Format, SignalImport]:
    """Each sample recording, loaded once per module."""
    fmt = FORMATS[request.param]
    path = request.getfixturevalue(fmt.fixture)
    return fmt, load_signal(str(path))


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def test_loads_expected_recording(recording: tuple[Format, SignalImport]) -> None:
    fmt, si = recording

    assert si.data.shape[0] == fmt.n_channels
    assert si.data.shape[1] > 0 and si.data.dtype == np.float64
    assert float(np.std(si.data)) > 0, "loaded EMG is flat"
    assert si.fsamp == fmt.fsamp
    if isinstance(fmt.grids, int):
        assert len(si.gridname) == fmt.grids
    else:
        assert si.gridname == fmt.grids
    if fmt.n_muscles is not None:
        assert len(si.muscle) == fmt.n_muscles
    assert si.auxiliary.shape[1] == si.data.shape[1]
    assert len(si.auxiliaryname) == si.auxiliary.shape[0]
    for key, expected in fmt.metadata.items():
        assert si.metadata[key] == expected, key


def test_grids_fit_the_data(recording: tuple[Format, SignalImport]) -> None:
    """The grid geometry ``preprocess_step`` derives covers the loaded channels."""
    _, sig = recording
    coordinates, ied, discard, emg_types = format_hdemg_signal(sig.gridname)
    n_grid = len(sig.gridname)
    assert len(coordinates) == len(ied) == len(discard) == len(emg_types) == n_grid
    for coords, mask, spacing in zip(coordinates, discard, ied, strict=True):
        assert np.asarray(coords).shape == (len(mask), 2)
        assert spacing > 0
    assert sum(len(m) for m in discard) <= sig.data.shape[0]


def test_bids_export_round_trip(recording: tuple[Format, SignalImport], tmp_path: Path) -> None:
    fmt, sig = recording
    md = sig.metadata
    if fmt.bids_metadata:
        assert set(_BIDS_METADATA_KEYS) <= set(md)
        assert len(md["coordinates"]) == len(sig.gridname)
        assert len(md["gains"]) == sig.data.shape[0]

    data = sig.data[:, :_EXPORT_SAMPLES]
    aux = sig.auxiliary[:, :_EXPORT_SAMPLES]
    coordinates, ied, discard, _ = format_hdemg_signal(sig.gridname)
    out = export_bids_emg(
        data,
        sig.fsamp,
        sig.gridname,
        coordinates,
        discard,
        tmp_path,
        ied=ied,
        subject="01",
        task="stairs",
        session="pre",
        manufacturer=md.get("manufacturer"),
        manufacturers_model_name=md.get("manufacturers_model_name"),
        powerline_freq=md.get("powerline_freq", 50.0),
        gain=md.get("gains"),
        units=md.get("units") or "uV",
        target_muscle=sig.muscle or None,
        low_cutoff=md.get("emg_hpf"),
        high_cutoff=md.get("emg_lpf"),
        hardware_filters=md.get("hardware_filters") or "n/a",
        aux_data=aux if aux.size else None,
        aux_names=sig.auxiliaryname or None,
        aux_units=md.get("aux_units"),
    )

    for key in ("edf", "emg_json", "channels_tsv", "electrodes_tsv"):
        assert out[key].exists(), key
    emg_json = json.loads(out["emg_json"].read_text(encoding="utf-8"))
    assert float(emg_json["SamplingFrequency"]) == sig.fsamp
    assert int(emg_json["EMGChannelCount"]) == data.shape[0]

    rows = _read_tsv(out["channels_tsv"])
    assert len(rows) == data.shape[0] + aux.shape[0]
    if md.get("gains"):
        written = [float(r["gain"]) for r in rows if r["type"] == "EMG"]
        assert written == [float(g) for g in md["gains"]]

    reloaded = load_signal(str(out["edf"]))
    assert reloaded.data.shape[0] == data.shape[0]
    assert reloaded.data.shape[1] == data.shape[1]
    assert reloaded.fsamp == sig.fsamp
    assert reloaded.gridname == sig.gridname


@pytest.mark.data
def test_load_decomposition_mat(decomp_mat_file: Path) -> None:
    """The v7.3 HDF5 decomposition artifact loads with its 54 motor units."""
    from muedit.decomp.decomposition_file import load_decomposition_file

    loaded = load_decomposition_file(str(decomp_mat_file))
    n_mu = len(loaded.distime_all)
    assert n_mu == 54
    assert len(loaded.pulse_trains_full) == len(loaded.mu_grid_index) == n_mu
    assert loaded.fsamp == 2000.0
    assert loaded.total_samples > 0
    for dt in loaded.distime_all:
        arr = np.asarray(dt)
        assert arr.dtype.kind in {"i", "u"}
        assert arr.size == 0 or arr.min() >= 0
