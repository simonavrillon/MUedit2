"""Edit sessions over HTTP (``editing_service``): opening, edits, recovery, duplicate removal, saves."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from httpx import Response
from starlette.testclient import TestClient

from muedit.api import cache
from muedit.api.schemas import EDIT_OPS
from muedit.api.services import editing_service
from muedit.decomp.decomposition_file import save_decomposition_npz, unpack_csr
from muedit.editing import edit_log
from muedit.editing.session import EditSession
from muedit.io.bids import export_bids_emg
from muedit.signal.grid import format_hdemg_signal
from tests._api import API, PLACEMENT, SESSION_HEADER, error, frame, ok, run_result, stored_run
from tests._editing_data import (
    FSAMP,
    N_SAMPLES,
    noisy_pulse_trains,
    spike_train,
    write_decomposition,
)
from tests._io_files import tsv_rows


@pytest.fixture()
def decomp_file(tmp_path: Path) -> Path:
    return write_decomposition(tmp_path / "sub-01_task-edit_decomp.npz")


def _open(api: TestClient, path: Path, tab: str = "tab-edit") -> tuple[dict[str, Any], dict]:
    return frame(
        api.post(
            f"{API}/edit/session/open", json={"path": str(path)}, headers={SESSION_HEADER: tab}
        )
    )


def _op(api: TestClient, token: str, op: str, tab: str = "tab-edit", **args: Any) -> Any:
    return frame(
        api.post(
            f"{API}/edit/ops/{op}", json={"token": token, **args}, headers={SESSION_HEADER: tab}
        )
    )


class TestSessionApi:
    def test_a_session_whose_edits_cannot_be_logged_says_so(
        self, api: TestClient, decomp_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        blocker = tmp_path / "not-a-folder"
        blocker.write_text("")
        monkeypatch.setattr(edit_log, "edit_logs_dir", lambda: blocker / "edit_logs")
        meta = _open(api, decomp_file)[0]
        assert meta["crash_recovery"] is False
        assert _op(api, meta["token"], "flag", mu=0, flag=True)[0]["crash_recovery"] is False

    def test_open_sends_fields_and_csr_spikes_not_pulse_trains(
        self, api: TestClient, decomp_file: Path
    ) -> None:
        meta, arrays = _open(api, decomp_file)
        assert meta["n_mu"] == 3
        assert meta["file_label"] == decomp_file.name
        assert meta["has_pulse"] == [True, True, True]
        assert meta["dirty"] is False and meta["can_undo"] is False
        assert meta["recoverable_edits"] == 0
        assert meta["crash_recovery"] is True
        assert set(arrays) == {"spikes", "spike_offsets"}
        assert arrays["spikes"].dtype == np.int32
        rows = unpack_csr(arrays["spikes"], arrays["spike_offsets"])
        assert rows[1].tolist() == spike_train(10.0, seed=2).tolist()
        edit = cache.get_edit_session(meta["token"])
        assert edit is not None
        assert isinstance(edit.arrays["base"], np.memmap)  # mapped from the file, not copied

    def test_ops_return_what_changed(self, api: TestClient, decomp_file: Path) -> None:
        meta, _ = _open(api, decomp_file)
        token = meta["token"]
        change, arrays = _op(
            api, token, "delete-spikes", mu=1, x_start=0, x_end=N_SAMPLES, y_min=0.0, y_max=5.0
        )
        assert change["changed"] == [1]
        assert change["dirty"] is True and change["can_undo"] is True
        assert change["history_start"] == 0
        assert change["history"][0]["type"] == "delete_spikes"
        assert arrays["spike_offsets"].tolist() == [0, 0]
        assert change["versions"][1] != meta["versions"][1]
        undone, arrays = _op(api, token, "undo")
        assert undone["history_start"] == 0 and undone["history"] == []
        assert unpack_csr(arrays["spikes"], arrays["spike_offsets"])[0].size > 0

    def test_duplicate_and_remove_duplicates(self, api: TestClient, decomp_file: Path) -> None:
        token = _open(api, decomp_file)[0]["token"]
        dup, _ = _op(api, token, "duplicate", mu=0)
        assert dup["n_mu"] == 4 and dup["changed"] == [3]
        removed, _ = _op(api, token, "remove-duplicates")
        assert removed["kept_indices"] == [0, 1, 2]
        assert removed["removed_count"] == 1
        assert removed["can_undo"] is False

    def test_bad_requests(self, api: TestClient, decomp_file: Path) -> None:
        token = _open(api, decomp_file)[0]["token"]
        for mu in (9, -1):
            resp = api.post(f"{API}/edit/ops/flag", json={"token": token, "mu": mu})
            assert resp.status_code == 400
            err = resp.json()["error"]
            assert (err["message"], err["detail"]) == (
                f"mu {mu} is out of range [0, 3)",
                {"field": "mu"},
            )
        resp = api.post(f"{API}/edit/ops/explode", json={"token": token})
        assert resp.status_code == 422
        # Each op's body is checked before the session sees it.
        resp = api.post(f"{API}/edit/ops/add-spikes", json={"token": token, "mu": 0, "x_end": 9})
        assert resp.status_code == 422
        assert resp.json()["error"]["detail"][0]["loc"] == ["body", "x_start"]
        for bad_mu in ("first", "1", True, 1.0):
            resp = api.post(f"{API}/edit/ops/flag", json={"token": token, "mu": bad_mu})
            assert resp.status_code == 422, bad_mu
        resp = api.post(f"{API}/edit/ops/flag", json={"token": "gone", "mu": 0})
        assert resp.status_code == 400
        resp = api.post(
            f"{API}/edit/ops/undo", json={"token": token}, headers={SESSION_HEADER: "tab-edit"}
        )
        assert resp.status_code == 400  # nothing to undo

    def test_prepare_grid(self, api: TestClient, decomp_file: Path) -> None:
        token = _open(api, decomp_file)[0]["token"]
        url = f"{API}/edit/session/prepare-grid"
        headers = {SESSION_HEADER: "tab-edit"}
        resp = api.post(url, json={"token": token, "grid": 9}, headers=headers)
        assert resp.status_code == 200 and resp.json()["data"] == {"grid": 9}
        resp = api.post(url, json={"token": "gone", "grid": 0}, headers=headers)
        assert resp.status_code == 400

    def test_pulse_viewport_is_exact(self, api: TestClient, decomp_file: Path) -> None:
        token = _open(api, decomp_file)[0]["token"]
        with np.load(decomp_file) as z:
            row = z["pulse_trains"][2]
        meta, arrays = frame(
            api.get(f"{API}/series/pulse", params={"token": token, "mu": 2, "bins": 100})
        )
        assert meta["kind"] == "envelope" and meta["end"] == N_SAMPLES
        np.testing.assert_array_equal(arrays["max"][0], row.reshape(100, -1).max(axis=1))
        np.testing.assert_array_equal(arrays["min"][0], row.reshape(100, -1).min(axis=1))
        spikes = spike_train(10.0, seed=3)
        assert arrays["spikes"].tolist() == spikes.tolist()
        np.testing.assert_array_equal(arrays["spike_values"], row[spikes])
        meta, arrays = frame(
            api.get(
                f"{API}/series/pulse",
                params={"token": token, "mu": 2, "start": 500, "end": 900, "bins": 1000},
            )
        )
        assert meta["kind"] == "samples"
        np.testing.assert_array_equal(arrays["samples"][0], row[500:900])

    def test_a_reloaded_page_takes_the_session_over(
        self, api: TestClient, decomp_file: Path
    ) -> None:
        token = _open(api, decomp_file, tab="tab-before")[0]["token"]
        meta, _ = frame(
            api.get(
                f"{API}/edit/session",
                params={"token": token},
                headers={SESSION_HEADER: "tab-after"},
            )
        )
        assert meta["token"] == token and meta["n_mu"] == 3
        slot = cache._EDIT_SESSIONS.slots[token]
        assert slot.session == "tab-after"

    def test_state_carries_the_log_tail_back_to_the_oldest_undo(
        self, api: TestClient, decomp_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(editing_service, "HISTORY_TAIL", 1)
        token = _open(api, decomp_file)[0]["token"]
        for mu in range(3):
            _op(api, token, "flag", mu=mu)
        edit = cache.get_edit_session(token)
        assert edit is not None
        n, first_undo = len(edit.history), edit.undo_stack[0].history_len

        def state() -> dict[str, Any]:
            return frame(
                api.get(
                    f"{API}/edit/session",
                    params={"token": token},
                    headers={SESSION_HEADER: "tab-edit"},
                )
            )[0]

        # All three flags can be undone, so the client holds the log back to the first.
        meta = state()
        assert meta["edit_history_start"] == first_undo
        assert meta["edit_history"] == edit.history[first_undo:]
        edit.undo_stack.clear()
        meta = state()
        assert meta["edit_history_start"] == n - 1
        assert meta["edit_history"] == edit.history[-1:]

    def test_unsaved_edits_are_offered_when_the_file_is_reopened(
        self, api: TestClient, decomp_file: Path
    ) -> None:
        token = _open(api, decomp_file)[0]["token"]
        _op(api, token, "delete-spikes", mu=0, x_start=0, x_end=N_SAMPLES, y_max=5.0)
        api.post(f"{API}/session/close", params={"session": "tab-edit"})

        meta, _ = _open(api, decomp_file)
        assert meta["recoverable_edits"] == 1
        meta, arrays = frame(
            api.post(
                f"{API}/edit/session/recover",
                json={"token": meta["token"]},
                headers={SESSION_HEADER: "tab-edit"},
            )
        )
        assert meta["recovered_edits"] == 1 and meta["dirty"] is True
        assert arrays["spike_offsets"].tolist()[:2] == [0, 0]

        api.post(f"{API}/session/close", params={"session": "tab-edit"})
        meta, _ = _open(api, decomp_file)
        assert meta["recoverable_edits"] == 1  # the recovered edits are unsaved again
        declined, _ = frame(
            api.post(
                f"{API}/edit/session/recover",
                json={"token": meta["token"], "apply": False},
                headers={SESSION_HEADER: "tab-edit"},
            )
        )
        assert declined["recovered_edits"] == 0 and declined["dirty"] is False
        api.post(f"{API}/session/close", params={"session": "tab-edit"})
        assert _open(api, decomp_file)[0]["recoverable_edits"] == 0

    def test_a_failed_recovery_still_drops_the_log(
        self, api: TestClient, decomp_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        token = _open(api, decomp_file)[0]["token"]
        _op(api, token, "flag", mu=0)
        api.post(f"{API}/session/close", params={"session": "tab-edit"})
        meta, _ = _open(api, decomp_file)
        assert meta["recoverable_edits"] == 1

        def broken(self: EditSession, records: Any) -> int:
            raise RuntimeError("replay broke")

        monkeypatch.setattr(EditSession, "replay", broken)
        resp = api.post(
            f"{API}/edit/session/recover",
            json={"token": meta["token"]},
            headers={SESSION_HEADER: "tab-edit"},
        )
        assert resp.status_code == 500
        monkeypatch.undo()
        api.post(f"{API}/session/close", params={"session": "tab-edit"})
        assert _open(api, decomp_file)[0]["recoverable_edits"] == 0

    def test_embedded_emg_lives_in_a_store_the_next_open_deletes(
        self, api: TestClient, tmp_path: Path
    ) -> None:
        path = write_decomposition(tmp_path / "sub-01_task-edit_decomp.npz", emg=True)

        def open_session() -> EditSession:
            edit = cache.get_edit_session(_open(api, path, tab="edit-store")[0]["token"])
            assert edit is not None
            return edit

        first = open_session()
        assert first.store.path.is_dir() and first.signal is not None
        assert isinstance(first.signal.data, np.memmap)
        with np.load(path) as z:
            np.testing.assert_array_equal(first.signal.data, z["emg_data"].astype(np.float32))
        second = open_session()
        assert not first.store.path.exists()
        assert second.store.path.is_dir()
        cache.close_session("edit-store")
        assert not second.store.path.exists()

    def test_update_filter_refits_on_the_embedded_emg(
        self, api: TestClient, tmp_path: Path
    ) -> None:
        path = write_decomposition(tmp_path / "sub-01_task-edit_decomp.npz", emg=True)
        token = _open(api, path)[0]["token"]
        meta, arrays = _op(api, token, "update-filter", mu=0, view_start=500, view_end=4000)
        assert meta["changed"] == [0]
        assert meta["history"][-1]["type"] == "update_filter"
        assert arrays["spikes"].tolist() == sorted(arrays["spikes"].tolist())

    def test_every_op_has_a_request_model(self) -> None:
        assert set(EDIT_OPS) == set(EditSession.OPS)

    def test_open_errors(self, api: TestClient, tmp_path: Path) -> None:
        assert api.post(f"{API}/edit/session/open", json={"path": ""}).status_code == 400
        missing = api.post(f"{API}/edit/session/open", json={"path": str(tmp_path / "no.npz")})
        assert missing.status_code == 404
        recording = tmp_path / "recording.otb4"
        recording.write_bytes(b"")
        err = error(api.post(f"{API}/edit/session/open", json={"path": str(recording)}), 400)
        assert "Expected .mat or .npz" in err["message"]
        assert err["detail"] == {"field": "path"}

    def test_a_file_that_fails_to_open_leaves_the_open_one(
        self, api: TestClient, decomp_file: Path, tmp_path: Path
    ) -> None:
        token = _open(api, decomp_file)[0]["token"]
        broken = tmp_path / "broken_decomp.npz"
        broken.write_bytes(b"not a decomposition")
        resp = api.post(
            f"{API}/edit/session/open",
            json={"path": str(broken)},
            headers={SESSION_HEADER: "tab-edit"},
        )
        assert resp.status_code == 400
        err = resp.json()["error"]
        assert err["message"] == "Cannot read broken_decomp.npz: File is not a zip file"
        assert err["detail"] == {"field": "path"}
        meta, _ = _op(api, token, "flag", mu=0, flag=True)
        assert meta["flagged"][0] is True
        replaced = _open(api, decomp_file)[0]["token"]
        assert replaced != token
        gone = api.post(
            f"{API}/edit/ops/undo", json={"token": token}, headers={SESSION_HEADER: "tab-edit"}
        )
        assert gone.status_code == 400

    def test_a_spikes_only_file_draws_its_trains(self, api: TestClient, tmp_path: Path) -> None:
        path = tmp_path / "spikes_only_decomp.npz"
        save_decomposition_npz(
            path,
            pulse_trains=None,
            spike_times=[[100, 200]],
            fsamp=FSAMP,
            grid_names=["GR08MM1305"],
            mu_grid_index=[0],
            muscle_names=[],
            parameters={},
            total_samples=N_SAMPLES,
        )
        meta, _ = _open(api, path)
        assert meta["has_pulse"] == [False]
        _, arrays = frame(
            api.get(
                f"{API}/series/pulse",
                params={"token": meta["token"], "mu": 0, "start": 0, "end": 300, "bins": 1000},
            )
        )
        assert np.flatnonzero(arrays["samples"][0]).tolist() == [100, 200]
        assert arrays["spike_values"].tolist() == [1.0, 1.0]


# ── duplicate removal ──────────────────────────────────


def _kept(spike_times: list[list[int]], grids: list[int] | None = None, **params: Any) -> list[int]:
    """MUs the edit stage's duplicate removal keeps."""
    from muedit.api.services.editing_service import _dedup

    grids = grids or [0] * len(spike_times)
    return _dedup([np.asarray(d) for d in spike_times], grids, params, FSAMP, N_SAMPLES)


