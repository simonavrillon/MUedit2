"""Server-side edit session: row versions, cache lifecycle and the save/update-filter flow."""

from __future__ import annotations

import json
import struct
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from starlette.testclient import TestClient

from muedit.api import cache
from muedit.api.edit_session import (
    KEEP_VERSIONS,
    EditSession,
    RowUnavailableError,
    UnknownMuError,
)

API = "/api/v1"
FSAMP = 2000.0
N_SAMPLES = 6000
N_CHANNELS = 64
GRID = "GR08MM1305"
FILE_LABEL = "sub-02_task-session_decomp.npz"
ENTITY = "sub-02_task-session"
UIDS = ["g0_mu0", "g0_mu1", "g1_mu0"]


def _matrix(n: int = 3, cols: int = 500, dtype: type = np.float64) -> np.ndarray:
    rng = np.random.default_rng(3)
    return (rng.standard_normal((n, cols)) * 100).astype(dtype)


# ── EditSession ──────────────────────────────────────────────────────────────


class TestEditSession:
    def test_rows_are_read_only_views_of_the_matrix(self) -> None:
        matrix = _matrix()
        session = EditSession.from_matrix("f.npz", matrix, UIDS)
        row = session.row("g0_mu1")
        assert np.shares_memory(row, matrix)
        assert np.array_equal(row, matrix[1])
        assert not row.flags.writeable
        assert session.n_samples == matrix.shape[1]
        assert session.nbytes == matrix.nbytes  # nothing copied yet

    def test_from_matrix_rejects_inconsistent_input(self) -> None:
        with pytest.raises(ValueError, match="shape"):
            EditSession.from_matrix("f.npz", _matrix(3), UIDS[:2])
        with pytest.raises(ValueError, match="unique"):
            EditSession.from_matrix("f.npz", _matrix(3), ["a", "a", "b"])

    def test_add_version_keeps_the_original_row_untouched(self) -> None:
        matrix = _matrix()
        session = EditSession.from_matrix("f.npz", matrix, UIDS)
        new = matrix[0] * 2.0
        rev = session.add_version("g0_mu0", new)
        assert rev == 1
        assert np.array_equal(session.row("g0_mu0", 1), new)
        assert np.array_equal(session.row("g0_mu0", 0), matrix[0])
        assert np.array_equal(matrix, _matrix())  # the loaded matrix itself never changes
        assert session.nbytes == matrix.nbytes + new.nbytes

    def test_old_versions_are_pruned_but_revision_zero_never_is(self) -> None:
        session = EditSession.from_matrix("f.npz", _matrix(), UIDS)
        revs = [session.add_version("g0_mu0", np.full(500, float(i))) for i in range(6)]
        assert revs == [1, 2, 3, 4, 5, 6]
        assert session.row("g0_mu0", 0) is not None
        for kept in revs[-KEEP_VERSIONS:]:
            assert session.row("g0_mu0", kept) is not None
        with pytest.raises(RowUnavailableError):
            session.row("g0_mu0", revs[0])
        # memory accounting follows the pruning
        assert session.nbytes == 3 * 500 * 8 + KEEP_VERSIONS * 500 * 8

    def test_versions_are_kept_per_motor_unit(self) -> None:
        session = EditSession.from_matrix("f.npz", _matrix(), UIDS)
        session.add_version("g0_mu0", np.zeros(500))
        assert session.add_version("g0_mu1", np.ones(500)) == 1
        with pytest.raises(RowUnavailableError):
            session.row("g1_mu0", 1)

    def test_unknown_motor_unit(self) -> None:
        session = EditSession.from_matrix("f.npz", _matrix(), UIDS)
        with pytest.raises(UnknownMuError):
            session.row("nope")
        with pytest.raises(UnknownMuError):
            session.add_version("nope", np.zeros(500))

    def test_add_version_rejects_wrong_length(self) -> None:
        session = EditSession.from_matrix("f.npz", _matrix(), UIDS)
        with pytest.raises(ValueError, match="shape"):
            session.add_version("g0_mu0", np.zeros(10))

    def test_duplicate_shares_the_row_it_was_made_from(self) -> None:
        matrix = _matrix()
        session = EditSession.from_matrix("f.npz", matrix, UIDS)
        session.add_version("g0_mu0", matrix[0] + 1.0)
        session.duplicate("g0_mu0", 1, "g0_mu2")
        assert np.array_equal(session.row("g0_mu2"), matrix[0] + 1.0)
        # editing the duplicate leaves its source alone, and the other way round
        session.add_version("g0_mu2", np.zeros(500))
        assert np.array_equal(session.row("g0_mu0", 1), matrix[0] + 1.0)
        assert np.array_equal(session.row("g0_mu2", 0), matrix[0] + 1.0)

    def test_duplicate_errors(self) -> None:
        session = EditSession.from_matrix("f.npz", _matrix(), UIDS)
        with pytest.raises(UnknownMuError):
            session.duplicate("nope", 0, "x")
        with pytest.raises(RowUnavailableError):
            session.duplicate("g0_mu0", 9, "x")
        with pytest.raises(ValueError, match="already exists"):
            session.duplicate("g0_mu0", 0, "g0_mu1")

    def test_assemble_follows_the_requested_order_and_revisions(self) -> None:
        matrix = _matrix()
        session = EditSession.from_matrix("f.npz", matrix, UIDS)
        session.add_version("g0_mu1", np.full(500, 7.0))
        out = session.assemble(["g1_mu0", "g0_mu1", "g0_mu0"], {"g0_mu1": 1})
        assert out.dtype == np.float64
        assert np.array_equal(out[0], matrix[2])
        assert np.array_equal(out[1], np.full(500, 7.0))
        assert np.array_equal(out[2], matrix[0])
        assert np.array_equal(session.assemble(["g0_mu1"])[0], matrix[1])  # default = row as read

    def test_float32_source_is_widened_exactly(self) -> None:
        matrix = _matrix(dtype=np.float32)
        out = EditSession.from_matrix("f.npz", matrix, UIDS).assemble(UIDS)
        assert out.dtype == np.float64
        assert np.array_equal(out, matrix.astype(np.float64))

    def test_non_contiguous_matrix_is_made_contiguous(self) -> None:
        transposed = _matrix(500, 3).T  # a (3, 500) view with strided rows
        assert not transposed.flags.c_contiguous
        session = EditSession.from_matrix("f.npz", transposed, UIDS)
        assert np.array_equal(session.row("g0_mu1"), transposed[1])
        assert session.row("g0_mu1").flags.c_contiguous


