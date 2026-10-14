"""BIDS EMG export on a synthetic recording: sidecar values and what a round trip keeps."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import numpy as np
import pyedflib
import pytest

from muedit.io._bids_reader import load_bids_signal, reference_location
from muedit.io.bids import BidsFieldError, export_bids_emg
from muedit.signal.grid import format_hdemg_signal
from muedit.signal.segmentation import find_target_channel

FSAMP = 2000.0
N_SAMPLES = 4000
GRIDS = ["HD08MM1305"]
PLACEMENT = "Grid over the muscle belly, located by palpation."
N_EMG = len(format_hdemg_signal(GRIDS)[2][0])


def _export(root: Path, **kwargs: Any) -> dict[str, Path]:
    coordinates, ied, discard, _ = format_hdemg_signal(GRIDS)
    data = np.random.default_rng(0).normal(size=(N_EMG, N_SAMPLES)).astype(np.float32)
    kwargs.setdefault("ied", ied)
    kwargs.setdefault("placement_scheme_description", PLACEMENT)
    return export_bids_emg(data, FSAMP, GRIDS, coordinates, discard, root, **kwargs)


def _aux(n_rows: int) -> np.ndarray:
    aux = np.zeros((n_rows, N_SAMPLES), np.float32)
    aux[:, 1000:2000] = 1.0
    return aux


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def _sidecar(out: dict[str, Path]) -> dict[str, Any]:
    return json.loads(out["emg_json"].read_text(encoding="utf-8"))


def _re_export(root: Path, out: dict[str, Path]) -> dict[str, Path]:
    """Export again with the units the reader handed back, as the edit-save path does."""
    sig = load_bids_signal(str(out["edf"]))
    md = sig.metadata
    return _export(
        root,
        units=md["units"] or "uV",
        aux_data=sig.auxiliary,
        aux_names=sig.auxiliaryname,
        aux_units=md["aux_units"],
    )


def test_units_survive_a_round_trip(tmp_path: Path) -> None:
    out = _export(tmp_path / "a", units="mV", aux_data=_aux(1), aux_names=["Force"], aux_units="V")
    md = load_bids_signal(str(out["edf"])).metadata
    assert (md["units"], md["aux_units"]) == ("mV", "V")

    rows = _rows(_re_export(tmp_path / "b", out)["channels_tsv"])
    assert {r["units"] for r in rows if r["type"] == "EMG"} == {"mV"}
    assert [r["units"] for r in rows if r["type"] != "EMG"] == ["V"]


def test_differing_aux_units_survive_a_round_trip(tmp_path: Path) -> None:
    out = _export(
        tmp_path / "a",
        aux_data=_aux(3),
        aux_names=["Force", "Angle", "Marker"],
        aux_units=["N", "deg", None],
    )
    assert load_bids_signal(str(out["edf"])).metadata["aux_units"] == ["N", "deg", "n/a"]

    rows = _rows(_re_export(tmp_path / "b", out)["channels_tsv"])
    assert [r["units"] for r in rows if r["type"] != "EMG"] == ["N", "deg", "n/a"]


def test_unknown_aux_unit_is_na(tmp_path: Path) -> None:
    out = _export(tmp_path, aux_data=_aux(1), aux_names=["Force"])
    rows = _rows(out["channels_tsv"])
    assert rows[-1]["units"] == "n/a"
    reader = pyedflib.EdfReader(str(out["edf"]))
    try:
        assert reader.getPhysicalDimension(len(rows) - 1) == ""
    finally:
        reader.close()
    assert load_bids_signal(str(out["edf"])).metadata["aux_units"] is None


def test_long_aux_names_survive_a_round_trip(tmp_path: Path) -> None:
    names = ["Torque - Original Path", "Torque - Performed Path"]
    out = _export(tmp_path, aux_data=_aux(2), aux_names=names)
    assert [r["name"] for r in _rows(out["channels_tsv"])[-2:]] == [
        "Torque - Origina",
        "Torque - Perform",
    ]
    sig = load_bids_signal(str(out["edf"]))
    assert sig.auxiliaryname == names
    assert find_target_channel(sig.auxiliaryname) == 0


def test_coordinate_units_without_spacing_are_na(tmp_path: Path) -> None:
    out = _export(tmp_path, ied=None)
    coordsystem = json.loads(out["coordsystem_json_0"].read_text(encoding="utf-8"))
    assert coordsystem["EMGCoordinateUnits"] == "n/a"


def test_trigger_channels_are_counted(tmp_path: Path) -> None:
    out = _export(tmp_path, aux_data=_aux(2), aux_names=["Force", "Trig"])
    sidecar = _sidecar(out)
    assert sidecar["TriggerChannelCount"] == 1
    assert "MISCChannelCount" not in sidecar
    types = [r["type"] for r in _rows(out["channels_tsv"])]
    assert sidecar["EMGChannelCount"] == types.count("EMG")


def test_digital_lines_are_trigger_channels(tmp_path: Path) -> None:
    out = _export(
        tmp_path,
        aux_data=_aux(3),
        aux_names=["Force - Original Path", "TTL1", "DIGITAL-IN-03"],
        aux_units="V",
        aux_kinds=["target", "digital", "digital"],
    )
    rows = _rows(out["channels_tsv"])[-3:]
    assert [(r["type"], r["units"]) for r in rows] == [
        ("MISC", "V"),
        ("TRIG", "n/a"),
        ("TRIG", "n/a"),
    ]
    assert _sidecar(out)["TriggerChannelCount"] == 2

    md = load_bids_signal(str(out["edf"])).metadata
    assert md["aux_kinds"] == ["target", "digital", "digital"]
    assert md["aux_units"] == ["V", "n/a", "n/a"]


def test_aux_kinds_default_to_the_names(tmp_path: Path) -> None:
    out = _export(tmp_path, aux_data=_aux(2), aux_names=["Force - Performed Path", "Torque"])
    assert [r["type"] for r in _rows(out["channels_tsv"])[-2:]] == ["MISC", "MISC"]
    md = load_bids_signal(str(out["edf"])).metadata
    assert md["aux_kinds"] == ["performed", "analog"]


def test_no_trigger_count_without_trigger_channels(tmp_path: Path) -> None:
    sidecar = _sidecar(_export(tmp_path, aux_data=_aux(1), aux_names=["Force"]))
    assert "TriggerChannelCount" not in sidecar
    assert "MISCChannelCount" not in sidecar


def test_channels_name_their_reference(tmp_path: Path) -> None:
    out = _export(
        tmp_path,
        aux_data=_aux(1),
        aux_names=["Force"],
        reference_location="Wet band around the ankle",
        ground_location="Wet band around the wrist",
    )
    rows = _rows(out["channels_tsv"])
    assert {r["reference"] for r in rows if r["type"] == "EMG"} == {"R1"}
    assert rows[-1]["reference"] == "n/a"
    levels = json.loads(out["channels_tsv"].with_suffix(".json").read_text())["reference"]["Levels"]
    assert levels == {"R1": "Monopolar reference electrode: Wet band around the ankle"}
    sidecar = _sidecar(out)
    assert sidecar["EMGReference"] == "Wet band around the ankle"
    assert sidecar["EMGGround"] == "Wet band around the wrist"
    md = load_bids_signal(str(out["edf"])).metadata
    assert md["reference_location"] == "Wet band around the ankle"
    assert md["ground_location"] == "Wet band around the wrist"


def test_reference_without_location_is_the_electrode_name(tmp_path: Path) -> None:
    sidecar = _sidecar(_export(tmp_path))
    assert sidecar["EMGReference"] == "R1"
    assert "EMGGround" not in sidecar
    assert sidecar["EMGPlacementScheme"] == "Other"
    assert load_bids_signal(str(_export(tmp_path)["edf"])).metadata["reference_location"] is None


@pytest.mark.parametrize(
    "value",
    [
        "R1",
        "REF2",
        "monopolar",
        "Monopolar",
        "ChannelSpecific",
        "bipolar",
        "Common average",
        "n/a",
        "",
    ],
)
def test_reference_keywords_are_not_locations(value: str) -> None:
    assert reference_location(value) is None


def test_reference_location_is_kept() -> None:
    assert reference_location(" ankle ") == "ankle"


def test_electrodes_list_only_grid_electrodes(tmp_path: Path) -> None:
    """Every coordinate_system names a space- file, as the validator's EMG_COORD_SYS_MISMATCH asks."""
    out = _export(tmp_path)
    systems = {r["coordinate_system"] for r in _rows(out["electrodes_tsv"])}
    spaces = {
        p.name.split("_space-")[1].split("_")[0]
        for p in out["edf"].parent.glob("*_coordsystem.json")
    }
    assert systems <= spaces
    assert "R1" not in {r["name"] for r in _rows(out["electrodes_tsv"])}


