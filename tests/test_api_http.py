"""HTTP smoke tier: one FastAPI ``TestClient`` pass per live endpoint."""

from __future__ import annotations

import json
import re
import struct
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
    ("POST", "/qc/window"),
    ("POST", "/qc/auto"),
    ("POST", "/decompose_stream"),
    ("POST", "/decompose/cancel"),
    ("GET", "/decompose_preview/{token}"),
    ("POST", "/edit/load-by-path"),
    ("POST", "/edit/save"),
    ("POST", "/edit/update-filter"),
    ("POST", "/edit/add-spikes"),
    ("POST", "/edit/add-artifact"),
    ("POST", "/edit/delete-spikes"),
    ("POST", "/edit/delete-dr"),
    ("POST", "/edit/remove-outliers"),
    ("POST", "/edit/remove-duplicates"),
    ("POST", "/edit/flag-mu"),
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
    import muedit.api.services.editing_service as editing_service

    root = tmp_path_factory.mktemp("api_http")
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
    from muedit.decomp.decomposition_file import pack_object_array, save_decomposition_npz

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
        extras={
            "emg_data": emg,
            "discard_channels": pack_object_array([np.zeros(N_CHANNELS, dtype=int)]),
            "coordinates": pack_object_array([np.zeros((N_CHANNELS, 2))]),
        },
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
def edit_signal_token(client: TestClient, decomp_npz: Path) -> str:
    data = _ok(
        client.post(
            f"{API}/edit/load-by-path",
            json={"path": str(decomp_npz)},
            headers={"x-muedit-binary": "0", SESSION_HEADER: "fixture-edit"},
        )
    )
    return data["edit_signal_token"]


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


def _unpack_mqcr(blob: bytes) -> dict[str, Any]:
    """Decode the MQCR v1 QC-window payload."""
    assert blob[:4] == b"MQCR"
    (version,) = struct.unpack("<I", blob[4:8])
    grid, ch, start, end, total = struct.unpack("<5i", blob[8:28])
    (fsamp,) = struct.unpack("<f", blob[28:32])
    (n,) = struct.unpack("<I", blob[32:36])
    off, channels = 36, []
    for _ in range(n):
        idx, size = struct.unpack("<iI", blob[off : off + 8])
        off += 8 + 4 * size
        channels.append((idx, size))
    assert off == len(blob)
    return {
        "version": version,
        "grid": grid,
        "channel": ch,
        "start": start,
        "end": end,
        "total": total,
        "fsamp": fsamp,
        "channels": channels,
    }


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
            "edit_signal_contexts",
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
    "mean_abs",
    "grid_mean_abs",
    "grid_names",
    "total_samples",
    "fsamp",
    "channel_means",
    "coordinates",
    "metadata",
    "muscle",
    "auxiliary",
    "auxiliary_names",
}


def _check_preview(data: dict[str, Any]) -> None:
    assert set(data) >= PREVIEW_KEYS
    assert isinstance(data["upload_token"], str) and data["upload_token"]
    assert data["grid_names"] == [GRID]
    assert data["total_samples"] == N_SAMPLES
    assert data["fsamp"] == FSAMP
    assert len(data["grid_mean_abs"]) == 1
    assert len(data["channel_means"]) == 1 and len(data["channel_means"][0]) == N_CHANNELS
    assert len(data["coordinates"]) == 1 and len(data["coordinates"][0]) == N_CHANNELS
    assert isinstance(data["mean_abs"], list) and data["mean_abs"]


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


# ── /qc/window ───────────────────────────────────────────────────────────────


