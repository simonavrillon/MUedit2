"""Decompositions in a worker process: one at a time, cancellable, isolated (plan stage 11)."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import time
from collections.abc import Callable, Iterator
from multiprocessing.connection import Connection
from multiprocessing.reduction import ForkingPickler
from pathlib import Path
from typing import Any, TypeVar

import numpy as np
import pytest
import scipy.io
from starlette.testclient import TestClient

from muedit.api import cache
from muedit.api.services import decompose_service
from muedit.api.services.decompose_service import (
    WORKER_ENV,
    active_run,
    decomposition_event_stream,
    start_decomposition,
)
from muedit.api.services.decompose_worker import RunJob
from muedit.io.store import sessions_dir
from muedit.models import resident_nbytes
from tests._synthetic_emg import motor_unit_emg

API = "/api/v1"
SESSION_HEADER = "X-MUedit-Session"
FSAMP = 2000.0
N_SAMPLES = 20_000
QUICK = {"niter": 10, "nbextchan": 400}
SLOW = {"niter": 5000, "nbextchan": 400}  # outlasts every test that cancels it

T = TypeVar("T")


def _die(job: RunJob, store_path: str, conn: Connection) -> None:
    """A worker that crashes as soon as it starts."""
    os._exit(3)


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    from muedit.api.app_factory import create_app
    from muedit.api.routes import include_routers

    app = create_app()
    include_routers(app)
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture(scope="module")
def recording(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("worker") / "motor_units.mat"
    scipy.io.savemat(
        path,
        {
            "signal": {
                "data": motor_unit_emg(seed=3, n_samples=N_SAMPLES, fsamp=FSAMP),
                "fsamp": FSAMP,
                "gridname": "GR08MM1305",
                "muscle": "ta",
                "auxiliary": np.zeros((0, N_SAMPLES)),
                "auxiliaryname": [],
            }
        },
    )
    return path


@pytest.fixture(autouse=True)
def _no_run_left() -> Iterator[None]:
    assert active_run() is None
    yield
    run = active_run()
    if run is not None:
        run.cancel()
        run.join(60)
    assert active_run() is None


def _wait_for(probe: Callable[[], T | None], timeout: float = 60.0) -> T:
    deadline = time.monotonic() + timeout
    while (value := probe()) is None:
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.01)
    return value


def _upload(client: TestClient, recording: Path, session: str) -> str:
    resp = client.post(
        f"{API}/preview-by-path", json={"path": str(recording)}, headers={SESSION_HEADER: session}
    )
    assert resp.status_code == 200, resp.text
    return str(resp.json()["data"]["upload_token"])


def _run(
    client: TestClient, token: str, session: str, params: dict[str, Any], **form: str
) -> list[dict[str, Any]]:
    resp = client.post(
        f"{API}/decompose_stream",
        data={"upload_token": token, "params": json.dumps(params), **form},
        headers={SESSION_HEADER: session},
    )
    assert resp.status_code == 200, resp.text
    return [json.loads(line) for line in resp.text.splitlines()]


def _closed(folder: Path) -> bool:
    """Whether a closed store's folder is gone; Windows keeps it while this process maps it."""
    return sys.platform == "win32" or not folder.exists()


def _run_folders() -> set[Path]:
    return set(sessions_dir().glob("run-*"))


class _Background:
    """A run posted from another thread, as another browser tab would."""

    def __init__(
        self, client: TestClient, token: str, session: str, params: dict[str, Any]
    ) -> None:
        self.events: list[dict[str, Any]] = []
        self.thread = threading.Thread(
            target=lambda: self.events.extend(_run(client, token, session, params))
        )
        self.thread.start()
        self.run = _wait_for(active_run)

    def worker_started(self) -> Any:
        """The worker process once it runs."""
        return _wait_for(
            lambda: (
                self.run._process
                if self.run._process is not None and self.run._process.is_alive()
                else None
            )
        )

    def finish(self) -> list[dict[str, Any]]:
        self.thread.join(120)
        assert not self.thread.is_alive()
        assert self.events
        return self.events


