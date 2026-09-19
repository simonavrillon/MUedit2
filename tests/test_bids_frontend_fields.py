"""Frontend → backend BIDS field-passing tests for the two export routes."""

from __future__ import annotations

import csv
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from muedit.decomp.preprocess import preprocess_step
from muedit.decomp.types import DecompositionParameters, LoadStepOutput
from muedit.io import load_signal
from muedit.models import EditSignalContext, SignalImport
from muedit.signal.grid import format_hdemg_signal

FRONTEND_BIDS_INPUTS: dict[str, Any] = {
    "subject": "07",
    "task": "stairs",
    "session": "pre",
    "acquisition": "rf",
    "run": "2",
    "muscles": ["soleus", "tibialisanterior"],
    "powerline_freq": 60,
    "manufacturer": "OT Bioelettronica",
    "manufacturers_model_name": "Quattrocento",
    "placement_scheme": "Other",
    "placement_scheme_description": "Custom template placement",
    "task_description": "Stair climbing",
    "participant_age": "34",
    "participant_sex": "M",
    "participant_handedness": "right",
    "software_versions": "OTBIOLAB26",
}


def _frontend_decompose_bids_entities() -> dict[str, Any]:
    """Mirror ``collectBidsEntities`` (file-session.js:101-142)."""
    return {
        "subject": FRONTEND_BIDS_INPUTS["subject"],
        "task": FRONTEND_BIDS_INPUTS["task"],
        "session": FRONTEND_BIDS_INPUTS["session"],
        "run": FRONTEND_BIDS_INPUTS["run"],
        "target_muscle": FRONTEND_BIDS_INPUTS["muscles"],
        "powerline_freq": FRONTEND_BIDS_INPUTS["powerline_freq"],
        "manufacturer": FRONTEND_BIDS_INPUTS["manufacturer"],
        "manufacturers_model_name": FRONTEND_BIDS_INPUTS["manufacturers_model_name"],
        "placement_scheme": FRONTEND_BIDS_INPUTS["placement_scheme"],
        "placement_scheme_description": FRONTEND_BIDS_INPUTS["placement_scheme_description"],
        "task_description": FRONTEND_BIDS_INPUTS["task_description"],
        "participant_meta": {
            "age": FRONTEND_BIDS_INPUTS["participant_age"],
            "sex": FRONTEND_BIDS_INPUTS["participant_sex"],
            "handedness": FRONTEND_BIDS_INPUTS["participant_handedness"],
        },
    }


def _frontend_entity_label() -> str:
    """Mirror ``buildEntityLabelFromSession`` (io/bids.js:10-29)."""
    return (
        f"sub-{FRONTEND_BIDS_INPUTS['subject']}"
        f"_ses-{FRONTEND_BIDS_INPUTS['session']}"
        f"_task-{FRONTEND_BIDS_INPUTS['task']}"
        f"_acq-{FRONTEND_BIDS_INPUTS['acquisition']}"
        f"_run-{FRONTEND_BIDS_INPUTS['run']}"
    )


def _frontend_edit_save_payload(
    *,
    total_samples: int,
    fsamp: float,
    grid_names: list[str],
    edit_signal_token: str,
    project: str,
) -> dict[str, Any]:
    """Mirror the merged ``editSave`` body (saveEditedFile + persistNpzBySaveTarget)."""
    return {
        "distimes": [[100, 5000, 9000], [300, 7000]],
        "total_samples": total_samples,
        "fsamp": fsamp,
        "grid_names": grid_names,
        "mu_grid_index": [0, 0],
        "parameters": {"duplicatesthresh": 0.3},
        "muscle": FRONTEND_BIDS_INPUTS["muscles"],
        "file_label": "Quattrocento_edited.npz",
        "edit_signal_token": edit_signal_token,
        "software_versions": FRONTEND_BIDS_INPUTS["software_versions"],
        "entity_label": _frontend_entity_label(),
        "project": project,
        "participant_meta": {
            "age": FRONTEND_BIDS_INPUTS["participant_age"],
            "sex": FRONTEND_BIDS_INPUTS["participant_sex"],
            "handedness": FRONTEND_BIDS_INPUTS["participant_handedness"],
        },
        "powerline_freq": FRONTEND_BIDS_INPUTS["powerline_freq"],
        "manufacturer": FRONTEND_BIDS_INPUTS["manufacturer"],
        "manufacturers_model_name": FRONTEND_BIDS_INPUTS["manufacturers_model_name"],
        "placement_scheme": FRONTEND_BIDS_INPUTS["placement_scheme"],
        "placement_scheme_description": FRONTEND_BIDS_INPUTS["placement_scheme_description"],
        "task_description": FRONTEND_BIDS_INPUTS["task_description"],
        "remove_flagged": False,
        "remove_duplicates": False,
    }


