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
from starlette.routing import Route
from starlette.testclient import TestClient

from tests.conftest import REPO_ROOT

FSAMP = 2000.0
N_CHANNELS = 64
N_SAMPLES = 6000
GRID = "GR08MM1305"
API = "/api/v1"

LIVE_ENDPOINTS: list[tuple[str, str]] = [
    ("GET", "/health"),
    ("GET", "/dialog/open-file"),
    ("POST", "/preview-by-path"),
    ("POST", "/qc/window"),
    ("POST", "/qc/auto"),
    ("POST", "/decompose_stream"),
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
]

ENVELOPE_KEYS = {"data", "meta"}


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
    return _ok(client.post(f"{API}/preview-by-path", json={"path": str(signal_mat)}))[
        "upload_token"
    ]


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
def edit_signal_token(client: TestClient, decomp_npz: Path) -> str:
    data = _ok(
        client.post(
            f"{API}/edit/load-by-path",
            json={"path": str(decomp_npz)},
            headers={"x-muedit-binary": "0"},
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


def _unpack_json_f32(
    blob: bytes, magic: bytes, n_arrays: int
) -> tuple[dict, list[tuple[int, int]]]:
    """Decode ``pack_json_f32_payload`` output; check the byte length adds up."""
    assert blob[:4] == magic
    version, meta_len = struct.unpack("<II", blob[4:12])
    assert version == 1
    shapes = [struct.unpack("<II", blob[12 + 8 * i : 20 + 8 * i]) for i in range(n_arrays)]
    head = 12 + 8 * n_arrays
    meta = json.loads(blob[head : head + meta_len])
    assert len(blob) == head + meta_len + sum(4 * r * c for r, c in shapes)
    return meta, shapes


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
        registered = {
            (method, route.path)
            for route in app.routes
            if isinstance(route, Route)
            for method in route.methods or ()
        }
        missing = [(m, API + p) for m, p in LIVE_ENDPOINTS if (m, API + p) not in registered]
        assert not missing

    def test_frontend_static_routes_are_live(self) -> None:
        """Every literal path in ``routes.js`` is covered by this tier."""
        js = (REPO_ROOT / "frontend" / "src" / "api" / "routes.js").read_text()
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
    def test_binary_payload(self, client: TestClient, stream_events: list[dict[str, Any]]) -> None:
        token = stream_events[-1]["preview"]["preview_binary_token"]
        resp = client.get(f"{API}/decompose_preview/{token}")
        assert resp.status_code == 200
        assert resp.headers["x-muedit-format"] == "decompose-preview-f32-v1"
        meta, shapes = _unpack_json_f32(resp.content, b"MDPV", 2)
        assert meta["pulse_dtype"] == "float32"
        declared = [meta["pulse_trains_full_shape"], meta["pulse_trains_all_shape"]]
        assert [tuple(s) for s in declared] == shapes

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


def _check_meld(resp: Response) -> dict[str, Any]:
    assert resp.status_code == 200
    assert resp.headers["x-muedit-format"] == "edit-load-f32-v1"
    meta, shapes = _unpack_json_f32(resp.content, b"MELD", 1)
    assert set(meta) >= EDIT_LOAD_KEYS
    assert meta["pulse_binary"] is True
    assert shapes == [(2, N_SAMPLES)] == [tuple(meta["pulse_shape"])]
    assert len(meta["distime_all"]) == 2
    return meta


class TestEditLoad:
    def test_by_path_binary(self, client: TestClient, decomp_npz: Path) -> None:
        meta = _check_meld(client.post(f"{API}/edit/load-by-path", json={"path": str(decomp_npz)}))
        assert meta["file_label"] == decomp_npz.name
        assert "project" in meta

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