class TestQcWindow:
    def test_all_channels_binary(self, client: TestClient, upload_token: str) -> None:
        resp = client.post(
            f"{API}/qc/window",
            json={
                "upload_token": upload_token,
                "start": 0,
                "end": 2000,
                "target_fs": 500.0,
            },
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/octet-stream"
        assert resp.headers["x-muedit-format"] == "qc-raw-f32-v1"
        dec = _unpack_mqcr(resp.content)
        assert dec["version"] == 1
        assert (dec["grid"], dec["channel"], dec["start"], dec["end"]) == (0, -1, 0, 2000)
        assert dec["total"] == N_SAMPLES
        assert dec["fsamp"] == FSAMP
        assert [idx for idx, _ in dec["channels"]] == list(range(N_CHANNELS))
        assert all(size > 0 for _, size in dec["channels"])

    def test_single_channel_binary(self, client: TestClient, upload_token: str) -> None:
        resp = client.post(
            f"{API}/qc/window",
            json={
                "upload_token": upload_token,
                "channel_index": 5,
            },
        )
        assert resp.status_code == 200
        dec = _unpack_mqcr(resp.content)
        assert dec["channel"] == 5
        assert dec["end"] == N_SAMPLES
        assert [idx for idx, _ in dec["channels"]] == [5]

    @pytest.mark.parametrize(
        "body",
        [{"upload_token": "expired"}, {"grid_index": 1}, {"channel_index": N_CHANNELS}],
        ids=["bad-token", "bad-grid", "bad-channel"],
    )
    def test_rejected(self, client: TestClient, upload_token: str, body: dict[str, Any]) -> None:
        _err(client.post(f"{API}/qc/window", json={"upload_token": upload_token, **body}), 400)

    def test_missing_token_is_422(self, client: TestClient) -> None:
        _err(client.post(f"{API}/qc/window", json={}), 422, "validation_error")


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
        assert "pulse_trains_all" not in preview
        assert "run_result_token" not in preview  # no motor units, nothing to save

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
            },
            headers={"x-muedit-binary": "0"},
        )
        done = json.loads(resp.text.splitlines()[-1])
        assert done["stage"] == "done"
        assert "preview_binary_token" not in done["preview"]

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
        assert meta["distime_all"] == mu_run["preview"]["distime_all"]
        full = arrays["pulse_trains_full"]
        assert full.dtype == np.float32
        assert full.shape == (mu_run["summary"]["mu_count"], meta["total_samples"])
        np.testing.assert_array_equal(
            full, cache._get_run_result(mu_run["preview"]["run_result_token"])
        )
        assert arrays["pulse_trains_all"].shape == (0, 0)
        _err(client.get(f"{API}/decompose_preview/{token}"), 404)

    def test_unknown_token_is_404(self, client: TestClient) -> None:
        _err(client.get(f"{API}/decompose_preview/not-a-token"), 404)


# ── /edit/load-by-path ───────────────────────────────────────────────────────

EDIT_LOAD_KEYS = {
    "distime_all",
    "edit_signal_token",
    "file_label",
    "fsamp",
    "grid_names",
    "mu_grid_index",
    "muscle",
    "parameters",
    "rois",
    "sil",
    "total_samples",
}


class TestEditLoad:
    def test_by_path_binary(self, client: TestClient, decomp_npz: Path) -> None:
        meta, arrays = _unpack_frame_response(
            client.post(f"{API}/edit/load-by-path", json={"path": str(decomp_npz)})
        )
        assert set(meta) >= EDIT_LOAD_KEYS | {"project"}
        assert "pulse_trains_full" not in meta
        assert meta["file_label"] == decomp_npz.name
        assert len(meta["distime_all"]) == 2
        with np.load(decomp_npz, allow_pickle=True) as z:
            saved = z["pulse_trains"]
        assert arrays["pulse_trains_full"].dtype == np.float32
        np.testing.assert_array_equal(arrays["pulse_trains_full"], saved.astype(np.float32))

    def test_by_path_json(self, client: TestClient, decomp_npz: Path) -> None:
        data = _ok(
            client.post(
                f"{API}/edit/load-by-path",
                json={"path": str(decomp_npz)},
                headers={"x-muedit-binary": "0"},
            )
        )
        assert EDIT_LOAD_KEYS | {"pulse_trains_full", "project"} <= set(data)
        assert np.asarray(data["pulse_trains_full"]).shape == (2, N_SAMPLES)

    def test_by_path_empty_is_400(self, client: TestClient) -> None:
        assert _err(client.post(f"{API}/edit/load-by-path", json={"path": ""}), 400)["message"] == (
            "path is required"
        )

    @pytest.mark.parametrize("header", ["1", "0"], ids=["binary", "json"])
    def test_by_path_nonexistent_is_404(
        self,
        client: TestClient,
        workspace: Path,
        header: str,
    ) -> None:
        resp = client.post(
            f"{API}/edit/load-by-path",
            json={"path": str(workspace / "no.npz")},
            headers={"x-muedit-binary": header},
        )
        assert _err(resp, 404)["detail"]["field"] == "path"

    def test_by_path_unsupported_format_is_400(self, client: TestClient, workspace: Path) -> None:
        path = workspace / "recording.otb4"
        path.write_bytes(b"")
        err = _err(client.post(f"{API}/edit/load-by-path", json={"path": str(path)}), 400)
        assert "Expected .mat or .npz" in err["detail"]["reason"]


# ── /edit/save ───────────────────────────────────────────────────────────────


