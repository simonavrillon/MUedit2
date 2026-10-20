"""Decompositions (``decompose_service``, ``decompose_worker``): one at a time, in a worker process."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from collections.abc import Callable, Iterator
from multiprocessing.connection import Connection
from multiprocessing.reduction import ForkingPickler
from pathlib import Path
from typing import Any, TypeVar, cast

import numpy as np
import pytest
from starlette.testclient import TestClient

from muedit.api import cache
from muedit.api.schemas import DecomposePayload
from muedit.api.services import decompose_service, decompose_worker
from muedit.api.services.decompose_service import (
    active_run,
    decomposition_event_stream,
    start_decomposition,
)
from muedit.api.services.decompose_worker import INTERNAL_ERROR_DETAIL, RunJob
from muedit.decomp.types import DecompositionParameters
from muedit.errors import MueditError
from muedit.models import SignalImport, resident_nbytes
from muedit.storage import RamStore, sessions_dir
from tests._api import (
    API,
    ENTITIES,
    FSAMP,
    N_CHANNELS,
    SESSION_HEADER,
    error,
    upload,
    write_recording,
)
from tests._platform import deleted
from tests._synthetic_emg import motor_unit_emg

N_RUN_SAMPLES = 20_000
QUICK = {"niter": 10, "nbextchan": 400}
SLOW = {"niter": 5000, "nbextchan": 400}  # outlasts every test that cancels it
T = TypeVar("T")


def _die(job: RunJob, store_path: str, conn: Connection) -> None:
    """A worker that crashes as soon as it starts."""
    os._exit(3)


def _fail_with_a_traceback(job: RunJob, store_path: str, conn: Connection) -> None:
    """A worker whose run failed on a bug: its error event carries the traceback."""
    conn.send(
        {
            "stage": "error",
            "message": "Decomposition failed",
            "detail": INTERNAL_ERROR_DETAIL,
            "traceback": "Traceback (most recent call last): ...",
        }
    )
    conn.close()


@pytest.fixture(scope="module")
def recording(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A recording with motor units in it, which a quick run finds."""
    emg = motor_unit_emg(seed=3, n_samples=N_RUN_SAMPLES, fsamp=FSAMP)
    return write_recording(tmp_path_factory.mktemp("worker") / "motor_units.mat", emg)


@pytest.fixture(scope="module")
def mu_run(client: TestClient, recording: Path) -> list[dict[str, Any]]:
    """The events of a run that finds motor units, started as the app starts one."""
    token = upload(client, recording, "fixture-mu-run")
    events = _run(
        client, token, "fixture-mu-run", QUICK, artifact_regions=[{"start": 100, "end": 400}]
    )
    assert events[-1]["stage"] == "done", events[-1]
    assert events[-1]["summary"]["mu_count"] > 0
    return events


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


def _run(
    client: TestClient, token: str, session: str, params: dict[str, Any], **fields: Any
) -> list[dict[str, Any]]:
    resp = client.post(
        f"{API}/decompose_stream",
        json={"upload_token": token, "params": params, "bids_entities": ENTITIES, **fields},
        headers={SESSION_HEADER: session},
    )
    assert resp.status_code == 200, resp.text
    return [json.loads(line) for line in resp.text.splitlines()]


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


class TestDecomposeStream:
    def test_event_sequence(self, mu_run: list[dict[str, Any]]) -> None:
        stages = [e["stage"] for e in mu_run]
        assert stages[0] == "start" and stages[-1] == "done"
        assert set(stages) <= {"start", "progress", "done"}

    def test_done_event_shape(self, mu_run: list[dict[str, Any]]) -> None:
        done = mu_run[-1]
        assert {"summary", "preview", "message"} <= set(done)
        assert set(done["summary"]) == {"grid_names", "mu_count", "parameters"}
        assert done["summary"]["parameters"]["niter"] == QUICK["niter"]
        # The page shows the recording already: only the MUs' grids and the run's token come back.
        assert set(done["preview"]) == {"mu_grid_index", "run_result_token"}
        assert len(done["preview"]["mu_grid_index"]) == done["summary"]["mu_count"]

    def test_the_run_keeps_its_trains_and_discharges_in_its_store(
        self, mu_run: list[dict[str, Any]]
    ) -> None:
        done = mu_run[-1]
        entry = cache._RUN_RESULTS.get(done["preview"]["run_result_token"])
        assert entry is not None and entry.store is not None
        assert (entry.store.path / "pulse_trains.npy").is_file()
        assert resident_nbytes(entry.pulse_trains) == 0
        assert entry.pulse_trains.dtype == np.float32
        n_mu = done["summary"]["mu_count"]
        assert entry.pulse_trains.shape == (n_mu, N_RUN_SAMPLES)
        assert len(entry.spike_times) == n_mu


