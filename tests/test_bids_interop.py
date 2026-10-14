"""Reading EMG-BIDS datasets MUedit did not write: the MUnitQuest tutorial layout."""

from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path

import numpy as np
import pyedflib
import pytest

from muedit.io._bids_reader import (
    load_bids_emg_grid,
    load_bids_signal,
    read_bids_emg_grid,
    read_channels_summary,
)
from muedit.io.bids import export_bids_emg
from muedit.io.store import RamStore
from muedit.models import SignalImport
from muedit.signal import grid
from muedit.signal.grid import GridSpec

FIXTURE = Path(__file__).parent / "bids_fixtures" / "munitquest"
SUB01 = "sub-01/emg/sub-01_task-isometric30percentMVC_run-01"
SUB02 = "sub-02/ses-01/emg/sub-02_ses-01_task-isometric30percentMVC_run-01"
# Catalogue entries a user adds for the tutorial's grids, channels numbered row by row.
TUTORIAL_GRIDS = {
    "MQ8MM0304": GridSpec(
        np.arange(1, 13).reshape(3, 4), 12, 8.0, 1, "n/a", "surface array", "n/a"
    ),
    "MQ8MM0303": GridSpec(np.arange(1, 10).reshape(3, 3), 9, 8.0, 1, "n/a", "surface array", "n/a"),
    "MQWIRE": GridSpec(np.ones((1, 1), dtype=int), 1, 0.0, 2, "n/a", "intramuscular", "n/a"),
}
GRID_NAMES = {
    SUB01: {"grid1": "MQ8MM0304", "intramuscular": "MQWIRE"},
    SUB02: {"grid1": "MQ8MM0303", "grid2": "MQ8MM0303"},
}


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def _trace(row: int, n_samples: int) -> np.ndarray:
    """Channels.tsv row ``row`` carries ``10 * (row + 1)`` plus a small wave, so a loaded row names its source."""
    return 10.0 * (row + 1) + np.sin(np.arange(n_samples) / 50.0)


def _source_rows(data: np.ndarray) -> list[int]:
    """The channels.tsv row each loaded channel came from."""
    return [int(round(float(np.mean(row)) / 10.0)) - 1 for row in data]


def _write_edf(root: Path, stem: str) -> None:
    rows = _rows(root / f"{stem}_channels.tsv")
    fsamp = int(json.loads((root / f"{stem}_emg.json").read_text())["SamplingFrequency"])
    writer = pyedflib.EdfWriter(
        str(root / f"{stem}_emg.edf"), n_channels=len(rows), file_type=pyedflib.FILETYPE_EDFPLUS
    )
    try:
        writer.setSignalHeaders(
            [
                {
                    "label": row["name"],
                    "dimension": row["units"],
                    "sample_frequency": fsamp,
                    "physical_min": -500.0,
                    "physical_max": 500.0,
                    "digital_min": -32768,
                    "digital_max": 32767,
                    "transducer": "",
                    "prefilter": "",
                }
                for row in rows
            ]
        )
        # 40 s: long enough for the task phases the tutorial's events.tsv lists.
        writer.writeSamples([_trace(i, 40 * fsamp) for i in range(len(rows))])
    finally:
        writer.close()


@pytest.fixture
def dataset(tmp_path: Path) -> Path:
    root = tmp_path / "munitquest"
    shutil.copytree(FIXTURE, root)
    for stem in (SUB01, SUB02):
        _write_edf(root, stem)
    return root


