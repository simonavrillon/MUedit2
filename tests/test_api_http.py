"""HTTP smoke tier: one FastAPI ``TestClient`` pass per live endpoint."""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import scipy.io
from fastapi import FastAPI
from httpx import Response
from starlette.testclient import TestClient

from muedit.api import cache
from muedit.api.binary import FRAME_FORMAT, FRAME_MEDIA_TYPE, pack_frame, unpack_frame
from muedit.decomp.decomposition_file import load_decomposition_file, unpack_csr
from tests._synthetic_emg import motor_unit_emg
from tests.conftest import REPO_ROOT

FSAMP = 2000.0
N_CHANNELS = 64
N_SAMPLES = 6000
GRID = "GR08MM1305"
API = "/api/v1"
SESSION_HEADER = "X-MUedit-Session"

LIVE_ENDPOINTS: list[tuple[str, str]] = [
    ("GET", "/health"),
    ("GET", "/dialog/open-file"),
    ("POST", "/preview-by-path"),
    ("GET", "/series/emg"),
    ("GET", "/series/overview"),
    ("GET", "/series/aux"),
    ("POST", "/qc/auto"),
    ("POST", "/decompose_stream"),
    ("POST", "/decompose/cancel"),
    ("GET", "/decompose_preview/{token}"),
    ("GET", "/series/pulse"),
    ("GET", "/spikes"),
    ("POST", "/edit/session/open"),
    ("GET", "/edit/session"),
    ("POST", "/edit/session/recover"),
    ("POST", "/edit/session/save"),
    ("POST", "/edit/ops/{op}"),
    ("POST", "/edit/save"),
    ("POST", "/session/close"),
    ("GET", "/debug/memory"),
]

ENVELOPE_KEYS = {"data", "meta"}