class TestRejected:
    @pytest.mark.parametrize(
        ("body", "loc"),
        [
            ({"params": [1, 2]}, ["body", "params"]),
            ({"params": {"contrast_func": "skwe"}}, ["body", "params", "contrast_func"]),
            ({"rois": [[100, 400]]}, ["body", "rois", 0]),
        ],
        ids=["params-not-an-object", "unknown-contrast", "roi-not-a-span"],
    )
    def test_bad_body_is_422_before_any_run(
        self, client: TestClient, upload_token: str, body: dict[str, Any], loc: list[Any]
    ) -> None:
        resp = client.post(f"{API}/decompose_stream", json={"upload_token": upload_token, **body})
        assert error(resp, 422, "validation_error")["detail"][0]["loc"] == loc

    def test_missing_token_is_422(self, client: TestClient) -> None:
        resp = client.post(f"{API}/decompose_stream", json={"params": {}})
        assert error(resp, 422, "validation_error")["detail"][0]["loc"] == ["body", "upload_token"]

    @pytest.mark.parametrize(
        ("body", "field"),
        [
            ({"upload_token": "gone"}, "upload_token"),
            ({"project": "../outside"}, "project"),
            ({"discard_channels": [[1] * N_CHANNELS]}, "discard_channels"),
            ({"bids_entities": {"subject": "01"}}, "bids_entities"),
        ],
        ids=["expired-token", "path-as-project", "every-channel-discarded", "no-placement"],
    )
    def test_a_bad_field_is_400_before_any_run(
        self, client: TestClient, upload_token: str, body: dict[str, Any], field: str
    ) -> None:
        payload = {"upload_token": upload_token, "bids_entities": ENTITIES, **body}
        resp = client.post(f"{API}/decompose_stream", json=payload)
        assert error(resp, 400)["detail"]["field"] == field
        assert active_run() is None

    def test_a_run_that_fails_to_start_releases_its_upload(
        self, client: TestClient, recording: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        token = upload(client, recording, "tab-fail")
        entry = cache._UPLOADS.get(token)
        assert entry is not None

        def fail(**kwargs: Any) -> None:
            raise ValueError("bad job")

        monkeypatch.setattr(decompose_service, "RunJob", fail)
        payload = DecomposePayload.model_validate(
            {"upload_token": token, "bids_entities": ENTITIES}
        )
        with pytest.raises(ValueError):
            decompose_service.start_decomposition(payload, session="tab-fail")
        assert active_run() is None
        cache.close_session("tab-fail")
        assert deleted(entry.store.path)


class TestWorkerProcess:
    def test_the_upload_goes_to_the_worker_as_file_locations(
        self, client: TestClient, recording: Path
    ) -> None:
        held = cache.hold_upload(upload(client, recording, "locations"))
        assert held is not None
        held.release()
        signal = held.signal
        job = RunJob(
            run_path="x", signal=signal, params=DecompositionParameters(), bids_root="bids"
        )
        assert len(ForkingPickler.dumps(job)) < 16 * 1024 < signal.data.nbytes

    def test_a_crashed_worker_is_an_error_event(
        self, client: TestClient, recording: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        token = upload(client, recording, "crash")
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
        def broken(*_: Any) -> str:
            raise RuntimeError("result packing broke")

        token = upload(client, recording, "relay")
        before = _run_folders()
        monkeypatch.setattr(decompose_service, "store_run_result", broken)
        last = _run(client, token, "relay", QUICK)[-1]
        assert last == {
            "stage": "error",
            "message": "Decomposition failed",
            "detail": INTERNAL_ERROR_DETAIL,
        }
        assert all(deleted(path) for path in _run_folders() - before)


class TestErrorEvents:
    def _execute_raising(
        self, monkeypatch: pytest.MonkeyPatch, exc: Exception
    ) -> list[dict[str, Any]]:
        def fail(*_: Any, **__: Any) -> None:
            raise exc

        monkeypatch.setattr(decompose_worker, "run_decomposition", fail)
        sent: list[dict[str, Any]] = []
        job = RunJob(
            run_path="x",
            signal=cast("SignalImport", None),
            params=DecompositionParameters(),
            bids_root="bids",
        )
        decompose_worker.execute(job, RamStore(), sent.append)
        return sent

    def test_a_user_fixable_failure_says_what_to_change(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sent = self._execute_raising(monkeypatch, MueditError("Unknown grid model 'X'"))
        assert sent == [
            {
                "stage": "error",
                "message": "Decomposition failed",
                "detail": "Unknown grid model 'X'",
            }
        ]

    def test_a_bug_keeps_its_message_and_traceback_for_the_server(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (event,) = self._execute_raising(monkeypatch, RuntimeError("secret internal detail"))
        assert event["detail"] == INTERNAL_ERROR_DETAIL
        assert "secret internal detail" in event["traceback"]

    def test_the_traceback_is_logged_and_never_reaches_the_client(
        self,
        client: TestClient,
        recording: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        token = upload(client, recording, "traceback")
        monkeypatch.setattr(decompose_service, "child_main", _fail_with_a_traceback)
        with caplog.at_level(logging.INFO, logger=decompose_service.logger.name):
            last = _run(client, token, "traceback", QUICK)[-1]
        assert last == {
            "stage": "error",
            "message": "Decomposition failed",
            "detail": INTERNAL_ERROR_DETAIL,
        }
        failed = [r for r in caplog.records if r.getMessage().startswith("Decomposition failed")]
        assert [r.levelno for r in failed] == [logging.ERROR]
        assert "Traceback (most recent call last)" in failed[0].getMessage()


class TestOneRunAtATime:
    def test_a_second_run_is_409_until_the_first_is_cancelled(
        self, client: TestClient, recording: Path
    ) -> None:
        token = upload(client, recording, "tab-a")
        first = _Background(client, token, "tab-a", SLOW)
        process = first.worker_started()
        assert first.run.store is not None
        run_folder = first.run.store.path
        resp = client.post(
            f"{API}/decompose_stream",
            json={"upload_token": token, "params": QUICK, "bids_entities": ENTITIES},
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

    def test_closing_the_tab_cancels_its_run(self, client: TestClient, recording: Path) -> None:
        token = upload(client, recording, "closing")
        entry = cache._UPLOADS.get(token)
        assert entry is not None and entry.store is not None
        run = _Background(client, token, "closing", SLOW)
        run.worker_started()
        resp = client.post(f"{API}/session/close", params={"session": "closing"})
        assert resp.status_code == 204
        assert run.finish()[-1]["stage"] == "cancelled"
        assert deleted(entry.store.path)

    def test_an_upload_dropped_mid_run_stays_until_the_run_ends(
        self, client: TestClient, recording: Path
    ) -> None:
        token = upload(client, recording, "drop")
        entry = cache._UPLOADS.get(token)
        assert entry is not None and entry.store is not None
        run = _Background(client, token, "drop", QUICK)
        cache.release_upload("drop")
        assert entry.store.path.exists()
        assert run.finish()[-1]["stage"] == "done"
        assert deleted(entry.store.path)


class TestStreamEnd:
    def _start(self, client: TestClient, recording: Path, session: str) -> Any:
        token = upload(client, recording, session)
        payload = DecomposePayload.model_validate(
            {"upload_token": token, "params": SLOW, "bids_entities": ENTITIES}
        )
        return start_decomposition(payload, session=session)

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