class TestRemoveDuplicates:
    @pytest.mark.parametrize(
        "bgrids,kept",
        [(None, [0, 1]), (False, [0, 1, 2]), (0, [0, 1, 2]), (True, [0, 1]), ([[1.0]], [0, 1])],
    )
    def test_remove_duplicates_across_grids_unless_disabled(
        self, bgrids: Any, kept: list[int]
    ) -> None:
        a = spike_train(10.0, seed=3).tolist()
        b = spike_train(8.0, seed=4).tolist()
        parameters = {} if bgrids is None else {"duplicatesbgrids": bgrids}
        assert _kept([a, b, a], [0, 0, 1], **parameters) == kept

    @pytest.mark.parametrize("bgrids", [False, True])
    def test_remove_duplicates_matches_decomposition(self, bgrids: bool) -> None:
        from muedit.decomp.decomposition_file import build_pulse_trains_from_spike_times
        from muedit.decomp.postprocess import remove_duplicates_by_grid
        from muedit.decomp.types import DecompositionParameters

        a = spike_train(10.0, seed=3).tolist()
        b = spike_train(8.0, seed=4).tolist()
        c = spike_train(12.0, seed=5).tolist()
        noisy_a = sorted([*a, (a[5] + a[6]) // 2])
        spike_times = [a, b, noisy_a, c, a, b]
        grids = [0, 0, 1, 1, 1, 2]
        _, _, _, pipeline_kept = remove_duplicates_by_grid(
            build_pulse_trains_from_spike_times(spike_times, N_SAMPLES),
            [np.asarray(d) for d in spike_times],
            grids,
            3,
            DecompositionParameters(duplicatesbgrids=bgrids),
            FSAMP,
        )
        assert _kept(spike_times, grids, duplicatesbgrids=bgrids) == sorted(pipeline_kept)


# ── Saves ─────────────────────────────────────────────────────────────────────

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


def _open_to_save(api: TestClient, path: Path) -> tuple[str, Response]:
    resp = api.post(f"{API}/edit/session/open", json={"path": str(path)}, headers=TAB)
    token = frame(resp)[0]["token"] if resp.status_code == 200 else ""
    return token, resp


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


class TestRunSave:
    """``/edit/save``: a run saved from its stored result."""

    def test_a_negative_grid_index_is_refused_rather_than_dropping_its_mus(
        self, api: TestClient, tmp_path: Path
    ) -> None:
        resp = _run_save(api, mu_grid_index=[0, -1])
        assert resp.status_code == 422, resp.text
        _, opened = _open_to_save(api, _decomp(tmp_path / "neg_decomp.npz", mu_grid_index=[-1, -1]))
        assert opened.status_code == 400
        assert "negative grid index" in opened.json()["error"]["message"]

    def test_a_run_save_writes_the_runs_trains_discharges_and_artifacts(
        self, api: TestClient, tmp_path: Path
    ) -> None:
        trains = noisy_pulse_trains(2)
        spikes = [_spikes(1), _spikes(2)]
        token = stored_run(spikes, trains)
        regions = [{"start": 10, "end": 20}, {"start": 30, "end": 40}]
        data = ok(
            _run_save(
                api, run_result_token=token, remove_duplicates=False, artifact_regions=regions
            )
        )
        out = Path(data["path"])
        assert out.is_relative_to(tmp_path / "data" / "proj") and out.with_suffix(".json").is_file()
        with np.load(out) as z:
            np.testing.assert_array_equal(z["pulse_trains"], trains)
            assert [r.tolist() for r in unpack_csr(z["spike_times"], z["spike_offsets"])] == spikes
            assert z["artifact_intervals"].tolist() == [[10, 20], [30, 40]]
        assert run_result(token) is not None  # the run explorer still reads it

    def test_a_run_save_of_a_run_without_trains_writes_the_discharges_only(
        self, api: TestClient
    ) -> None:
        with np.load(ok(_run_save(api, remove_duplicates=False))["path"]) as z:
            assert "pulse_trains" not in z.files

    def test_a_run_save_keeps_the_runs_segments_sil_and_windows(self, api: TestClient) -> None:
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
        self, api: TestClient, tmp_path: Path, body: dict[str, Any]
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
        self, api: TestClient, tmp_path: Path, body: dict[str, Any], field: str
    ) -> None:
        resp = _run_save(api, **body)
        assert resp.status_code == 400, resp.text
        assert resp.json()["error"]["detail"]["field"] == field
        assert not (tmp_path / "data" / "proj" / "derivatives").exists()

    def test_a_run_save_without_dedup_keeps_duplicates(self, api: TestClient) -> None:
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


class TestSessionSave:
    """``/edit/session/save``: an edit session saved into its dataset."""

    def test_a_session_save_keeps_what_the_file_held(self, api: TestClient, tmp_path: Path) -> None:
        token, _ = _open_to_save(api, _decomp(tmp_path / f"{LABEL}_decomp.npz", emg=True))
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
        assert {r["units"] for r in tsv_rows(Path(data["bids_emg_paths"]["channels_tsv"]))} == {
            "uV"
        }
        events = Path(data["bids_deriv_paths"]["events_tsv"]).read_text(encoding="utf-8")
        assert events.splitlines()[0].split("\t") == [
            "onset", "duration", "sample", "unit_id", "event_type", "group"
        ]  # fmt: skip
        edit = cache.get_edit_session(token)
        assert edit is not None and edit.meta["file_label"] == out.name
        assert edit.meta["source_path"] == str(out)

    def test_a_session_save_without_the_placement_says_why_bids_was_skipped(
        self, api: TestClient, tmp_path: Path
    ) -> None:
        token, _ = _open_to_save(api, _decomp(tmp_path / f"{LABEL}_decomp.npz", emg=True))
        data = ok(_save(api, token, remove_duplicates=False))
        assert "bids_emg_paths" not in data
        assert "placement description" in data["bids_emg_error"]

    def test_a_new_mu_leaves_the_sil_out(self, api: TestClient, tmp_path: Path) -> None:
        token, _ = _open_to_save(api, _decomp(tmp_path / f"{LABEL}_decomp.npz"))
        api.post(f"{API}/edit/ops/duplicate", json={"token": token, "mu": 0}, headers=TAB)
        resp = _save(api, token, remove_duplicates=False)
        with np.load(resp.json()["data"]["path"]) as z:
            assert "sil" not in z.files

    def test_a_save_into_the_files_dataset_keeps_its_subject(
        self, api: TestClient, tmp_path: Path
    ) -> None:
        dataset = tmp_path / "data" / "study"
        path = dataset / "derivatives" / "muedit" / "sub-01" / "decomp" / f"{LABEL}_decomp.npz"
        token, _ = _open_to_save(api, _decomp(path))
        refused = _save(api, token, project="study", entity_label="sub-02_task-save")
        assert refused.status_code == 400
        assert refused.json()["error"]["detail"]["field"] == "entity_label"
        assert not (dataset / "derivatives" / "muedit" / "sub-02").exists()
        elsewhere = _save(api, token, project="other", entity_label="sub-02_task-save")
        assert elsewhere.status_code == 200, elsewhere.text

    def test_side_output_failures_are_reported_and_keep_the_rest(
        self, api: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fail(*_: Any, **__: Any) -> None:
            raise OSError("read-only")

        monkeypatch.setattr(editing_service, "export_bids_events", fail)
        monkeypatch.setattr(editing_service, "write_participant", fail)
        token, _ = _open_to_save(api, _decomp(tmp_path / f"{LABEL}_decomp.npz", emg=True))
        data = _save(api, token, placement_scheme_description=PLACEMENT).json()["data"]
        assert "bids_emg_error" not in data
        assert Path(data["bids_emg_paths"]["edf"]).is_file()
        assert len(data["bids_warnings"]) == 2
        assert any("events" in w for w in data["bids_warnings"])
        assert any("participants" in w for w in data["bids_warnings"])

    def test_derivative_groups_come_from_the_raw_channels_tsv(
        self, api: TestClient, tmp_path: Path
    ) -> None:
        dataset = tmp_path / "data" / "proj"
        coordinates, ied, discard, _ = format_hdemg_signal(GRIDS)
        export_bids_emg(
            np.zeros((N_EMG, N_SAMPLES), dtype=np.float32), FSAMP, GRIDS, coordinates, discard,
            dataset, ied=ied, task="save", placement_scheme_description=PLACEMENT,
            grid_labels=["TA_GRID"],
        )  # fmt: skip
        path = dataset / "derivatives" / "muedit" / "sub-01" / "decomp" / f"{LABEL}_decomp.npz"
        token, _ = _open_to_save(api, _decomp(path))
        data = _save(api, token, remove_duplicates=False).json()["data"]
        events = tsv_rows(Path(data["bids_deriv_paths"]["events_tsv"]))
        assert {r["group"] for r in events} == {"TA_GRID"}

    def test_a_failed_sidecar_write_keeps_the_previous_save(
        self, api: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        token, _ = _open_to_save(api, _decomp(tmp_path / f"{LABEL}_decomp.npz"))
        out = Path(_save(api, token, remove_duplicates=False).json()["data"]["path"])
        before = out.read_bytes(), out.with_suffix(".json").read_bytes()

        def fail(*_: Any, **__: Any) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(editing_service, "save_editlog", fail)
        api.post(f"{API}/edit/ops/flag", json={"token": token, "mu": 0}, headers=TAB)
        assert _save(api, token).status_code == 500
        assert (out.read_bytes(), out.with_suffix(".json").read_bytes()) == before
        assert sorted(p.name for p in out.parent.iterdir()) == [
            out.with_suffix(".json").name,
            out.name,
        ]

    def test_a_session_save_writes_the_edits_and_makes_them_the_baseline(
        self, api: TestClient, tmp_path: Path
    ) -> None:
        token, _ = _open_to_save(api, write_decomposition(tmp_path / "sub-01_task-edit_decomp.npz"))
        _op(api, token, "delete-spikes", tab="tab-save", mu=0, x_start=0, x_end=3000, y_max=5.0)
        _op(api, token, "flag", tab="tab-save", mu=1)
        _op(api, token, "duplicate", tab="tab-save", mu=2)
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
        reopened, _ = _open_to_save(api, out)
        _op(
            api,
            reopened,
            "delete-spikes",
            tab="tab-save",
            mu=1,
            x_start=0,
            x_end=N_SAMPLES,
            y_max=5.0,
        )
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
        self, api: TestClient, tmp_path: Path
    ) -> None:
        dataset = tmp_path / "elsewhere" / "study"
        path = (
            dataset / "derivatives" / "muedit" / "sub-01" / "decomp" / "sub-01_task-edit_decomp.npz"
        )
        path.parent.mkdir(parents=True)
        token, opened = _open_to_save(api, write_decomposition(path))
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