def test_other_placement_without_description_writes_nothing(tmp_path: Path) -> None:
    with pytest.raises(BidsFieldError, match="placement description"):
        _export(tmp_path, placement_scheme_description=" ")
    assert not list(tmp_path.rglob("*_emg.*"))


def test_an_existing_sidecar_needs_no_placement_description(tmp_path: Path) -> None:
    out = _export(tmp_path)
    again = _export(tmp_path, placement_scheme_description=None, skip_existing=True)
    assert _sidecar(again)["EMGPlacementSchemeDescription"] == PLACEMENT
    assert again["emg_json"] == out["emg_json"]


_BOTH = {"HighPassFilter": {"CutOffHz": 10.0}, "LowPassFilter": {"CutOffHz": 900.0}}
_CUTOFFS: dict[str, tuple[Any, Any, Any]] = {
    "scalars": (10.0, 900.0, _BOTH),
    "shared-lists": ([10.0] * N_EMG, [900.0] * N_EMG, _BOTH),
    "high-pass-only": ([10.0] * N_EMG, None, {"HighPassFilter": {"CutOffHz": 10.0}}),
    "differing": ([10.0] * (N_EMG - 1) + [20.0], [900.0] * N_EMG, "n/a"),
    "partly-known": ([10.0] * (N_EMG - 1) + ["n/a"], None, "n/a"),
    "unknown": (None, None, "n/a"),
}


@pytest.mark.parametrize("case", list(_CUTOFFS))
def test_hardware_filters_only_when_channels_agree(tmp_path: Path, case: str) -> None:
    low, high, expected = _CUTOFFS[case]
    out = _export(tmp_path, low_cutoff=low, high_cutoff=high)
    assert _sidecar(out)["HardwareFilters"] == expected
