"""The two saves (``editing_service``): what the run and session saves write, where, how each fails."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from httpx import Response
from starlette.testclient import TestClient

from muedit.api import cache
from muedit.api.services import editing_service
from muedit.decomp.decomposition_file import save_decomposition_npz, unpack_csr
from muedit.io.bids import export_bids_emg
from muedit.signal.grid import format_hdemg_signal
from tests._api import API, PLACEMENT, SESSION_HEADER, frame, ok, run_result, stored_run
from tests._editing_data import FSAMP, N_SAMPLES, noisy_pulse_trains, write_decomposition
from tests._io_files import tsv_rows

TAB = {SESSION_HEADER: "tab-save"}
GRIDS = ["HD08MM1305"]
N_EMG = len(format_hdemg_signal(GRIDS)[2][0])
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


def _open(api: TestClient, path: Path) -> tuple[str, Response]:
    resp = api.post(f"{API}/edit/session/open", json={"path": str(path)}, headers=TAB)
    token = frame(resp)[0]["token"] if resp.status_code == 200 else ""
    return token, resp


def _op(api: TestClient, token: str, op: str, **args: Any) -> Any:
    return frame(api.post(f"{API}/edit/ops/{op}", json={"token": token, **args}, headers=TAB))


def _save(api: TestClient, token: str, **form: Any) -> Response:
    body = {"token": token, "project": "proj", "entity_label": LABEL, **form}
    return api.post(f"{API}/edit/session/save", json=body, headers=TAB)


def _run_save(api: TestClient, **body: Any) -> Response:
    if "run_result_token" not in body:  # storing a run drops the session's previous one
        body["run_result_token"] = stored_run([_spikes(1), _spikes(2)])
    payload = {
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


# ── A2-02, A2-03, A2-04, A2-13, A2-15: what an edited file keeps ────────────


def test_a_run_save_writes_the_runs_trains_discharges_and_artifacts(
    api: TestClient, tmp_path: Path
) -> None:
    trains = noisy_pulse_trains(2)
    spikes = [_spikes(1), _spikes(2)]
    token = stored_run(spikes, trains)
    regions = [{"start": 10, "end": 20}, {"start": 30, "end": 40}]
    data = ok(
        _run_save(api, run_result_token=token, remove_duplicates=False, artifact_regions=regions)
    )
    out = Path(data["path"])
    assert out.is_relative_to(tmp_path / "data" / "proj") and out.with_suffix(".json").is_file()
    with np.load(out) as z:
        np.testing.assert_array_equal(z["pulse_trains"], trains)
        assert [r.tolist() for r in unpack_csr(z["spike_times"], z["spike_offsets"])] == spikes
        assert z["artifact_intervals"].tolist() == [[10, 20], [30, 40]]
    assert run_result(token) is not None  # the run explorer still reads it


def test_a_run_save_of_a_run_without_trains_writes_the_discharges_only(api: TestClient) -> None:
    with np.load(ok(_run_save(api, remove_duplicates=False))["path"]) as z:
        assert "pulse_trains" not in z.files


def test_a_run_save_keeps_the_runs_segments_sil_and_windows(api: TestClient) -> None:
    segments = {"target_index": 0, "target_name": "Force", "segments": []}
    token = stored_run(
        [_spikes(1), _spikes(2)],
        np.zeros((2, N_SAMPLES), dtype=np.float32),
        extras=cache.RunExtras(sil=[0.9, 0.8], rois=[(0, 2000)], target_segments=segments),
    )
    resp = _run_save(api, run_result_token=token, remove_duplicates=False)
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
    assert {r["units"] for r in tsv_rows(Path(data["bids_emg_paths"]["channels_tsv"]))} == {"uV"}
    events = Path(data["bids_deriv_paths"]["events_tsv"]).read_text(encoding="utf-8")
    assert events.splitlines()[0].split("\t") == [
        "onset", "duration", "sample", "unit_id", "event_type", "group"
    ]  # fmt: skip
    edit = cache.get_edit_session(token)
    assert edit is not None and edit.meta["file_label"] == out.name
    assert edit.meta["source_path"] == str(out)


def test_a_session_save_without_the_placement_says_why_bids_was_skipped(
    api: TestClient, tmp_path: Path
) -> None:
    token, _ = _open(api, _decomp(tmp_path / f"{LABEL}_decomp.npz", emg=True))
    data = ok(_save(api, token, remove_duplicates=False))
    assert "bids_emg_paths" not in data
    assert "placement description" in data["bids_emg_error"]


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


# ── A2-06: failure semantics ────────────────────────────────────────────────


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


# ── A2-08, A2-11, A2-12: BIDS sidecars ──────────────────────────────────────


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
    events = tsv_rows(Path(data["bids_deriv_paths"]["events_tsv"]))
    assert {r["group"] for r in events} == {"TA_GRID"}


# ── A2-09: validation at the save boundary ──────────────────────────────────


@pytest.mark.parametrize(
    "body",
    [
        {"fsamp": 0},
        {"fsamp": None},
        {"total_samples": 0},
        {"total_samples": None},
        {"powerline_freq": -50},
        {"powerline_freq": 5000},
        {"participant_meta": {"age": "abc"}},
    ],
    ids=[
        "zero-fsamp",
        "no-fsamp",
        "zero-samples",
        "no-samples",
        "negative-mains",
        "mains-unit",
        "age",
    ],
)
def test_a_bad_run_save_field_is_422_and_writes_nothing(
    api: TestClient, tmp_path: Path, body: dict[str, Any]
) -> None:
    resp = _run_save(api, **body)
    assert resp.status_code == 422, resp.text
    assert not list((tmp_path / "data").rglob("*.npz"))


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"entity_label": None}, "entity_label"),
        ({"entity_label": None, "file_label": "no_subject.npz"}, "entity_label"),
        ({"run_result_token": "expired"}, "run_result_token"),
    ],
    ids=["no-label", "no-subject", "expired-run"],
)
def test_a_bad_run_save_is_400_and_writes_nothing(
    api: TestClient, tmp_path: Path, body: dict[str, Any], field: str
) -> None:
    resp = _run_save(api, **body)
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["detail"]["field"] == field
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


# ── the session save's baseline, and where it goes ──────────────────────────


def test_a_session_save_writes_the_edits_and_makes_them_the_baseline(
    api: TestClient, tmp_path: Path
) -> None:
    token, _ = _open(api, write_decomposition(tmp_path / "sub-01_task-edit_decomp.npz"))
    _op(api, token, "delete-spikes", mu=0, x_start=0, x_end=3000, y_max=5.0)
    _op(api, token, "flag", mu=1)
    _op(api, token, "duplicate", mu=2)
    edit = cache.get_edit_session(token)
    assert edit is not None
    kept_spikes = [edit.spike_times[0].tolist(), edit.spike_times[2].tolist()]
    resp = api.post(
        f"{API}/edit/session/save",
        json={"token": token, "project": "proj", "entity_label": "sub-01_task-edit"},
        headers=TAB,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["kept_indices"] == [0, 2]
    assert [e["type"] for e in data["edit_history"][-2:]] == [
        "remove_flagged",
        "remove_duplicates",
    ]
    assert data["dirty"] is False and data["can_undo"] is False
    out = Path(data["path"])
    assert out.is_relative_to(tmp_path / "data" / "proj")
    with np.load(out) as z:
        np.testing.assert_array_equal(z["pulse_trains"], noisy_pulse_trains(3)[[0, 2]])
        rows = unpack_csr(z["spike_times"], z["spike_offsets"])
        assert [r.tolist() for r in rows] == kept_spikes
    assert edit.n_mu == 2 and not edit.dirty

    # Saving again overwrites the file the session may map.
    reopened, _ = _open(api, out)
    _op(api, reopened, "delete-spikes", mu=1, x_start=0, x_end=N_SAMPLES, y_max=5.0)
    again = api.post(
        f"{API}/edit/session/save",
        json={
            "token": reopened,
            "project": "proj",
            "entity_label": "sub-01_task-edit",
            "remove_duplicates": False,
        },
        headers=TAB,
    ).json()["data"]
    assert again["path"] == str(out)
    with np.load(out) as z:
        assert z["spike_offsets"].tolist()[-1] == z["spike_offsets"].tolist()[-2]
        saved_row = z["pulse_trains"][0].copy()
    # The file was replaced, not rewritten in place: the session still reads the
    # trains it had mapped, which are the ones it saved.
    _, arrays = frame(
        api.get(
            f"{API}/series/pulse",
            params={"token": reopened, "mu": 0, "start": 0, "end": 1000, "bins": 1000},
        )
    )
    np.testing.assert_array_equal(arrays["samples"][0], saved_row[:1000])


def test_a_dataset_outside_the_data_root_is_saved_back_into(
    api: TestClient, tmp_path: Path
) -> None:
    dataset = tmp_path / "elsewhere" / "study"
    path = dataset / "derivatives" / "muedit" / "sub-01" / "decomp" / "sub-01_task-edit_decomp.npz"
    path.parent.mkdir(parents=True)
    token, opened = _open(api, write_decomposition(path))
    assert frame(opened)[0]["project"] == ""

    def save(project: str) -> Response:
        return api.post(
            f"{API}/edit/session/save",
            json={
                "token": token,
                "project": project,
                "entity_label": "sub-01_task-edit",
            },
            headers=TAB,
        )

    assert Path(save("").json()["data"]["path"]).is_relative_to(dataset)
    assert Path(save("proj").json()["data"]["path"]).is_relative_to(tmp_path / "data" / "proj")
    refused = save("../escape")
    assert refused.status_code == 400
    assert refused.json()["error"]["detail"]["field"] == "project"


def test_a_run_save_without_dedup_keeps_duplicates(api: TestClient) -> None:
    a = _spikes(1)
    token = stored_run([a, a])
    resp = api.post(
        f"{API}/edit/save",
        json={
            "run_result_token": token,
            "remove_duplicates": False,
            "total_samples": N_SAMPLES,
            "fsamp": FSAMP,
            "entity_label": "sub-01_task-x",
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["kept_indices"] == [0, 1]
    assert data["edit_history"] == []