def _read_emg_json(bids_root: Path) -> dict[str, Any]:
    paths = list(bids_root.rglob("*_emg.json"))
    assert paths, f"no *_emg.json under {bids_root}"
    return json.loads(paths[0].read_text(encoding="utf-8"))


def _read_channels(bids_root: Path) -> list[dict[str, str]]:
    paths = list(bids_root.rglob("*_channels.tsv"))
    assert paths, f"no *_channels.tsv under {bids_root}"
    with paths[0].open(encoding="utf-8") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def _read_participants(bids_root: Path) -> list[dict[str, str]]:
    path = bids_root / "participants.tsv"
    assert path.exists(), f"no participants.tsv under {bids_root}"
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def _emg_row(bids_root: Path) -> Path:
    paths = list(bids_root.rglob("*_emg.bdf"))
    assert paths, f"no *_emg.bdf under {bids_root}"
    return paths[0]


def _muscles_by_group(channels: list[dict[str, str]]) -> dict[str, str]:
    """Map channels.tsv ``group`` (Grid1, Grid2, ...) → target_muscle, EMG rows only."""
    groups: dict[str, str] = {}
    for r in channels:
        if r["type"] != "EMG":
            continue
        groups.setdefault(r["group"], r["target_muscle"])
    return groups


@pytest.fixture(scope="module")
def otb4_signal(otb4_file: Path) -> SignalImport:
    """Loaded OTB4 signal — the same object the preview/import route yields."""
    return load_signal(str(otb4_file))