class TestEditSave:
    def test_save_writes_npz_and_bids(
        self,
        client: TestClient,
        decomp_npz: Path,
        edit_signal_token: str,
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
                    "edit_signal_token": edit_signal_token,
                    "artifact_regions": [[10, 20], {"start": 30, "end": 40}, "junk"],
                },
            )
        )
        assert data["saved"] is True
        assert {"path", "bids_emg_paths"} <= set(data)
        out = Path(data["path"])
        assert out.is_file() and out.suffix == ".npz"
        assert out.is_relative_to(workspace / "smoke")
        assert out.with_suffix(".json").is_file()
        with np.load(out, allow_pickle=True) as z:
            assert {"pulse_trains", "discharge_times", "fsamp", "artifact_mask"} <= set(z.files)
            assert int(z["artifact_mask"].sum()) == 20

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
        with np.load(data["path"], allow_pickle=True) as z:
            np.testing.assert_array_equal(z["pulse_trains"], pulse[[0, 2]])

    def test_run_save_uses_the_stored_run_result(
        self, client: TestClient, mu_run: dict[str, Any]
    ) -> None:
        preview = mu_run["preview"]
        token = preview["run_result_token"]
        stored = np.array(cache._get_run_result(token))
        data = _ok(
            client.post(
                f"{API}/edit/save",
                json={
                    "distimes": preview["distime_all"],
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
        with np.load(data["path"], allow_pickle=True) as z:
            np.testing.assert_array_equal(z["pulse_trains"], stored)
        assert cache._get_run_result(token) is None

    def test_mismatched_pulse_trains_fall_back_to_discharge_times(
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
        with np.load(data["path"], allow_pickle=True) as z:
            assert z["pulse_trains"].shape == (1, N_SAMPLES)
            assert np.flatnonzero(z["pulse_trains"][0]).tolist() == [1000, 1400]

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


# ── /edit/update-filter ──────────────────────────────────────────────────────


class TestEditUpdateFilter:
    def _body(self, token: str, **extra: Any) -> dict[str, Any]:
        return {
            "project": "smoke",
            "edit_signal_token": token,
            "file_label": "sub-01_task-smoke_decomp.npz",
            "distimes": [[1000, 1400, 1800, 2200, 2600, 3000]],
            "pulse_train": [0.0] * N_SAMPLES,
            "view_start": 500,
            "view_end": 4000,
            "nbextchan": 200,
            **extra,
        }

    def test_shape(self, client: TestClient, edit_signal_token: str) -> None:
        data = _ok(client.post(f"{API}/edit/update-filter", json=self._body(edit_signal_token)))
        assert set(data) == {"fsamp", "distimes", "pulse_train"}
        assert data["fsamp"] == FSAMP
        assert data["distimes"] == sorted(data["distimes"])
        assert len(data["pulse_train"]) == N_SAMPLES


# ── Origin and host restrictions (serve_api) ─────────────────────────────────


def _served_app(monkeypatch: pytest.MonkeyPatch, **env: str) -> tuple[FastAPI, str]:
    """The app and host ``serve_api`` would run with ``env``, without starting uvicorn."""
    import muedit.cli as cli

    served: dict[str, Any] = {}
    monkeypatch.setattr(
        cli.uvicorn, "run", lambda app, host, **_: served.update(app=app, host=host)
    )
    for key in ("MUEDIT_HOST", "MUEDIT_FRONTEND_PORT", "MUEDIT_ALLOWED_ORIGINS"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    cli.serve_api()
    return served["app"], served["host"]


def _preflight(client: TestClient, origin: str) -> Response:
    return client.options(
        f"{API}/preview-by-path",
        headers={"Origin": origin, "Access-Control-Request-Method": "POST"},
    )


class TestOriginAndHost:
    def test_binds_loopback_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _, host = _served_app(monkeypatch)
        assert host == "127.0.0.1"

    def test_only_the_frontend_origin_is_allowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        app, _ = _served_app(monkeypatch)
        with TestClient(app, base_url="http://127.0.0.1:8000") as c:
            for origin in ("http://localhost:8080", "http://127.0.0.1:8080"):
                r = _preflight(c, origin)
                assert r.status_code == 200
                assert r.headers["access-control-allow-origin"] == origin
            r = _preflight(c, "https://evil.example")
            assert r.status_code == 400
            assert "access-control-allow-origin" not in r.headers

    def test_the_frontend_may_send_its_session_header(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        app, _ = _served_app(monkeypatch)
        with TestClient(app, base_url="http://127.0.0.1:8000") as c:
            r = c.options(
                f"{API}/preview-by-path",
                headers={
                    "Origin": "http://localhost:8080",
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": "content-type,x-muedit-session",
                },
            )
            assert r.status_code == 200
            assert "x-muedit-session" in r.headers["access-control-allow-headers"].lower()

    def test_frontend_port_and_origin_overrides(self, monkeypatch: pytest.MonkeyPatch) -> None:
        app, _ = _served_app(monkeypatch, MUEDIT_FRONTEND_PORT="9090")
        with TestClient(app, base_url="http://127.0.0.1:8000") as c:
            assert _preflight(c, "http://localhost:9090").status_code == 200
            assert _preflight(c, "http://localhost:8080").status_code == 400
        app, _ = _served_app(monkeypatch, MUEDIT_ALLOWED_ORIGINS="http://lab-pc:8080, ")
        with TestClient(app, base_url="http://127.0.0.1:8000") as c:
            assert _preflight(c, "http://lab-pc:8080").status_code == 200
            assert _preflight(c, "http://localhost:8080").status_code == 400

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