# ── Cache lifecycle ──────────────────────────────────────────────────────────


class TestSessionCache:
    def test_store_and_get(self) -> None:
        session = EditSession.from_matrix("f.npz", _matrix(), UIDS)
        token = cache._store_edit_session(session)
        assert cache._get_edit_session(token) is session
        assert cache._get_edit_session("unknown") is None
        assert cache._get_edit_session(None) is None

    def test_opening_a_second_file_releases_the_first_session(self) -> None:
        first = cache._store_edit_session(EditSession.from_matrix("a.npz", _matrix(), UIDS))
        second = cache._store_edit_session(EditSession.from_matrix("b.npz", _matrix(), UIDS))
        assert cache._get_edit_session(first) is None
        assert cache._get_edit_session(second) is not None

    def test_expired_sessions_are_purged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        token = cache._store_edit_session(EditSession.from_matrix("f.npz", _matrix(), UIDS))
        monkeypatch.setattr(cache.time, "time", lambda: 1e12)
        assert cache._get_edit_session(token) is None

    def test_a_hit_refreshes_the_expiry(self) -> None:
        token = cache._store_edit_session(EditSession.from_matrix("f.npz", _matrix(), UIDS))
        entry = cache._EDIT_SESSION_CACHE[token]
        entry.expires_at = cache.time.time() + 1.0  # about to expire
        assert cache._get_edit_session(token) is not None
        assert entry.expires_at > cache.time.time() + 3600


