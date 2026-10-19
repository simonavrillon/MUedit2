"""The run and edit save paths: what each writes, where, and how each fails."""

from __future__ import annotations

import csv
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import scipy.io
from httpx import Response
from starlette.testclient import TestClient

from muedit.api import cache
from muedit.api.binary import unpack_frame
from muedit.api.schemas import DecomposePayload
from muedit.api.services import decompose_service, editing_service
from muedit.decomp.decomposition_file import save_decomposition_npz
from muedit.decomp.pipeline import run_decomposition
from muedit.decomp.postprocess import dedup_survivors
from muedit.decomp.types import DecompositionParameters
from muedit.io import bids
from muedit.io.bids import (
    BidsFieldError,
    bids_emg_fields,
    check_run_export,
    export_bids_emg,
    form_over_loader_meta,
    run_decomp_path,
    write_participant,
)
from muedit.signal.grid import format_hdemg_signal
from tests._synthetic_emg import motor_unit_emg

API = "/api/v1"
TAB = {"X-MUedit-Session": "tab-save"}
FSAMP = 2000.0
N_SAMPLES = 4000
GRIDS = ["HD08MM1305"]
N_EMG = len(format_hdemg_signal(GRIDS)[2][0])
PLACEMENT = "Grid over the muscle belly, located by palpation."
LABEL = "sub-01_task-save"


def _spikes(seed: int) -> list[int]:
    rng = np.random.default_rng(seed)
    return sorted(rng.choice(np.arange(100, N_SAMPLES - 100), 40, replace=False).tolist())


def _decomp(path: Path, *, emg: bool = False, **kwargs: Any) -> Path:
    """A two-MU decomposition, with the raw EMG and its loader fields when ``emg``."""
    coordinates, _, discard, _ = format_hdemg_signal(GRIDS)
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: dict[str, Any] = {
        "pulse_trains": None,
        "spike_times": [_spikes(1), _spikes(2)],
        "fsamp": FSAMP,
        "grid_names": GRIDS,
        "mu_grid_index": [0, 0],
        "muscle_names": ["TA"],
        "parameters": {},
        "total_samples": N_SAMPLES,
        "sil": [0.91, 0.87],
        "rois": [(0, N_SAMPLES)],
    }
    if emg:
        rng = np.random.default_rng(0)
        fields |= {
            "emg_data": rng.normal(size=(N_EMG, N_SAMPLES)).astype(np.float32),
            "discard_channels": discard,
            "coordinates": coordinates,
            "loader_meta": {"units": "mV", "recording_type": "continuous"},
        }
    save_decomposition_npz(path, **(fields | kwargs))
    return path


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f, delimiter="\t"))


