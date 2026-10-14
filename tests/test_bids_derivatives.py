"""Motor unit discharges as a BIDS derivative: the events file and the derivative dataset files."""

from __future__ import annotations

import csv
import importlib.metadata
import json
from pathlib import Path
from typing import Any

from muedit.io.bids import export_bids_mu_derivatives

ENTITIES = "sub-01_ses-pre_task-ramp_run-1"


def _export(root: Path, **kwargs: Any) -> dict[str, Path]:
    kwargs.setdefault("distimes", [[30, 10], [20]])
    kwargs.setdefault("mu_uids", ["mu-a", "mu-b"])
    return export_bids_mu_derivatives(fsamp=2000.0, bids_root=root, entities=ENTITIES, **kwargs)


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def _deriv(root: Path) -> Path:
    return root / "derivatives" / "muedit"


def test_one_row_per_discharge_sorted_by_onset(tmp_path: Path) -> None:
    out = _export(tmp_path, mu_grid_index=[0, 1], grid_labels=["grid1", "grid2"])
    assert out["events_tsv"].name == f"{ENTITIES}_desc-decomposition_events.tsv"
    header = out["events_tsv"].read_text(encoding="utf-8").splitlines()[0].split("\t")
    assert header == ["onset", "duration", "sample", "unit_id", "event_type", "group"]
    rows = _rows(out["events_tsv"])
    assert [(r["onset"], r["sample"], r["unit_id"], r["group"]) for r in rows] == [
        ("0.005000", "10", "mu-a", "grid1"),
        ("0.010000", "20", "mu-b", "grid2"),
        ("0.015000", "30", "mu-a", "grid1"),
    ]
    assert {(r["duration"], r["event_type"]) for r in rows} == {("0", "motor-unit-spike")}


def test_groups_default_to_the_exporter_labels(tmp_path: Path) -> None:
    rows = _rows(_export(tmp_path, mu_grid_index=[0, 1])["events_tsv"])
    assert [r["group"] for r in rows] == ["Grid1", "Grid2", "Grid1"]
    rows = _rows(_export(tmp_path / "none")["events_tsv"])
    assert {r["group"] for r in rows} == {"n/a"}


def test_every_column_is_described(tmp_path: Path) -> None:
    out = _export(tmp_path, mu_grid_index=[0, 0])
    described = json.loads(out["events_json"].read_text(encoding="utf-8"))
    header = out["events_tsv"].read_text(encoding="utf-8").splitlines()[0].split("\t")
    assert set(header) <= set(described)
    assert described["event_type"]["Levels"] == {"motor-unit-spike": "Motor unit discharge"}


def test_dataset_description_links_the_raw_dataset(tmp_path: Path) -> None:
    path = _deriv(tmp_path) / "dataset_description.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "Name": "My study, decomposed",
                "Authors": ["A. Person"],
                "GeneratedBy": [{"Name": "MUedit", "Version": "0.1"}, {"Name": "Notebook"}],
            }
        )
    )
    _export(tmp_path)
    _export(tmp_path)
    desc = json.loads(path.read_text(encoding="utf-8"))
    assert desc["Name"] == "My study, decomposed"
    assert desc["Authors"] == ["A. Person"]
    assert desc["DatasetType"] == "derivative"
    assert desc["GeneratedBy"] == [
        {
            "Name": "MUedit",
            "CodeURL": "https://github.com/simonavrillon/MUedit2",
            "Version": importlib.metadata.version("muedit"),
        },
        {"Name": "Notebook"},
    ]
    assert desc["DatasetLinks"] == {"raw": "../../"}
    assert desc["SourceDatasets"] == [{"URL": "bids:raw:"}]


def test_descriptions_tsv_names_the_desc_label(tmp_path: Path) -> None:
    path = _deriv(tmp_path) / "descriptions.tsv"
    path.parent.mkdir(parents=True)
    path.write_text("desc_id\tdescription\ndesc-preview\tfirst look\n", encoding="utf-8")
    _export(tmp_path)
    rows = {r["desc_id"]: r["description"] for r in _rows(path)}
    assert rows["desc-preview"] == "first look"
    assert rows["desc-decomposition"].startswith("Motor unit discharge times")