# ── API flow ─────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def workspace(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    import muedit.api.config as config
    import muedit.api.services.editing_service as editing_service

    root = tmp_path_factory.mktemp("edit_session")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(config, "DATA_ROOT", root)
        mp.setattr(editing_service, "DATA_ROOT", root)
        mp.chdir(root)
        yield root


@pytest.fixture(scope="module")
def client(workspace: Path) -> Iterator[TestClient]:
    from muedit.api.app_factory import create_app
    from muedit.api.routes import include_routers

    app = create_app()
    include_routers(app)
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture(scope="module")
def source_pulse() -> np.ndarray:
    rng = np.random.default_rng(11)
    return rng.standard_normal((2, N_SAMPLES)) * rng.uniform(1.0, 300.0, (2, 1))


@pytest.fixture(scope="module")
def decomp_npz(workspace: Path, source_pulse: np.ndarray) -> Path:
    """Decomposition with its raw EMG embedded, so ``update-filter`` has a signal to work on."""
    from muedit.decomp.decomposition_file import pack_object_array, save_decomposition_npz

    path = workspace / FILE_LABEL
    save_decomposition_npz(
        path,
        pulse_trains=source_pulse,
        distimes=[[1000, 1400, 1800, 2200, 2600, 3000], [1200, 2000, 2800]],
        fsamp=FSAMP,
        grid_names=[GRID],
        mu_grid_index=[0, 0],
        muscles=["ta"],
        parameters={},
        total_samples=N_SAMPLES,
        extras={
            "emg_data": np.random.default_rng(0).normal(0, 1, (N_CHANNELS, N_SAMPLES)),
            "discard_channels": pack_object_array([np.zeros(N_CHANNELS, dtype=int)]),
            "coordinates": pack_object_array([np.zeros((N_CHANNELS, 2))]),
        },
    )
    return path


def _load(client: TestClient, path: Path) -> dict[str, Any]:
    resp = client.post(
        f"{API}/edit/load-by-path", json={"path": str(path)}, headers={"x-muedit-binary": "0"}
    )
    assert resp.status_code == 200, resp.text
    data: dict[str, Any] = resp.json()["data"]
    return data


def _save(client: TestClient, meta: dict[str, Any], distimes: list[list[int]], **extra: Any) -> Any:
    payload: dict[str, Any] = {
        "distimes": distimes,
        "flagged": [False] * len(distimes),
        "total_samples": N_SAMPLES,
        "fsamp": FSAMP,
        "grid_names": [GRID],
        "mu_grid_index": [0] * len(distimes),
        "mu_uids": extra.pop("mu_uids", meta["mu_uids"]),
        "parameters": {},
        "muscle": [],
        "edit_history": [],
        "artifact_times": [[] for _ in distimes],
        "file_label": FILE_LABEL,
        "entity_label": ENTITY,
        "project": "session",
        "remove_flagged": False,
        "remove_duplicates": False,
        "session_token": meta["session_token"],
        **extra,
    }
    return client.post(f"{API}/edit/save", json=payload)


def _saved_pulse(resp: Any) -> np.ndarray:
    assert resp.status_code == 200, resp.text
    return np.asarray(np.load(resp.json()["data"]["path"], allow_pickle=True)["pulse_trains"])


def _update_filter(client: TestClient, meta: dict[str, Any], **extra: Any) -> Any:
    return client.post(
        f"{API}/edit/update-filter",
        json={
            "project": "session",
            "edit_signal_token": meta["edit_signal_token"],
            "file_label": FILE_LABEL,
            "distimes": meta["distime_all"],
            "mu_index": 0,
            "view_start": 500,
            "view_end": 4500,
            "nbextchan": 200,
            "session_token": meta["session_token"],
            "mu_uid": "g0_mu0",
            **extra,
        },
    )