@pytest.fixture()
def api(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[TestClient]:
    import muedit.api.config as config
    from muedit.api.app_factory import create_app
    from muedit.api.routes import include_routers

    monkeypatch.setenv("MUEDIT_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path / "data")
    app = create_app()
    include_routers(app)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


def _open(api: TestClient, path: Path) -> tuple[str, Response]:
    resp = api.post(f"{API}/edit/session/open", json={"path": str(path)}, headers=TAB)
    token = unpack_frame(resp.content)[0]["token"] if resp.status_code == 200 else ""
    return token, resp


def _save(api: TestClient, token: str, **form: Any) -> Response:
    body = {"token": token, "project": "proj", "entity_label": LABEL, **form}
    return api.post(f"{API}/edit/session/save", json=body, headers=TAB)


def _run_save(api: TestClient, **body: Any) -> Response:
    payload = {
        "spike_times": [_spikes(1), _spikes(2)],
        "total_samples": N_SAMPLES,
        "fsamp": FSAMP,
        "project": "proj",
        "entity_label": LABEL,
        **body,
    }
    return api.post(f"{API}/edit/save", json=payload)


# ── A2-01, A2-16: grid indices and dedup order ──────────────────────────────


def test_a_negative_grid_index_is_refused_rather_than_dropping_its_mus(
    api: TestClient, tmp_path: Path
) -> None:
    resp = _run_save(api, mu_grid_index=[0, -1])
    assert resp.status_code == 422, resp.text
    _, opened = _open(api, _decomp(tmp_path / "neg_decomp.npz", mu_grid_index=[-1, -1]))
    assert opened.status_code == 400
    assert "negative grid index" in opened.json()["error"]["message"]
    with pytest.raises(ValueError, match="outside"):
        dedup_survivors(
            [np.array([1]), np.array([2])], [0, 1], 1, DecompositionParameters(), FSAMP, 10
        )


def test_dedup_keeps_survivors_in_input_order() -> None:
    a = np.array(_spikes(1))
    b = np.array(_spikes(2))
    # Index 1 duplicates index 0 with a steadier rhythm, so it is the one kept of the pair.
    steady = np.arange(200, N_SAMPLES - 200, 100)
    kept, grids = dedup_survivors(
        [np.unique(np.r_[steady, steady[::3] + 7]), steady, a, b],
        [0, 0, 1, 1],
        2,
        DecompositionParameters(duplicatesbgrids=False),
        FSAMP,
        N_SAMPLES,
    )
    assert kept == sorted(kept) and 1 in kept and 0 not in kept
    assert grids == [0, 1, 1]


# ── A2-02, A2-03, A2-04, A2-13, A2-15: what an edited file keeps ────────────


def test_a_run_save_keeps_the_runs_segments_sil_and_windows(api: TestClient) -> None:
    segments = {"target_index": 0, "target_name": "Force", "segments": []}
    token = cache._store_run_result(
        np.zeros((2, N_SAMPLES), dtype=np.float32),
        spike_times=[np.array(_spikes(1)), np.array(_spikes(2))],
        extras=cache.RunExtras(sil=[0.9, 0.8], rois=[(0, 2000)], target_segments=segments),
    )
    resp = _run_save(api, spike_times=None, run_result_token=token, remove_duplicates=False)
    assert resp.status_code == 200, resp.text
    with np.load(resp.json()["data"]["path"]) as z:
        assert json.loads(str(z["target_segments"])) == segments
        assert z["sil"].tolist() == [0.9, 0.8]
        assert z["rois"].tolist() == [[0, 2000]]


def test_a_session_save_keeps_what_the_file_held(api: TestClient, tmp_path: Path) -> None:
    token, _ = _open(api, _decomp(tmp_path / f"{LABEL}_decomp.npz", emg=True))
    resp = _save(
        api,
        token,
        remove_duplicates=False,
        manufacturer="Acme",
        units="uV",
        placement_scheme_description=PLACEMENT,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    out = Path(data["path"])
    with np.load(out) as z:
        assert json.loads(str(z["muscle_names"])) == ["TA"]  # the form named none
        assert z["sil"].tolist() == [0.91, 0.87]
        assert z["rois"].tolist() == [[0, N_SAMPLES]]
        meta = json.loads(str(z["loader_meta"]))
    assert meta["manufacturer"] == "Acme" and meta["units"] == "uV"  # the form over the loader
    assert "artifact_times" not in json.loads(out.with_suffix(".json").read_text())
    sidecar = json.loads(Path(data["bids_emg_paths"]["emg_json"]).read_text())
    assert sidecar["Muscles"] == ["TA"] and sidecar["RecordingType"] == "continuous"
    assert {r["units"] for r in _rows(Path(data["bids_emg_paths"]["channels_tsv"]))} == {"uV"}
    edit = cache._get_edit_session(token)
    assert edit is not None and edit.meta["file_label"] == out.name
    assert edit.meta["source_path"] == str(out)


def test_a_new_mu_leaves_the_sil_out(api: TestClient, tmp_path: Path) -> None:
    token, _ = _open(api, _decomp(tmp_path / f"{LABEL}_decomp.npz"))
    api.post(f"{API}/edit/ops/duplicate", json={"token": token, "mu": 0}, headers=TAB)
    resp = _save(api, token, remove_duplicates=False)
    with np.load(resp.json()["data"]["path"]) as z:
        assert "sil" not in z.files


def test_a_save_into_the_files_dataset_keeps_its_subject(api: TestClient, tmp_path: Path) -> None:
    dataset = tmp_path / "data" / "study"
    path = dataset / "derivatives" / "muedit" / "sub-01" / "decomp" / f"{LABEL}_decomp.npz"
    token, _ = _open(api, _decomp(path))
    refused = _save(api, token, project="study", entity_label="sub-02_task-save")
    assert refused.status_code == 400
    assert refused.json()["error"]["detail"]["field"] == "entity_label"
    assert not (dataset / "derivatives" / "muedit" / "sub-02").exists()
    elsewhere = _save(api, token, project="other", entity_label="sub-02_task-save")
    assert elsewhere.status_code == 200, elsewhere.text


# ── A2-05: one BIDS field assembly ──────────────────────────────────────────


def test_the_form_fields_override_the_loader_fields_once() -> None:
    meta = form_over_loader_meta(
        {"units": "mV", "device_name": "Quattrocento", "recording_type": "epoched"},
        {"units": "uV", "manufacturers_model_name": "", "manufacturer": "OT"},
    )
    fields = bids_emg_fields(meta, ["TA", "VL"])
    assert fields["units"] == "uV"
    assert fields["manufacturers_model_name"] == "Quattrocento"  # a blank form field
    assert fields["manufacturer"] == "OT"
    assert fields["recording_type"] == "epoched"
    assert fields["target_muscle"] == ["TA", "VL"]
    assert fields["emg_json_extra"] == {"Muscles": ["TA", "VL"]}
    assert bids_emg_fields({}, "TA")["target_muscle"] == "TA"


# ── A2-06: failure semantics ────────────────────────────────────────────────


def test_a_run_with_a_bad_bids_field_is_400_before_it_starts(tmp_path: Path) -> None:
    payload = DecomposePayload.model_validate(
        {"upload_token": "t", "bids_export": True, "bids_entities": {"subject": "01"}}
    )
    with pytest.raises(BidsFieldError) as refused:
        decompose_service._check_bids_fields(payload, {}, tmp_path)
    assert refused.value.field == "bids_entities"  # the API answers it with a 400
    bad_age = {"placement_scheme_description": PLACEMENT, "participant_meta": {"age": "abc"}}
    with pytest.raises(BidsFieldError, match="age"):
        check_run_export(tmp_path, bad_age, {})
    check_run_export(tmp_path, {}, {"placement_scheme_description": PLACEMENT})


def test_a_failed_edf_write_leaves_no_signal_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*_: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(bids, "_write_records", fail)
    coordinates, ied, discard, _ = format_hdemg_signal(GRIDS)
    data = np.zeros((N_EMG, N_SAMPLES), dtype=np.float32)
    with pytest.raises(OSError, match="disk full"):
        export_bids_emg(
            data, FSAMP, GRIDS, coordinates, discard, tmp_path, ied=ied,
            placement_scheme_description=PLACEMENT,
        )  # fmt: skip
    assert not [p for p in tmp_path.rglob("*") if p.suffix in (".edf", ".bdf")]


def test_side_output_failures_are_reported_and_keep_the_rest(
    api: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*_: Any, **__: Any) -> None:
        raise OSError("read-only")

    monkeypatch.setattr(editing_service, "export_bids_events", fail)
    monkeypatch.setattr(editing_service, "write_participant", fail)
    token, _ = _open(api, _decomp(tmp_path / f"{LABEL}_decomp.npz", emg=True))
    data = _save(api, token, placement_scheme_description=PLACEMENT).json()["data"]
    assert "bids_emg_error" not in data
    assert Path(data["bids_emg_paths"]["edf"]).is_file()
    assert len(data["bids_warnings"]) == 2
    assert any("events" in w for w in data["bids_warnings"])
    assert any("participants" in w for w in data["bids_warnings"])


# ── A2-07, A2-14: where a run saves ─────────────────────────────────────────


def test_a_runs_npz_location(tmp_path: Path) -> None:
    source = tmp_path / "rec.mat"
    assert run_decomp_path(source, {}) == (tmp_path / "rec_decomp.npz", False)
    edf = tmp_path / "ds" / "sub-01" / "emg" / "sub-01_task-x_emg.edf"
    meta = {"bids_emg_path": str(edf), "bids_entity_label": "sub-01_task-x"}
    path, in_dataset = run_decomp_path(edf, meta)
    assert in_dataset
    assert (
        path
        == (
            tmp_path
            / "ds"
            / "derivatives"
            / "muedit"
            / "sub-01"
            / "decomp"
            / "sub-01_task-x_decomp.npz"
        ).resolve()
    )
    assert not path.parent.exists()  # created only when the file is written
    odd = {"bids_emg_path": str(tmp_path / "sub-x" / "a.edf"), "bids_entity_label": "nolabel"}
    assert run_decomp_path(source, odd) == (tmp_path / "rec_decomp.npz", False)


def test_a_bids_run_saves_a_complete_derivative_without_the_emg(tmp_path: Path) -> None:
    path = tmp_path / "motor_units.mat"
    n_samples = 12_000
    emg = motor_unit_emg(seed=3, n_samples=n_samples, fsamp=FSAMP)
    scipy.io.savemat(
        path,
        {"signal": {"data": emg, "fsamp": FSAMP, "gridname": "GR08MM1305", "muscle": "ta"}},
    )
    params = DecompositionParameters(niter=5, nbextchan=200)
    root = tmp_path / "ds"
    entities = {"subject": "01", "task": "mu", "placement_scheme_description": PLACEMENT}
    run = run_decomposition(str(path), params=params, bids_root=str(root), bids_entities=entities)
    assert run.save_path is not None
    deriv = root / "derivatives" / "muedit"
    assert Path(run.save_path).is_relative_to(deriv)
    assert json.loads((deriv / "dataset_description.json").read_text())["DatasetType"] == (
        "derivative"
    )
    with np.load(run.save_path) as z:
        assert "emg_data" not in z.files
    assert run.rois

    # The exported recording, decomposed again without an export, saves into the same
    # derivatives and does not copy the raw EMG there.
    edf = root / "sub-01" / "emg" / "sub-01_task-mu_emg.edf"
    again = run_decomposition(str(edf), params=params)
    assert again.save_path == run.save_path
    with np.load(again.save_path) as z:
        assert "emg_data" not in z.files


# ── A2-08, A2-11, A2-12: BIDS sidecars ──────────────────────────────────────


def test_auto_detected_bad_channels_are_marked_in_channels_tsv(tmp_path: Path) -> None:
    coordinates, ied, discard, _ = format_hdemg_signal(GRIDS)
    declared = [np.zeros_like(discard[0])]
    declared[0][0] = 1
    auto = [declared[0].copy()]
    auto[0][[0, 5]] = 1
    out = export_bids_emg(
        np.random.default_rng(0).normal(size=(N_EMG, N_SAMPLES)).astype(np.float32),
        FSAMP, GRIDS, coordinates, declared, tmp_path, ied=ied,
        placement_scheme_description=PLACEMENT, auto_discard_channels=auto,
    )  # fmt: skip
    rows = [r for r in _rows(out["channels_tsv"]) if r["type"] == "EMG"]
    assert (rows[0]["status"], rows[0]["status_description"]) == ("bad", "manually excluded")
    assert (rows[5]["status"], rows[5]["status_description"]) == ("bad", "auto-detected")
    assert sum(r["status"] == "bad" for r in rows) == 2


def test_derivative_groups_come_from_the_raw_channels_tsv(api: TestClient, tmp_path: Path) -> None:
    dataset = tmp_path / "data" / "proj"
    coordinates, ied, discard, _ = format_hdemg_signal(GRIDS)
    export_bids_emg(
        np.zeros((N_EMG, N_SAMPLES), dtype=np.float32), FSAMP, GRIDS, coordinates, discard,
        dataset, ied=ied, task="save", placement_scheme_description=PLACEMENT,
        grid_labels=["TA_GRID"],
    )  # fmt: skip
    path = dataset / "derivatives" / "muedit" / "sub-01" / "decomp" / f"{LABEL}_decomp.npz"
    token, _ = _open(api, _decomp(path))
    data = _save(api, token, remove_duplicates=False).json()["data"]
    events = _rows(Path(data["bids_deriv_paths"]["events_tsv"]))
    assert {r["group"] for r in events} == {"TA_GRID"}


def test_a_participant_update_keeps_the_fields_it_does_not_give(tmp_path: Path) -> None:
    write_participant(tmp_path, "01", {"age": 30, "sex": "F", "handedness": "right"})
    write_participant(tmp_path, "01", {"age": "n/a", "sex": "n/a", "handedness": "left"})
    write_participant(tmp_path, "02", None)
    rows = _rows(tmp_path / "participants.tsv")
    assert rows[0] == {"participant_id": "sub-01", "age": "30", "sex": "F", "handedness": "left"}
    assert rows[1] == {"participant_id": "sub-02", "age": "n/a", "sex": "n/a", "handedness": "n/a"}
    with pytest.raises(BidsFieldError, match="age"):
        write_participant(tmp_path, "01", {"age": "abc"})


# ── A2-09: validation at the save boundary ──────────────────────────────────


@pytest.mark.parametrize(
    "body",
    [
        {"fsamp": 0},
        {"spike_times": [[-5, 10]]},
        {"spike_times": [[2**31]]},
        {"powerline_freq": -50},
        {"powerline_freq": 5000},
        {"participant_meta": {"age": "abc"}},
    ],
    ids=["zero-fsamp", "negative-spike", "int32-overflow", "negative-mains", "mains-unit", "age"],
)
def test_a_bad_run_save_field_is_422_and_writes_nothing(
    api: TestClient, tmp_path: Path, body: dict[str, Any]
) -> None:
    resp = _run_save(api, **body)
    assert resp.status_code == 422, resp.text
    assert not list((tmp_path / "data").rglob("*.npz"))


def test_a_run_save_without_any_label_is_400(api: TestClient, tmp_path: Path) -> None:
    resp = _run_save(api, entity_label=None)
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["detail"]["field"] == "entity_label"
    assert not (tmp_path / "data" / "proj" / "derivatives").exists()


# ── A2-10: the npz and its sidecar as a pair ───────────────────────────────


def test_a_failed_sidecar_write_keeps_the_previous_save(
    api: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token, _ = _open(api, _decomp(tmp_path / f"{LABEL}_decomp.npz"))
    out = Path(_save(api, token, remove_duplicates=False).json()["data"]["path"])
    before = out.read_bytes(), out.with_suffix(".json").read_bytes()

    def fail(*_: Any, **__: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(editing_service, "save_editlog", fail)
    api.post(f"{API}/edit/ops/flag", json={"token": token, "mu": 0}, headers=TAB)
    assert _save(api, token).status_code == 500
    assert (out.read_bytes(), out.with_suffix(".json").read_bytes()) == before
    assert sorted(p.name for p in out.parent.iterdir()) == [out.with_suffix(".json").name, out.name]