# Path Item keys that are operations; the rest (``parameters``, ``summary``, ...) are metadata.
OPENAPI_OPERATION_KEYS = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def workspace(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """Tmp dir used as DATA_ROOT *and* cwd (some routes write relative paths)."""
    import muedit.api.config as config

    root = tmp_path_factory.mktemp("api_http")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(config, "DATA_ROOT", root)
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
def emg() -> np.ndarray:
    return np.random.default_rng(0).normal(0, 1, (N_CHANNELS, N_SAMPLES))


@pytest.fixture(scope="module")
def signal_mat(workspace: Path, emg: np.ndarray) -> Path:
    """Plain MATLAB v5 ``signal`` struct readable by ``load_signal``."""
    path = workspace / "synthetic_signal.mat"
    scipy.io.savemat(
        path,
        {
            "signal": {
                "data": emg,
                "fsamp": FSAMP,
                "gridname": GRID,
                "muscle": "ta",
                "device_name": "Synthetic",
                "auxiliary": np.zeros((0, N_SAMPLES)),
                "auxiliaryname": [],
            }
        },
    )
    return path


@pytest.fixture(scope="module")
def decomp_npz(workspace: Path, emg: np.ndarray) -> Path:
    """MUedit NPZ decomposition with embedded EMG (so edit-load caches a context)."""
    from muedit.decomp.decomposition_file import save_decomposition_npz

    path = workspace / "sub-01_task-smoke_decomp.npz"
    save_decomposition_npz(
        path,
        pulse_trains=np.random.default_rng(1).random((2, N_SAMPLES)).astype(np.float32),
        distimes=[[1000, 1400, 1800, 2200], [1200, 2000]],
        fsamp=FSAMP,
        grid_names=[GRID],
        mu_grid_index=[0, 0],
        muscles=["ta"],
        parameters={},
        total_samples=N_SAMPLES,
        emg_data=emg,
        discard_channels=[np.zeros(N_CHANNELS, dtype=int)],
        coordinates=[np.zeros((N_CHANNELS, 2))],
    )
    return path


@pytest.fixture(scope="module")
def upload_token(client: TestClient, signal_mat: Path) -> str:
    # Fixtures load files in their own sessions, as separate tabs would: a session keeps one upload.
    resp = client.post(
        f"{API}/preview-by-path",
        json={"path": str(signal_mat)},
        headers={SESSION_HEADER: "fixture-upload"},
    )
    return _ok(resp)["upload_token"]


@pytest.fixture(scope="module")
def stream_events(client: TestClient, upload_token: str) -> list[dict[str, Any]]:
    resp = client.post(
        f"{API}/decompose_stream",
        data={
            "upload_token": upload_token,
            "params": json.dumps({"niter": 5, "nbextchan": 200}),
        },
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/x-ndjson")
    return [json.loads(line) for line in resp.text.splitlines()]


@pytest.fixture(scope="module")
def mu_run(client: TestClient, workspace: Path) -> dict[str, Any]:
    """A full-preview run on synthetic data that yields motor units, as the app starts it."""
    n_samples = 20_000
    path = workspace / "motor_units.mat"
    scipy.io.savemat(
        path,
        {
            "signal": {
                "data": motor_unit_emg(seed=3, n_samples=n_samples, fsamp=FSAMP),
                "fsamp": FSAMP,
                "gridname": GRID,
                "muscle": "ta",
                "auxiliary": np.zeros((0, n_samples)),
                "auxiliaryname": [],
            }
        },
    )
    resp = client.post(
        f"{API}/preview-by-path",
        json={"path": str(path)},
        headers={SESSION_HEADER: "fixture-mu-run"},
    )
    token = _ok(resp)["upload_token"]
    resp = client.post(
        f"{API}/decompose_stream",
        data={
            "upload_token": token,
            "params": json.dumps({"niter": 10, "nbextchan": 400}),
            "full_preview": "true",
        },
        headers={SESSION_HEADER: "fixture-mu-run"},
    )
    done = json.loads(resp.text.splitlines()[-1])
    assert done["stage"] == "done", done
    assert done["summary"]["mu_count"] > 0
    return done


@pytest.fixture(scope="module")
def edit_token(client: TestClient, decomp_npz: Path) -> str:
    resp = client.post(
        f"{API}/edit/session/open",
        json={"path": str(decomp_npz)},
        headers={SESSION_HEADER: "fixture-edit"},
    )
    meta, _ = _unpack_frame_response(resp)
    return meta["token"]


# ── Helpers ──────────────────────────────────────────────────────────────────


def _ok(resp: Response) -> Any:
    """Assert a 200 success envelope and return its ``data``."""
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == ENVELOPE_KEYS
    assert body["meta"] == {"api_version": "v1"}
    return body["data"]


def _err(resp: Response, status: int, code: str | None = None) -> dict[str, Any]:
    """Assert an error envelope with the given status and return ``error``."""
    assert resp.status_code == status, resp.text
    body = resp.json()
    assert set(body) == {"error"}
    err = body["error"]
    assert {"code", "message"} <= set(err)
    assert err["code"] == (code or f"http_{status}")
    return err


def _unpack_frame_response(resp: Response) -> tuple[dict[str, Any], dict[str, np.ndarray]]:
    """Assert a MUB1 frame response and decode it."""
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == FRAME_MEDIA_TYPE
    assert resp.headers["x-muedit-format"] == FRAME_FORMAT
    return unpack_frame(resp.content)


# ── Route table ──────────────────────────────────────────────────────────────


class TestRouteTable:
    def test_every_live_endpoint_is_registered(self, client: TestClient) -> None:
        app = client.app
        assert isinstance(app, FastAPI)
        # FastAPI >=0.137 keeps included routers nested, so ``app.routes`` no longer lists them.
        registered = {
            (method.upper(), path)
            for path, path_item in app.openapi()["paths"].items()
            for method in path_item
            if method in OPENAPI_OPERATION_KEYS
        }
        missing = [(m, API + p) for m, p in LIVE_ENDPOINTS if (m, API + p) not in registered]
        assert not missing

    def test_frontend_static_routes_are_live(self) -> None:
        """Every literal path in ``routes.js`` is covered by this tier."""
        js = (REPO_ROOT / "frontend" / "src" / "api" / "routes.js").read_text(encoding="utf-8")
        static = set(re.findall(r':\s*"(/[^"]+)"', js))
        assert static, "no routes parsed from routes.js"
        assert static <= {p for _, p in LIVE_ENDPOINTS}

    def test_unknown_route_uses_error_envelope(self, client: TestClient) -> None:
        assert _err(client.get(f"{API}/does-not-exist"), 404)["message"] == "Not Found"

    def test_wrong_method_uses_error_envelope(self, client: TestClient) -> None:
        resp = client.get(f"{API}/edit/save")
        _err(resp, 405)
        assert resp.headers["allow"] == "POST"


# ── /health ──────────────────────────────────────────────────────────────────


def test_health(client: TestClient) -> None:
    assert _ok(client.get(f"{API}/health")) == {"status": "ok"}


# ── /session/close, /debug/memory ────────────────────────────────────────────


class TestSessions:
    def _preview(self, client: TestClient, path: Path, session: str) -> str:
        resp = client.post(
            f"{API}/preview-by-path", json={"path": str(path)}, headers={SESSION_HEADER: session}
        )
        return _ok(resp)["upload_token"]

    def _qc_auto(self, client: TestClient, token: str) -> Response:
        return client.post(f"{API}/qc/auto", json={"upload_token": token})

    def test_loading_the_next_file_releases_the_sessions_upload(
        self, client: TestClient, signal_mat: Path
    ) -> None:
        first = self._preview(client, signal_mat, "tab-next-file")
        second = self._preview(client, signal_mat, "tab-next-file")
        _err(self._qc_auto(client, first), 400)
        _ok(self._qc_auto(client, second))

    def test_closing_a_session_drops_its_data(
        self, client: TestClient, signal_mat: Path, upload_token: str
    ) -> None:
        token = self._preview(client, signal_mat, "tab-closing")
        resp = client.post(f"{API}/session/close", params={"session": "tab-closing"})
        assert resp.status_code == 204
        _err(self._qc_auto(client, token), 400)
        _ok(self._qc_auto(client, upload_token))
        sessions = _ok(client.get(f"{API}/debug/memory"))["budget"]["sessions"]
        assert "tab-closing" not in {s["id"] for s in sessions}

    def test_malformed_session_ids_are_ignored(self, client: TestClient) -> None:
        resp = client.post(f"{API}/session/close", params={"session": "../default"})
        assert resp.status_code == 204
        assert _err(client.post(f"{API}/session/close"), 422, "validation_error")

    def test_a_request_with_a_session_makes_it_active(self, client: TestClient) -> None:
        client.get(f"{API}/health", headers={SESSION_HEADER: "tab-active"})
        budget = _ok(client.get(f"{API}/debug/memory"))["budget"]
        assert budget["active_session"] == "tab-active"

    def test_debug_memory(self, client: TestClient, upload_token: str) -> None:
        data = _ok(client.get(f"{API}/debug/memory"))
        assert data["process_bytes"] > 0
        assert data["physical_memory_bytes"] > data["process_bytes"]
        budget = data["budget"]
        assert budget["limit_bytes"] == cache.BUDGET.limit_bytes
        assert set(budget["caches"]) == {
            "uploads",
            "decompose_previews",
            "run_results",
            "edit_sessions",
        }
        assert budget["used_bytes"] == sum(c["bytes"] for c in budget["caches"].values())
        fixture = next(s for s in budget["sessions"] if s["id"] == "fixture-upload")
        assert fixture["entries"] >= 1


# ── /dialog/open-file ────────────────────────────────────────────────────────


class TestDialog:
    @pytest.fixture()
    def pick(self, monkeypatch: pytest.MonkeyPatch) -> Callable[[object], None]:
        """Replace both native dialog backends with a controllable stub."""
        import muedit.api.routes.dialog as dialog

        def install(behaviour: object) -> None:
            def fake() -> object:
                if isinstance(behaviour, BaseException):
                    raise behaviour
                return behaviour

            monkeypatch.setattr(dialog, "_open_dialog_macos", fake)
            monkeypatch.setattr(dialog, "_open_dialog_tkinter", fake)

        return install

    def test_selected_file(self, client: TestClient, pick: Callable[[object], None]) -> None:
        pick("/data/rec/sub-01_emg.otb4")
        assert _ok(client.get(f"{API}/dialog/open-file")) == {
            "path": "/data/rec/sub-01_emg.otb4",
            "name": "sub-01_emg.otb4",
        }

    def test_cancelled(self, client: TestClient, pick: Callable[[object], None]) -> None:
        pick(None)
        assert _ok(client.get(f"{API}/dialog/open-file")) == {"path": None, "name": None}

    def test_timeout_is_408(self, client: TestClient, pick: Callable[[object], None]) -> None:
        pick(subprocess.TimeoutExpired("osascript", 120))
        _err(client.get(f"{API}/dialog/open-file"), 408)

    def test_backend_failure_is_500(
        self,
        client: TestClient,
        pick: Callable[[object], None],
    ) -> None:
        pick(RuntimeError("no display"))
        assert _err(client.get(f"{API}/dialog/open-file"), 500)["message"] == "no display"


# ── /preview-by-path ─────────────────────────────────────────────────────────

PREVIEW_KEYS = {
    "upload_token",
    "grid_names",
    "total_samples",
    "fsamp",
    "channel_means",
    "coordinates",
    "metadata",
    "muscle",
    "auxiliary_names",
}


def _check_preview(data: dict[str, Any]) -> None:
    assert set(data) >= PREVIEW_KEYS
    assert isinstance(data["upload_token"], str) and data["upload_token"]
    assert data["grid_names"] == [GRID]
    assert data["total_samples"] == N_SAMPLES
    assert data["fsamp"] == FSAMP
    assert len(data["channel_means"]) == 1 and len(data["channel_means"][0]) == N_CHANNELS
    assert len(data["coordinates"]) == 1 and len(data["coordinates"][0]) == N_CHANNELS
    # Series travel as viewport envelopes (/series/*), never as JSON lists.
    assert not {"mean_abs", "grid_mean_abs", "auxiliary"} & set(data)


class TestPreview:
    def test_by_path(self, client: TestClient, signal_mat: Path) -> None:
        _check_preview(_ok(client.post(f"{API}/preview-by-path", json={"path": str(signal_mat)})))

    def test_by_path_missing_body_is_422(self, client: TestClient) -> None:
        err = _err(client.post(f"{API}/preview-by-path", json={}), 422, "validation_error")
        assert isinstance(err["detail"], list)

    def test_by_path_without_json_content_type_is_422(self, client: TestClient) -> None:
        body = json.dumps({"path": "x.mat"}).encode()
        resp = client.post(f"{API}/preview-by-path", content=body, headers={"content-type": ""})
        _err(resp, 422, "validation_error")

    def test_by_path_nonexistent_file_is_404(self, client: TestClient, workspace: Path) -> None:
        missing = str(workspace / "nope.mat")
        err = _err(client.post(f"{API}/preview-by-path", json={"path": missing}), 404)
        assert err["detail"] == {"field": "path", "reason": f"File not found: {missing}"}

    def test_by_path_unsupported_format_is_400(self, client: TestClient, workspace: Path) -> None:
        path = workspace / "notes.txt"
        path.write_text("not a recording")
        err = _err(client.post(f"{API}/preview-by-path", json={"path": str(path)}), 400)
        assert err["detail"]["field"] == "path"
        assert "Unsupported file format" in err["detail"]["reason"]


# ── /series/emg, /series/overview, /series/aux ───────────────────────────────


def _series(
    client: TestClient, kind: str, **params: Any
) -> tuple[dict[str, Any], dict[str, np.ndarray]]:
    return _unpack_frame_response(client.get(f"{API}/series/{kind}", params=params))


class TestSeries:
    def test_emg_envelope_of_the_whole_recording(
        self, client: TestClient, upload_token: str
    ) -> None:
        meta, arrays = _series(client, "emg", upload_token=upload_token, grid=0, bins=100)
        assert meta["kind"] == "envelope"
        assert (meta["start"], meta["end"], meta["bins"]) == (0, N_SAMPLES, 100)
        assert meta["factor"] == 16  # the coarsest level with at least one bin per output bin
        assert (meta["total_samples"], meta["fsamp"], meta["grid"]) == (N_SAMPLES, FSAMP, 0)
        assert len(meta["names"]) == N_CHANNELS
        assert arrays["min"].dtype == np.float32
        assert arrays["min"].shape == arrays["max"].shape == (N_CHANNELS, 100)
        assert (arrays["min"] <= arrays["max"]).all()

    def test_emg_samples_when_zoomed_in(self, client: TestClient, upload_token: str) -> None:
        meta, arrays = _series(
            client, "emg", upload_token=upload_token, start=1000, end=1200, bins=512
        )
        assert (meta["kind"], meta["factor"]) == ("samples", 1)
        assert arrays["samples"].shape == (N_CHANNELS, 200)

    def test_overview_has_one_row_per_grid(self, client: TestClient, upload_token: str) -> None:
        meta, arrays = _series(client, "overview", upload_token=upload_token, bins=256)
        assert meta["names"] == [GRID]
        assert arrays["max"].shape == (1, 256)
        assert (arrays["max"] >= 0).all()  # a mean of |EMG|

    def test_aux_without_channels_is_empty(self, client: TestClient, upload_token: str) -> None:
        meta, arrays = _series(client, "aux", upload_token=upload_token, bins=64)
        assert meta["names"] == []
        assert arrays["max"].shape == (0, 64)

    @pytest.mark.parametrize(
        ("params", "status", "code"),
        [
            ({"upload_token": "expired"}, 400, None),
            ({"grid": 1}, 400, None),
            ({"bins": 0}, 422, "validation_error"),
            ({"end": -1}, 422, "validation_error"),
        ],
        ids=["bad-token", "bad-grid", "no-bins", "negative-end"],
    )
    def test_rejected(
        self,
        client: TestClient,
        upload_token: str,
        params: dict[str, Any],
        status: int,
        code: str | None,
    ) -> None:
        query = {"upload_token": upload_token, **params}
        _err(client.get(f"{API}/series/emg", params=query), status, code)

    def test_missing_token_is_422(self, client: TestClient) -> None:
        _err(client.get(f"{API}/series/emg"), 422, "validation_error")


# ── /qc/auto ─────────────────────────────────────────────────────────────────


class TestQcAuto:
    def test_shape(self, client: TestClient, upload_token: str) -> None:
        data = _ok(client.post(f"{API}/qc/auto", json={"upload_token": upload_token}))
        assert set(data) == {
            "bad_channels_per_grid",
            "artifact_regions",
            "artifact_samples",
            "total_samples",
            "fsamp",
            "grid_names",
        }
        assert [len(g) for g in data["bad_channels_per_grid"]] == [N_CHANNELS]
        assert isinstance(data["artifact_regions"], list)
        assert all(len(r) == 2 for r in data["artifact_regions"])
        assert data["total_samples"] == N_SAMPLES
        assert data["grid_names"] == [GRID]

    def test_bad_token(self, client: TestClient) -> None:
        err = _err(client.post(f"{API}/qc/auto", json={"upload_token": "expired"}), 400)
        assert err["detail"]["field"] == "upload_token"


# ── /decompose_stream, /decompose_preview/{token} ────────────────────────────


class TestDecomposeStream:
    def test_event_sequence(self, stream_events: list[dict[str, Any]]) -> None:
        stages = [e["stage"] for e in stream_events]
        assert stages[0] == "start"
        assert stages[-1] == "done"
        assert set(stages) <= {"start", "progress", "done"}
        assert all("pct" in e for e in stream_events)

    def test_done_event_shape(self, stream_events: list[dict[str, Any]]) -> None:
        done = stream_events[-1]
        assert {"summary", "preview", "pct", "message"} <= set(done)
        assert done["pct"] == 100
        summary = done["summary"]
        assert {
            "fsamp",
            "grid_names",
            "mu_count",
            "sil",
            "discard_channels",
            "parameters",
        } <= set(summary)
        assert summary["parameters"]["niter"] == 5
        preview = done["preview"]
        assert isinstance(preview["preview_binary_token"], str)
        assert "pulse_trains_full" not in preview
        assert "run_result_token" not in preview  # no motor units, nothing to save
        # Whole-recording series come from the upload's /series/* endpoints, never from here.
        dropped = {"mean_abs", "grid_mean_abs", "auxiliary", "pulse_trains", "pulse_trains_all"}
        assert not dropped & set(preview)

    def test_full_run_keeps_its_pulse_trains_for_the_save(self, mu_run: dict[str, Any]) -> None:
        token = mu_run["preview"]["run_result_token"]
        pulse = cache._get_run_result(token)
        assert pulse is not None
        assert pulse.dtype == np.float32
        assert pulse.shape == (mu_run["summary"]["mu_count"], mu_run["preview"]["total_samples"])

    def test_json_preview_when_binary_disabled(self, client: TestClient, upload_token: str) -> None:
        resp = client.post(
            f"{API}/decompose_stream",
            data={
                "upload_token": upload_token,
                "params": json.dumps({"niter": 2, "nbextchan": 200}),
                "full_preview": "true",
            },
            headers={"x-muedit-binary": "0"},
        )
        done = json.loads(resp.text.splitlines()[-1])
        assert done["stage"] == "done"
        assert "preview_binary_token" not in done["preview"]
        assert "pulse_trains_full" not in done["preview"]  # stays on the server here too
        assert isinstance(done["preview"]["distime_all"], list)

    def test_artifact_regions_accepted(self, client: TestClient, upload_token: str) -> None:
        resp = client.post(
            f"{API}/decompose_stream",
            data={
                "upload_token": upload_token,
                "params": json.dumps({"niter": 2, "nbextchan": 200}),
                "artifact_regions": json.dumps([[100, 400]]),
            },
        )
        assert json.loads(resp.text.splitlines()[-1])["stage"] == "done"

    def test_bad_params_streams_error_event(self, client: TestClient, upload_token: str) -> None:
        resp = client.post(
            f"{API}/decompose_stream",
            data={
                "upload_token": upload_token,
                "params": "[1, 2]",
            },
        )
        assert resp.status_code == 200
        last = json.loads(resp.text.splitlines()[-1])
        assert last["stage"] == "error"
        assert {"message", "detail", "pct"} <= set(last)

    def test_missing_token_is_400(self, client: TestClient) -> None:
        err = _err(client.post(f"{API}/decompose_stream", data={"params": "{}"}), 400)
        assert err["detail"]["field"] == "upload_token"

    def test_persisted_output_lands_next_to_source(
        self, client: TestClient, upload_token: str, signal_mat: Path
    ) -> None:
        resp = client.post(
            f"{API}/decompose_stream",
            data={
                "upload_token": upload_token,
                "params": json.dumps({"niter": 2, "nbextchan": 200}),
                "persist_output": "true",
            },
        )
        done = json.loads(resp.text.splitlines()[-1])
        assert done["stage"] == "done"
        expected = signal_mat.with_name(signal_mat.stem + "_decomp.npz")
        assert done["summary"]["save_path"] == str(expected)
        assert expected.is_file()


class TestDecomposePreview:
    def test_binary_payload_is_served_once(
        self, client: TestClient, mu_run: dict[str, Any]
    ) -> None:
        token = mu_run["preview"]["preview_binary_token"]
        meta, arrays = _unpack_frame_response(client.get(f"{API}/decompose_preview/{token}"))
        # Pulse trains stay on the server; discharge times come as CSR arrays.
        assert set(arrays) == {"spikes", "spike_offsets"}
        assert not {"distime_all", "distime", "pulse_trains_full"} & set(meta)
        run = cache._get_run_result_entry(mu_run["preview"]["run_result_token"])
        assert run is not None
        rows = unpack_csr(arrays["spikes"], arrays["spike_offsets"])
        assert [r.tolist() for r in rows] == [s.tolist() for s in run.spikes]
        assert len(rows) == mu_run["summary"]["mu_count"]
        _err(client.get(f"{API}/decompose_preview/{token}"), 404)

    def test_unknown_token_is_404(self, client: TestClient) -> None:
        _err(client.get(f"{API}/decompose_preview/not-a-token"), 404)

    def test_the_explorer_reads_the_runs_pulse_trains(
        self, client: TestClient, mu_run: dict[str, Any]
    ) -> None:
        token = mu_run["preview"]["run_result_token"]
        run = cache._get_run_result_entry(token)
        assert run is not None
        meta, arrays = _unpack_frame_response(
            client.get(f"{API}/series/pulse", params={"token": token, "mu": 0, "bins": 500})
        )
        assert meta["kind"] == "envelope"
        row = np.asarray(run.pulse_trains[0])
        assert arrays["max"][0].max() == row.max() and arrays["min"][0].min() == row.min()
        np.testing.assert_array_equal(arrays["spikes"], run.spikes[0])
        np.testing.assert_array_equal(arrays["spike_values"], row[run.spikes[0]])
        _, csr = _unpack_frame_response(client.get(f"{API}/spikes", params={"token": token}))
        assert csr["spike_offsets"][-1] == sum(s.size for s in run.spikes)


# ── /edit/session ────────────────────────────────────────────────────────────


class TestEditSession:
    def test_embedded_emg_lives_in_a_store_the_next_open_deletes(
        self, client: TestClient, decomp_npz: Path, emg: np.ndarray
    ) -> None:
        def open_session() -> Any:
            meta, _ = _unpack_frame_response(
                client.post(
                    f"{API}/edit/session/open",
                    json={"path": str(decomp_npz)},
                    headers={SESSION_HEADER: "edit-store"},
                )
            )
            return cache._get_edit_session(meta["token"])

        first = open_session()
        assert first.store.path.is_dir()
        assert isinstance(first.signal.data, np.memmap)
        np.testing.assert_array_equal(first.signal.data, emg.astype(np.float32))
        second = open_session()
        assert not first.store.path.exists()
        assert second.store.path.is_dir()
        cache.close_session("edit-store")
        assert not second.store.path.exists()

    def test_update_filter(self, client: TestClient, edit_token: str) -> None:
        resp = client.post(
            f"{API}/edit/ops/update-filter",
            json={
                "token": edit_token,
                "mu": 0,
                "view_start": 500,
                "view_end": 4000,
                "nbextchan": 200,
                "project": "smoke",
            },
            headers={SESSION_HEADER: "fixture-edit"},
        )
        meta, arrays = _unpack_frame_response(resp)
        assert meta["changed"] == [0]
        assert meta["history"][-1]["type"] == "update_filter"
        spikes = arrays["spikes"]
        assert spikes.tolist() == sorted(spikes.tolist())

    def test_open_unsupported_format_is_400(self, client: TestClient, workspace: Path) -> None:
        path = workspace / "recording.otb4"
        path.write_bytes(b"")
        err = _err(client.post(f"{API}/edit/session/open", json={"path": str(path)}), 400)
        assert "Expected .mat or .npz" in err["detail"]["reason"]

    def test_session_save_exports_the_embedded_emg(
        self, client: TestClient, edit_token: str, workspace: Path
    ) -> None:
        data = _ok(
            client.post(
                f"{API}/edit/session/save",
                json={"token": edit_token, "project": "smoke", "remove_duplicates": False},
                headers={SESSION_HEADER: "fixture-edit"},
            )
        )
        assert data["saved"] is True
        assert {"path", "bids_emg_paths"} <= set(data)
        out = Path(data["path"])
        assert out.is_relative_to(workspace / "smoke")
        with np.load(out) as z:
            assert z["pulse_trains"].shape == (2, N_SAMPLES)


# ── /edit/save ───────────────────────────────────────────────────────────────


class TestEditSave:
    def test_save_writes_npz(
        self,
        client: TestClient,
        decomp_npz: Path,
        workspace: Path,
    ) -> None:
        data = _ok(
            client.post(
                f"{API}/edit/save",
                json={
                    "distimes": [[1000, 1400], [1200]],
                    "total_samples": N_SAMPLES,
                    "fsamp": FSAMP,
                    "grid_names": [GRID],
                    "project": "smoke",
                    "file_label": decomp_npz.name,
                    "artifact_regions": [[10, 20], {"start": 30, "end": 40}, "junk"],
                },
            )
        )
        assert data["saved"] is True
        assert "path" in data
        out = Path(data["path"])
        assert out.is_file() and out.suffix == ".npz"
        assert out.is_relative_to(workspace / "smoke")
        assert out.with_suffix(".json").is_file()
        with np.load(out) as z:
            assert {"schema_version", "spike_times", "spike_offsets", "fsamp"} <= set(z.files)
            assert "pulse_trains" not in z.files  # spikes only: no IPTs were sent
            assert z["artifact_intervals"].tolist() == [[10, 20], [30, 40]]

    def test_frame_body_saves_the_sent_pulse_trains_of_kept_mus(
        self, client: TestClient, decomp_npz: Path
    ) -> None:
        pulse = np.random.default_rng(2).random((3, N_SAMPLES)).astype(np.float32)
        meta = {
            "distimes": [[1000, 1400], [1200], [3000]],
            "flagged": [False, True, False],
            "remove_duplicates": False,
            "total_samples": N_SAMPLES,
            "fsamp": FSAMP,
            "grid_names": [GRID],
            "project": "smoke",
            "file_label": decomp_npz.name,
        }
        data = _ok(
            client.post(
                f"{API}/edit/save",
                content=bytes(pack_frame(meta, {"pulse_trains": (pulse, "f4")})),
                headers={"content-type": FRAME_MEDIA_TYPE},
            )
        )
        assert data["kept_indices"] == [0, 2]
        with np.load(data["path"]) as z:
            np.testing.assert_array_equal(z["pulse_trains"], pulse[[0, 2]])

    def test_run_save_uses_the_stored_run_result(
        self, client: TestClient, mu_run: dict[str, Any]
    ) -> None:
        preview = mu_run["preview"]
        token = preview["run_result_token"]
        stored = np.array(cache._get_run_result(token))
        entry = cache._get_run_result_entry(token)
        assert entry is not None
        data = _ok(
            client.post(
                f"{API}/edit/save",
                json={
                    "distimes": [s.tolist() for s in entry.spikes],
                    "remove_duplicates": False,
                    "total_samples": preview["total_samples"],
                    "fsamp": FSAMP,
                    "grid_names": [GRID],
                    "mu_grid_index": preview["mu_grid_index"],
                    "project": "smoke",
                    "file_label": "motor_units_decomposition.npz",
                    "run_result_token": token,
                },
            )
        )
        with np.load(data["path"]) as z:
            np.testing.assert_array_equal(z["pulse_trains"], stored)
        # The run explorer still reads it after the save.
        assert cache._get_run_result(token) is not None

    def test_run_save_takes_the_discharge_times_from_the_run(
        self, client: TestClient, mu_run: dict[str, Any]
    ) -> None:
        preview = mu_run["preview"]
        run = cache._get_run_result_entry(preview["run_result_token"])
        assert run is not None
        data = _ok(
            client.post(
                f"{API}/edit/save",
                json={
                    "run_result_token": preview["run_result_token"],
                    "remove_duplicates": False,
                    "total_samples": preview["total_samples"],
                    "fsamp": FSAMP,
                    "project": "smoke",
                    "file_label": "motor_units_run.npz",
                },
            )
        )
        with np.load(data["path"]) as z:
            rows = unpack_csr(z["spike_times"], z["spike_offsets"])
        assert [r.tolist() for r in rows] == [s.tolist() for s in run.spikes]

    def test_run_save_with_an_expired_run_is_400_and_writes_nothing(
        self, client: TestClient, workspace: Path
    ) -> None:
        # Before, the missing run meant no discharge times, and a file without motor units.
        derivatives = workspace / "smoke" / "derivatives"
        before = set(derivatives.rglob("*.npz")) if derivatives.exists() else set()
        err = _err(
            client.post(
                f"{API}/edit/save",
                json={
                    "run_result_token": "expired",
                    "total_samples": N_SAMPLES,
                    "fsamp": FSAMP,
                    "project": "smoke",
                    "file_label": "motor_units_expired.npz",
                },
            ),
            400,
        )
        assert err["detail"]["field"] == "run_result_token"
        after = set(derivatives.rglob("*.npz")) if derivatives.exists() else set()
        assert after == before

    def test_mismatched_pulse_trains_save_spikes_only(
        self, client: TestClient, decomp_npz: Path
    ) -> None:
        meta = {
            "distimes": [[1000, 1400]],
            "total_samples": N_SAMPLES,
            "fsamp": FSAMP,
            "project": "smoke",
            "file_label": decomp_npz.name,
        }
        data = _ok(
            client.post(
                f"{API}/edit/save",
                content=bytes(pack_frame(meta, {"pulse_trains": (np.ones((1, 10)), "f4")})),
                headers={"content-type": FRAME_MEDIA_TYPE},
            )
        )
        with np.load(data["path"]) as z:
            assert "pulse_trains" not in z.files
            assert z["spike_times"].tolist() == [1000, 1400]
        loaded = load_decomposition_file(data["path"])
        assert loaded.pulse_trains_full.shape == (1, N_SAMPLES)
        assert np.flatnonzero(loaded.pulse_trains_full[0]).tolist() == [1000, 1400]

    def test_malformed_frame_is_400(self, client: TestClient) -> None:
        resp = client.post(
            f"{API}/edit/save", content=b"MUB1junk", headers={"content-type": FRAME_MEDIA_TYPE}
        )
        assert "Invalid frame" in _err(resp, 400)["message"]

    def test_frame_without_total_samples_is_422(self, client: TestClient) -> None:
        resp = client.post(
            f"{API}/edit/save",
            content=bytes(pack_frame({"distimes": [[1]]}, {})),
            headers={"content-type": FRAME_MEDIA_TYPE},
        )
        _err(resp, 422, "validation_error")

    def test_missing_total_samples_is_422(self, client: TestClient) -> None:
        _err(client.post(f"{API}/edit/save", json={"distimes": [[1]]}), 422, "validation_error")

    def test_zero_total_samples_is_400(self, client: TestClient) -> None:
        _err(client.post(f"{API}/edit/save", json={"distimes": [[1]], "total_samples": 0}), 400)


# ── Origin, host and token restrictions (serve_api, desktop) ─────────────────


def _served_app(monkeypatch: pytest.MonkeyPatch, **env: str) -> tuple[FastAPI, str]:
    """The app and host ``serve_api`` would run with ``env``, without starting uvicorn."""
    import muedit.cli as cli

    served: dict[str, Any] = {}
    monkeypatch.setattr(
        cli.uvicorn, "run", lambda app, host, **_: served.update(app=app, host=host)
    )
    monkeypatch.delenv("MUEDIT_HOST", raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    cli.serve_api()
    return served["app"], served["host"]


class TestOriginAndHost:
    def test_binds_loopback_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _, host = _served_app(monkeypatch)
        assert host == "127.0.0.1"

    def test_no_other_origin_is_allowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        app, _ = _served_app(monkeypatch)
        with TestClient(app, base_url="http://127.0.0.1:8000") as c:
            for origin in ("http://localhost:8080", "https://evil.example"):
                r = c.options(
                    f"{API}/preview-by-path",
                    headers={"Origin": origin, "Access-Control-Request-Method": "POST"},
                )
                assert "access-control-allow-origin" not in r.headers

    def test_the_frontend_is_served_on_the_api_origin(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        app, _ = _served_app(monkeypatch)
        with TestClient(app, base_url="http://127.0.0.1:8000") as c:
            page = c.get("/")
            assert page.status_code == 200
            assert page.headers["content-type"].startswith("text/html")
            assert page.headers["cache-control"] == "no-cache"
            assert c.get("/app.js").status_code == 200
            # The API routes come first; an unknown API path is still the JSON error envelope.
            assert c.get(f"{API}/health").json()["data"]["status"] == "ok"
            _err(c.get(f"{API}/no-such-route"), 404)

    def test_rebound_host_is_rejected_on_loopback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        app, _ = _served_app(monkeypatch)
        with TestClient(app, base_url="http://127.0.0.1:8000") as c:
            assert c.get(f"{API}/health").status_code == 200
            assert c.get(f"{API}/health", headers={"Host": "localhost:8000"}).status_code == 200
            assert c.get(f"{API}/health", headers={"Host": "evil.example:8000"}).status_code == 400

    def test_explicit_all_interfaces_skips_the_host_check(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        app, host = _served_app(monkeypatch, MUEDIT_HOST="0.0.0.0")  # noqa: S104
        assert host == "0.0.0.0"  # noqa: S104
        with TestClient(app, base_url="http://192.168.1.20:8000") as c:
            assert c.get(f"{API}/health").status_code == 200