class TestSessionApi:
    def test_load_hands_out_a_session_and_the_motor_unit_ids(
        self, client: TestClient, decomp_npz: Path
    ) -> None:
        meta = _load(client, decomp_npz)
        assert isinstance(meta["session_token"], str) and meta["session_token"]
        assert meta["mu_uids"] == ["g0_mu0", "g0_mu1"]

    def test_binary_load_carries_the_token_too(self, client: TestClient, decomp_npz: Path) -> None:
        resp = client.post(f"{API}/edit/load-by-path", json={"path": str(decomp_npz)})
        meta_len = struct.unpack("<I", resp.content[8:12])[0]
        meta = json.loads(resp.content[20 : 20 + meta_len])
        assert meta["session_token"] and meta["mu_uids"] == ["g0_mu0", "g0_mu1"]

    def test_ids_come_from_the_edit_log_when_it_matches(
        self, client: TestClient, workspace: Path, source_pulse: np.ndarray
    ) -> None:
        from muedit.decomp.decomposition_file import save_decomposition_npz

        path = workspace / "sub-03_task-log_decomp.npz"
        save_decomposition_npz(
            path,
            pulse_trains=source_pulse,
            distimes=[[10, 20], [30, 40]],
            fsamp=FSAMP,
            grid_names=[GRID],
            mu_grid_index=[0, 0],
            muscles=["ta"],
            parameters={},
            total_samples=N_SAMPLES,
        )
        path.with_suffix(".json").write_text(
            json.dumps({"mu_uids": ["g0_mu4", "g0_mu7"], "history": []}), encoding="utf-8"
        )
        assert _load(client, path)["mu_uids"] == ["g0_mu4", "g0_mu7"]

    def test_saving_from_the_session_needs_no_uploaded_matrix(
        self, client: TestClient, decomp_npz: Path, source_pulse: np.ndarray
    ) -> None:
        meta = _load(client, decomp_npz)
        out = _saved_pulse(_save(client, meta, meta["distime_all"]))
        assert np.array_equal(out, source_pulse)

    def test_update_filter_creates_a_revision_and_only_that_row_changes(
        self, client: TestClient, decomp_npz: Path, source_pulse: np.ndarray
    ) -> None:
        meta = _load(client, decomp_npz)
        resp = _update_filter(client, meta)
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["row_rev"] == 1
        recomputed = np.asarray(data["pulse_train"])
        assert recomputed.shape == (N_SAMPLES,)
        assert not np.array_equal(recomputed, source_pulse[0])  # the window was recomputed
        out = _saved_pulse(_save(client, meta, meta["distime_all"], row_revs={"g0_mu0": 1}))
        assert np.array_equal(out[0], recomputed)  # exactly what the editor was shown
        assert np.array_equal(out[1], source_pulse[1])  # untouched MU: bit-identical
        # outside the recomputed window the row is still the file's, bit for bit
        assert np.array_equal(out[0][:500], source_pulse[0][:500])
        assert np.array_equal(out[0][4500:], source_pulse[0][4500:])

    def test_undo_is_a_revision_number_not_a_round_trip(
        self, client: TestClient, decomp_npz: Path, source_pulse: np.ndarray
    ) -> None:
        meta = _load(client, decomp_npz)
        assert _update_filter(client, meta).json()["data"]["row_rev"] == 1
        # the editor undid it: it now reports revision 0 again
        out = _saved_pulse(_save(client, meta, meta["distime_all"], row_revs={"g0_mu0": 0}))
        assert np.array_equal(out, source_pulse)
        # ... and a second recompute starts from the version the editor shows
        again = _update_filter(client, meta, row_rev=0).json()["data"]
        assert again["row_rev"] == 2

    def test_a_duplicated_motor_unit_is_saved_from_the_session(
        self, client: TestClient, decomp_npz: Path, source_pulse: np.ndarray
    ) -> None:
        meta = _load(client, decomp_npz)
        recomputed = np.asarray(_update_filter(client, meta).json()["data"]["pulse_train"])
        dup = client.post(
            f"{API}/edit/session/duplicate",
            json={
                "session_token": meta["session_token"],
                "source_uid": "g0_mu0",
                "source_rev": 1,
                "new_uid": "g0_mu2",
            },
        )
        assert dup.status_code == 200, dup.text
        distimes = [*meta["distime_all"], meta["distime_all"][0]]
        out = _saved_pulse(
            _save(
                client,
                meta,
                distimes,
                mu_uids=["g0_mu0", "g0_mu1", "g0_mu2"],
                row_revs={"g0_mu0": 1},
            )
        )
        assert out.shape[0] == 3
        assert np.array_equal(out[2], recomputed)  # the copy, made from the version shown
        assert np.array_equal(out[1], source_pulse[1])

    def test_removed_motor_units_are_left_out_of_the_saved_matrix(
        self, client: TestClient, decomp_npz: Path, source_pulse: np.ndarray
    ) -> None:
        meta = _load(client, decomp_npz)
        resp = _save(client, meta, meta["distime_all"], flagged=[True, False], remove_flagged=True)
        assert np.array_equal(_saved_pulse(resp), source_pulse[[1]])

    def test_unknown_session_is_a_404_that_says_what_to_do(
        self, client: TestClient, decomp_npz: Path
    ) -> None:
        meta = {**_load(client, decomp_npz), "session_token": "expired"}
        resp = _save(client, meta, meta["distime_all"])
        assert resp.status_code == 404
        assert "reopen the file" in resp.json()["error"]["detail"]["reason"]
        assert _update_filter(client, meta).status_code == 404

    def test_a_new_file_replaces_the_previous_session(
        self, client: TestClient, decomp_npz: Path
    ) -> None:
        first = _load(client, decomp_npz)
        _load(client, decomp_npz)
        assert _save(client, first, first["distime_all"]).status_code == 404

    def test_unknown_motor_unit_id_is_a_400(self, client: TestClient, decomp_npz: Path) -> None:
        meta = _load(client, decomp_npz)
        resp = _save(client, meta, meta["distime_all"], mu_uids=["g0_mu0", "nope"])
        assert resp.status_code == 400
        assert "nope" in resp.json()["error"]["detail"]["reason"]

    def test_discarded_revision_is_a_409(self, client: TestClient, decomp_npz: Path) -> None:
        meta = _load(client, decomp_npz)
        resp = _save(client, meta, meta["distime_all"], row_revs={"g0_mu0": 5})
        assert resp.status_code == 409

    def test_sample_count_mismatch_is_a_400(self, client: TestClient, decomp_npz: Path) -> None:
        meta = _load(client, decomp_npz)
        resp = _save(client, meta, meta["distime_all"], total_samples=N_SAMPLES + 1)
        assert resp.status_code == 400

    def test_the_uploaded_matrix_still_works_without_a_session(
        self, client: TestClient, decomp_npz: Path, source_pulse: np.ndarray
    ) -> None:
        meta = _load(client, decomp_npz)
        meta.pop("session_token")
        resp = client.post(
            f"{API}/edit/save",
            json={
                "distimes": meta["distime_all"],
                "pulse_trains": source_pulse.tolist(),
                "total_samples": N_SAMPLES,
                "fsamp": FSAMP,
                "grid_names": [GRID],
                "mu_grid_index": [0, 0],
                "file_label": FILE_LABEL,
                "entity_label": ENTITY,
                "project": "session",
                "remove_flagged": False,
                "remove_duplicates": False,
            },
        )
        assert np.array_equal(_saved_pulse(resp), source_pulse)  # json floats round-trip exactly