class TestWorkerProcess:
    def test_matches_the_thread_run_exactly(
        self, client: TestClient, recording: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        results = {}
        for mode in ("process", "thread"):
            monkeypatch.setenv(WORKER_ENV, mode)
            token = _upload(client, recording, f"parity-{mode}")
            events = _run(client, token, f"parity-{mode}", QUICK, full_preview="true")
            done = events[-1]
            assert done["stage"] == "done", done
            pulse = cache._get_run_result(done["preview"].pop("run_result_token"))
            assert pulse is not None
            done["preview"].pop("preview_binary_token")
            results[mode] = (events[:-1], done, np.array(pulse))
        process_events, process_done, process_pulse = results["process"]
        thread_events, thread_done, thread_pulse = results["thread"]
        assert process_done["summary"]["mu_count"] > 0
        assert process_events == thread_events
        assert process_done == thread_done
        np.testing.assert_array_equal(process_pulse, thread_pulse)

    def test_pulse_trains_come_back_as_the_run_store_file(
        self, client: TestClient, recording: Path
    ) -> None:
        token = _upload(client, recording, "files")
        done = _run(client, token, "files", QUICK, full_preview="true")[-1]
        entry = cache._RUN_RESULTS.get(done["preview"]["run_result_token"])
        assert entry is not None and entry.store is not None
        assert resident_nbytes(entry.pulse_trains) == 0
        assert (entry.store.path / "pulse_trains.npy").is_file()

    def test_the_upload_goes_to_the_worker_as_file_locations(
        self, client: TestClient, recording: Path
    ) -> None:
        signal = cache._get_upload_signal(_upload(client, recording, "locations"))
        assert signal is not None
        job = RunJob(
            run_path="x", signal=signal, params_raw=None, duration=None, persist_output=False
        )
        assert len(ForkingPickler.dumps(job)) < 16 * 1024 < signal.data.nbytes

    def test_a_crashed_worker_is_an_error_event(
        self, client: TestClient, recording: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        token = _upload(client, recording, "crash")
        before = _run_folders()
        monkeypatch.setattr(decompose_service, "child_main", _die)
        last = _run(client, token, "crash", QUICK)[-1]
        assert last["stage"] == "error"
        assert "terminated unexpectedly" in last["message"]
        assert "code 3" in last["detail"]
        assert _run_folders() == before
        monkeypatch.undo()
        assert _run(client, token, "crash", QUICK)[-1]["stage"] == "done"

    def test_a_failure_in_the_server_part_is_an_error_event(
        self, client: TestClient, recording: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def broken(*_: Any) -> memoryview:
            raise RuntimeError("frame encoding broke")

        token = _upload(client, recording, "relay")
        before = _run_folders()
        monkeypatch.setattr(decompose_service, "_encode_decompose_preview", broken)
        last = _run(client, token, "relay", QUICK)[-1]
        assert last["stage"] == "error"
        assert last["detail"] == "frame encoding broke"
        assert _run_folders() == before


class TestOneRunAtATime:
    def test_a_second_run_is_409_until_the_first_is_cancelled(
        self, client: TestClient, recording: Path
    ) -> None:
        token = _upload(client, recording, "tab-a")
        first = _Background(client, token, "tab-a", SLOW)
        process = first.worker_started()
        assert first.run.store is not None
        run_folder = first.run.store.path
        resp = client.post(
            f"{API}/decompose_stream",
            data={"upload_token": token, "params": json.dumps(QUICK)},
            headers={SESSION_HEADER: "tab-b"},
        )
        assert resp.status_code == 409
        assert "already running" in resp.json()["error"]["message"]

        other = client.post(f"{API}/decompose/cancel", headers={SESSION_HEADER: "tab-b"})
        assert other.json()["data"]["cancelled"] is False
        mine = client.post(f"{API}/decompose/cancel", headers={SESSION_HEADER: "tab-a"})
        assert mine.json()["data"]["cancelled"] is True

        assert first.finish()[-1]["stage"] == "cancelled"
        assert process.exitcode != 0
        assert not run_folder.exists()
        assert _run(client, token, "tab-b", QUICK)[-1]["stage"] == "done"

    def test_cancel_without_a_run(self, client: TestClient) -> None:
        resp = client.post(f"{API}/decompose/cancel", headers={SESSION_HEADER: "idle"})
        assert resp.json()["data"]["cancelled"] is False

    def test_the_thread_fallback_stops_at_its_next_progress_event(
        self, client: TestClient, recording: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(WORKER_ENV, "thread")
        token = _upload(client, recording, "thread-cancel")
        run = _Background(client, token, "thread-cancel", SLOW)
        assert decompose_service.cancel_decomposition("thread-cancel")
        assert run.finish()[-1]["stage"] == "cancelled"

    def test_closing_the_tab_cancels_its_run(self, client: TestClient, recording: Path) -> None:
        token = _upload(client, recording, "closing")
        upload = cache._UPLOADS.get(token)
        assert upload is not None and upload.store is not None
        run = _Background(client, token, "closing", SLOW)
        run.worker_started()
        resp = client.post(f"{API}/session/close", params={"session": "closing"})
        assert resp.status_code == 204
        assert run.finish()[-1]["stage"] == "cancelled"
        assert _closed(upload.store.path)

    def test_an_upload_dropped_mid_run_stays_until_the_run_ends(
        self, client: TestClient, recording: Path
    ) -> None:
        token = _upload(client, recording, "drop")
        upload = cache._UPLOADS.get(token)
        assert upload is not None and upload.store is not None
        run = _Background(client, token, "drop", QUICK)
        cache._release_upload("drop")
        assert upload.store.path.exists()
        assert run.finish()[-1]["stage"] == "done"
        assert _closed(upload.store.path)


class TestStreamEnd:
    def _start(self, client: TestClient, recording: Path, session: str) -> Any:
        return start_decomposition(
            _upload(client, recording, session),
            {"params_raw": json.dumps(SLOW), "duration": None, "persist_output": False},
            binary_preview=True,
            session=session,
        )

    def test_a_client_that_leaves_cancels_the_run(
        self, client: TestClient, recording: Path
    ) -> None:
        run = self._start(client, recording, "leaves")

        async def first_event_then_leave() -> dict[str, Any]:
            stream = decomposition_event_stream(run)
            line = await anext(stream)
            await stream.aclose()
            return dict(json.loads(line))

        assert asyncio.run(first_event_then_leave())["stage"] == "start"
        run.join(60)
        assert run.cancelled.is_set()
        assert active_run() is None

    def test_a_disconnected_client_cancels_a_quiet_run(
        self, client: TestClient, recording: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(decompose_service, "DISCONNECT_POLL_SEC", 0.05)
        run = self._start(client, recording, "disconnected")

        async def disconnected() -> bool:
            return True

        async def drain() -> list[str]:
            return [line async for line in decomposition_event_stream(run, disconnected)]

        asyncio.run(drain())
        run.join(60)
        assert run.cancelled.is_set()
        assert active_run() is None

    def test_shutdown_stops_the_run(self, client: TestClient, recording: Path) -> None:
        run = self._start(client, recording, "shutdown")
        _wait_for(lambda: run._process)
        decompose_service.stop_decompositions()
        assert active_run() is None
        assert not run.store.path.exists()