@pytest.fixture()
def bids_data_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Redirect the API's DATA_ROOT to a tmp dir so route 2 writes to tmp."""
    import muedit.api.config as config
    import muedit.api.services.editing_service as editing_service

    monkeypatch.setattr(config, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(editing_service, "DATA_ROOT", tmp_path)
    return tmp_path


@pytest.fixture()
def api_client(bids_data_root: Path) -> Iterator[TestClient]:
    """FastAPI TestClient with all v1 routers mounted."""
    from fastapi import FastAPI

    from muedit.api.routes import include_routers

    app = FastAPI()
    include_routers(app)
    with TestClient(app) as client:
        yield client


@pytest.fixture()
def edit_signal_token(otb4_signal: SignalImport) -> str:
    """Cache a raw-signal context exactly as the edit-load route does."""
    from muedit.api.cache import _store_edit_signal_context

    sig = otb4_signal
    coordinates, ied, discard_channels, _ = format_hdemg_signal(sig.gridname)
    meta_keys = ("manufacturer", "device_name", "gains", "emg_hpf", "emg_lpf")
    ctx = EditSignalContext(
        data=sig.data,
        fsamp=sig.fsamp,
        grid_names=sig.gridname,
        coordinates=coordinates,
        emgmask=discard_channels,
        ied=ied,
        aux_data=sig.auxiliary,
        aux_names=sig.auxiliaryname,
        loader_meta={
            **{key: sig.metadata.get(key) for key in meta_keys},
            "powerline_freq": 50,
            "hardware_filters": sig.metadata.get("hardware_filters"),
            "units": sig.metadata.get("units"),
        },
    )
    return _store_edit_signal_context(ctx, file_label="Quattrocento.otb4")


def test_decompose_bids_export_before_decomposition_writes_all_frontend_fields(
    otb4_signal: SignalImport, tmp_path: Path
) -> None:
    """``preprocess_step`` writes the BIDS tree *before* ICA; every frontend ``bids_entities`` field must land in the sidecars."""
    entities = _frontend_decompose_bids_entities()
    sig = otb4_signal

    loaded = LoadStepOutput(
        full_path="Quattrocento.otb4",
        filename="Quattrocento.otb4",
        signal=sig,
        data=sig.data,
        fsamp=float(sig.fsamp),
    )
    preprocess_step(
        loaded=loaded,
        duration=None,
        manual_roi=False,
        roi=None,
        rois=None,
        params=DecompositionParameters(),
        discard_overrides=None,
        bids_root=str(tmp_path),
        bids_entities=entities,
        bids_metadata=None,
    )

    emg_json = _read_emg_json(tmp_path)
    assert emg_json["TaskName"] == entities["task"]
    emg_path = _emg_row(tmp_path)
    assert f"sub-{entities['subject']}" in emg_path.name
    assert f"ses-{entities['session']}" in emg_path.name
    assert f"task-{entities['task']}" in emg_path.name
    assert f"run-{entities['run']}" in emg_path.name
    assert emg_path.parent.name == "emg"
    assert emg_path.parent.parent.name == f"ses-{entities['session']}"
    assert emg_json["PowerLineFrequency"] == entities["powerline_freq"]
    assert emg_json["Manufacturer"] == entities["manufacturer"]
    assert emg_json["ManufacturersModelName"] == entities["manufacturers_model_name"]
    assert emg_json["EMGPlacementScheme"] == entities["placement_scheme"]
    assert emg_json["EMGPlacementSchemeDescription"] == entities["placement_scheme_description"]
    assert emg_json["TaskDescription"] == entities["task_description"]

    channels = _read_channels(tmp_path)
    grid_muscles = _muscles_by_group(channels)
    assert list(grid_muscles.values()) == entities["target_muscle"]

    participants = _read_participants(tmp_path)
    row = next(r for r in participants if r["participant_id"] == f"sub-{entities['subject']}")
    assert row["age"] == entities["participant_meta"]["age"]
    assert row["sex"] == entities["participant_meta"]["sex"]
    assert row["handedness"] == entities["participant_meta"]["handedness"]


def test_edit_save_route_writes_all_frontend_bids_fields(
    api_client: TestClient,
    otb4_signal: SignalImport,
    edit_signal_token: str,
    bids_data_root: Path,
) -> None:
    """The full ``POST /edit/save`` HTTP path with the frontend's merged payload must write BIDS sidecars echoing every frontend field."""
    sig = otb4_signal
    project = "testproj"
    payload = _frontend_edit_save_payload(
        total_samples=sig.data.shape[1],
        fsamp=float(sig.fsamp),
        grid_names=sig.gridname,
        edit_signal_token=edit_signal_token,
        project=project,
    )

    resp = api_client.post("/api/v1/edit/save", json=payload)
    assert resp.status_code == 200, resp.text
    body = resp.json()["data"]
    assert body["saved"] is True
    assert "bids_emg_paths" in body, "edit/save did not export BIDS (no cached signal context?)"
    for key in ("edf", "emg_json", "channels_tsv", "electrodes_tsv"):
        assert Path(body["bids_emg_paths"][key]).exists()

    bids_root = bids_data_root / project
    emg_json = _read_emg_json(bids_root)
    inputs = FRONTEND_BIDS_INPUTS

    emg_path = _emg_row(bids_root)
    assert emg_path.name.startswith(f"sub-{inputs['subject']}_ses-{inputs['session']}")
    assert f"task-{inputs['task']}" in emg_path.name
    assert f"acq-{inputs['acquisition']}" in emg_path.name
    assert f"run-{inputs['run']}" in emg_path.name
    assert emg_json["TaskName"] == inputs["task"]

    assert emg_json["PowerLineFrequency"] == inputs["powerline_freq"]
    assert emg_json["Manufacturer"] == inputs["manufacturer"]
    assert emg_json["ManufacturersModelName"] == inputs["manufacturers_model_name"]
    assert emg_json["EMGPlacementScheme"] == inputs["placement_scheme"]
    assert emg_json["EMGPlacementSchemeDescription"] == inputs["placement_scheme_description"]
    assert emg_json["TaskDescription"] == inputs["task_description"]
    assert emg_json["SoftwareVersions"] == inputs["software_versions"]

    channels = _read_channels(bids_root)
    grid_muscles = _muscles_by_group(channels)
    assert list(grid_muscles.values()) == inputs["muscles"]

    participants = _read_participants(bids_root)
    row = next(r for r in participants if r["participant_id"] == f"sub-{inputs['subject']}")
    assert row["age"] == inputs["participant_age"]
    assert row["sex"] == inputs["participant_sex"]
    assert row["handedness"] == inputs["participant_handedness"]