@pytest.fixture
def named(dataset: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The tutorial once its grids are in the catalogue and named in ``grid_name``."""
    for key, spec in TUTORIAL_GRIDS.items():
        monkeypatch.setitem(grid._GRID_CATALOG, key, spec)
    for stem, names in GRID_NAMES.items():
        path = dataset / f"{stem}_channels.tsv"
        header, *body = path.read_text(encoding="utf-8").splitlines()
        group = header.split("\t").index("group")
        lines = [f"{header}\tgrid_name"]
        lines += [line + "\t" + names.get(line.split("\t")[group], "n/a") for line in body]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return dataset


def _load(root: Path, stem: str) -> SignalImport:
    return load_bids_signal(str(root / f"{stem}_emg.edf"))


def test_grids_missing_from_the_catalogue_raise(dataset: Path) -> None:
    with pytest.raises(ValueError, match=r"group 'grid1'.*_GRID_CATALOG"):
        _load(dataset, SUB02)


def test_unknown_grid_name_is_named_in_the_error(named: Path) -> None:
    path = named / f"{SUB02}_channels.tsv"
    path.write_text(path.read_text(encoding="utf-8").replace("MQ8MM0303", "GRX"), encoding="utf-8")
    with pytest.raises(ValueError, match="unknown model 'GRX'"):
        _load(named, SUB02)


def test_two_grids_load_in_channels_order(named: Path) -> None:
    sig = _load(named, SUB02)
    assert sig.gridname == ["MQ8MM0303", "MQ8MM0303"]
    assert sig.muscle == ["right tibialis anterior"] * 2
    assert sig.auxiliaryname == ["Torque"]
    assert (sig.metadata["units"], sig.metadata["aux_units"]) == ("uV", "Nm")
    assert _source_rows(sig.data) == list(range(18))


def test_intramuscular_wire_is_its_own_grid(named: Path) -> None:
    sig = _load(named, SUB01)
    assert sig.gridname == ["MQ8MM0304", "MQWIRE"]
    assert sig.metadata["emg_types"] == [1, 2]
    assert sig.data.shape[0] == 13
    assert _source_rows(sig.data[12:]) == [12]


def test_single_grid_readers_match_the_full_load(named: Path) -> None:
    sig = _load(named, SUB02)
    entity = Path(SUB02).name
    for index in (0, 1):
        expected = sig.data[9 * index : 9 * (index + 1)]
        windowed, fsamp, mask = load_bids_emg_grid(named, entity, index)
        whole, _, _ = read_bids_emg_grid(named, entity, index, RamStore())
        assert fsamp == sig.fsamp
        assert mask.tolist() == [0] * 9
        np.testing.assert_allclose(windowed, expected, atol=1e-4)
        np.testing.assert_array_equal(whole, expected)


def test_channels_tsv_rows_read_their_own_edf_signals(named: Path) -> None:
    path = named / f"{SUB02}_channels.tsv"
    header, *body = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join([header, *reversed(body)]) + "\n", encoding="utf-8")
    sig = _load(named, SUB02)
    # Matched by label: row EMG009, now first in grid1, reads the EDF signal EMG009.
    assert _source_rows(sig.data) == [*range(8, -1, -1), *range(17, 8, -1)]
    assert sig.gridname == ["MQ8MM0303", "MQ8MM0303"]


def test_sidecars_inherit_from_the_dataset_root(named: Path) -> None:
    (named / "task-isometric30percentMVC_emg.json").write_text(
        json.dumps({"SkinPreparation": "shaved", "Instructions": "from the root"})
    )
    md = _load(named, SUB01).metadata
    assert md["skin_preparation"] == "shaved"
    assert md["instructions"].startswith("Sit upright")  # the recording's own value wins


def test_tutorial_phases_become_segments(named: Path) -> None:
    segments = _load(named, SUB02).metadata["segments"]
    assert [(s["kind"], s["direction"], s["start"], s["end"]) for s in segments] == [
        ("ramp", "up", 10240, 20480),
        ("plateau", None, 20480, 61440),
        ("ramp", "down", 61440, 71680),
    ]
    assert segments[1]["level_start"] == segments[1]["level_end"] == 30.0


def test_channels_sidecar_gives_no_grid_names_without_models(dataset: Path) -> None:
    grid_names, muscles, _ = read_channels_summary(dataset / f"{SUB02}_channels.tsv")
    assert grid_names == []
    assert muscles == ["right tibialis anterior"] * 2


def test_muedit_export_of_a_foreign_dataset_reads_back(named: Path, tmp_path: Path) -> None:
    sig = _load(named, SUB02)
    md = sig.metadata
    out = export_bids_emg(
        sig.data,
        sig.fsamp,
        sig.gridname,
        md["coordinates"],
        md["discard_channels"],
        tmp_path / "export",
        ied=md["ieds"],
        placement_scheme_description="Grid over the muscle belly, located by palpation.",
        units=md["units"],
        aux_data=sig.auxiliary,
        aux_names=sig.auxiliaryname,
        aux_units=md["aux_units"],
        grid_labels=md["grid_labels"],
    )
    assert md["grid_labels"] == ["grid1", "grid2"]
    groups = [row["group"] for row in _rows(out["channels_tsv"]) if row["type"] == "EMG"]
    assert groups == ["grid1"] * 9 + ["grid2"] * 9
    again = load_bids_signal(str(out["edf"]))
    assert again.gridname == sig.gridname
    assert again.auxiliaryname == ["Torque"]
    np.testing.assert_allclose(again.data, sig.data, atol=1e-2)
