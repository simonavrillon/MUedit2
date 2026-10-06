"""Application services for decomposition execution."""

from __future__ import annotations

import asyncio
import json
import logging
import multiprocessing
import queue
import tempfile
import threading
import traceback
from collections.abc import AsyncGenerator, Awaitable, Callable, Generator, Iterator
from multiprocessing.connection import wait
from multiprocessing.process import BaseProcess
from pathlib import Path
from typing import Any

import numpy as np
from fastapi import HTTPException

from muedit.api.cache import (
    HeldUpload,
    _hold_upload,
    _store_run_result,
)
from muedit.api.common import (
    make_json_safe,
    parse_discard_channels,
    parse_json_object,
    parse_rois,
)
from muedit.api.memory import DEFAULT_SESSION
from muedit.api.services.decompose_worker import (
    RESULT_STAGE,
    RunJob,
    child_main,
)
from muedit.editing.session import spike_array
from muedit.io.store import SessionStore
from muedit.models import IntArray

logger = logging.getLogger(__name__)

#: The preview fields a ``done`` event carries: the page already shows the recording the rest
#: describe, and the pulse trains and discharge times stay on the server under the run token.
DONE_PREVIEW_KEYS = ("mu_grid_index",)
#: How often a stream waiting for the next event checks that its client is still connected.
DISCONNECT_POLL_SEC = 1.0
SHUTDOWN_WAIT_SEC = 10.0
CANCELLED_EVENT: dict[str, Any] = {
    "stage": "cancelled",
    "pct": 0,
    "message": "Decomposition cancelled",
}


def _as_matrix(value: Any) -> np.ndarray:
    """Coerce a preview pulse payload into a 2-D matrix without copying an ndarray."""
    matrix = np.asarray(value if value is not None else [])
    if matrix.ndim == 1:
        matrix = matrix.reshape(1, -1) if matrix.size else np.zeros((0, 0))
    if matrix.ndim != 2:
        matrix = np.zeros((0, 0))
    return matrix


def _preview_spikes(preview: dict[str, Any]) -> list[IntArray]:
    """The preview's discharge times, as sorted int32 arrays."""
    return [spike_array(d) for d in preview.get("distime_all") or []]


class _Run:
    """One decomposition: its worker and a thread turning the worker's messages into events."""

    def __init__(self, job: RunJob, session: str, upload: HeldUpload) -> None:
        self.job = job
        self.session = session
        self.upload = upload
        self.events: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self.cancelled = threading.Event()
        self.store: SessionStore | None = None
        self._lock = threading.Lock()
        self._process: BaseProcess | None = None
        self._exitcode: int | None = None
        self._supervisor: threading.Thread | None = None

    def start(self) -> None:
        """Create the run's store and start supervising the worker."""
        self.store = SessionStore.create("run")
        self._supervisor = threading.Thread(
            target=self._supervise, name="muedit-decompose", daemon=True
        )
        self._supervisor.start()

    def cancel(self) -> None:
        """Stop the worker process."""
        with self._lock:
            self.cancelled.set()
            process = self._process
        if process is not None and process.is_alive():
            process.terminate()

    def join(self, timeout: float | None = None) -> None:
        """Wait until the run has ended and released what it held."""
        if self._supervisor is not None:
            self._supervisor.join(timeout)

    def _supervise(self) -> None:
        kept = False
        messages = self._process_messages()
        try:
            kept = self._relay(messages)
        except Exception as exc:
            logger.exception("Decomposition run failed")
            self.cancel()  # the worker must not outlive its run's store
            self.events.put(
                {
                    "stage": "error",
                    "pct": 100,
                    "message": "Decomposition failed",
                    "detail": str(exc),
                    "traceback": traceback.format_exc(),
                }
            )
        finally:
            messages.close()  # waits for the worker to exit
            if self.store is not None and not kept:
                self.store.close()
            self.upload.release()
            _finish(self)
            self.events.put(None)

    def _process_messages(self) -> Generator[dict[str, Any], None, None]:
        """Messages from a spawned worker process, until it exits."""
        assert self.store is not None
        ctx = multiprocessing.get_context("spawn")
        receiver, sender = ctx.Pipe(duplex=False)
        process = ctx.Process(
            target=child_main,
            args=(self.job, str(self.store.path), sender),
            name="muedit-decompose",
            daemon=True,
        )
        with receiver:
            try:
                with self._lock:
                    if self.cancelled.is_set():
                        return
                    process.start()
                    self._process = process
            finally:
                sender.close()  # the worker holds the only sending end: its exit ends the stream
            # Waits on the process too: on Windows a worker killed before it unpickled its
            # arguments leaves its sending end open in this process, so EOF never comes.
            # ``wait``, not ``poll``: on Windows ``poll`` raises on a pipe the worker broke.
            try:
                while True:
                    if receiver not in wait([receiver, process.sentinel]) and not wait(
                        [receiver], 0
                    ):
                        break  # the worker exited and left nothing to read
                    try:
                        yield receiver.recv()
                    except EOFError:
                        break
            finally:
                process.join()
                self._exitcode = process.exitcode

    def _relay(self, messages: Iterator[dict[str, Any]]) -> bool:
        """Forward the worker's events; True when the result keeps the run's store."""
        ended = False
        kept = False
        for message in messages:
            if message.get("stage") == RESULT_STAGE:
                done, kept = self._done_event(message)
                self.events.put(done)
                ended = True
            else:
                if message.get("stage") == "error":
                    logger.error(
                        "Decomposition failed: %s\n%s",
                        message.get("detail"),
                        message.get("traceback", ""),
                    )
                self.events.put(message)
                ended = ended or message.get("stage") == "error"
        if not ended:
            if self.cancelled.is_set():
                self.events.put(dict(CANCELLED_EVENT))
            else:
                logger.error("Decomposition worker exited with code %s", self._exitcode)
                self.events.put(
                    {
                        "stage": "error",
                        "pct": 100,
                        "message": "Decomposition worker terminated unexpectedly",
                        "detail": f"The worker process exited with code {self._exitcode}",
                    }
                )
        return kept

    def _done_event(self, message: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        """The ``done`` event for a finished run, and whether its pulse trains were kept."""
        preview_raw: dict[str, Any] = message.get("preview") or {}
        spikes = _preview_spikes(preview_raw)
        preview_payload = make_json_safe(
            {k: preview_raw[k] for k in DONE_PREVIEW_KEYS if k in preview_raw}
        )
        # The run save reads the pulse trains and discharge times from the run's store.
        pulse_full = _as_matrix(preview_raw.get("pulse_trains_full")).astype(np.float32, copy=False)
        kept = bool(pulse_full.size)
        if kept:
            preview_payload["run_result_token"] = _store_run_result(
                pulse_full, self.session, self.store, spikes
            )
        done = {
            "stage": "done",
            "summary": message.get("summary"),
            "preview": preview_payload,
            "pct": 100,
            "message": "Complete",
        }
        return done, kept


class _RunSlot:
    """The one run the server executes at a time."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.run: _Run | None = None


_SLOT = _RunSlot()


def _finish(run: _Run) -> None:
    """Let the next run start."""
    with _SLOT.lock:
        if _SLOT.run is run:
            _SLOT.run = None


def active_run() -> _Run | None:
    """The run in progress, if any."""
    with _SLOT.lock:
        return _SLOT.run


def start_decomposition(
    upload_token: str | None,
    options: dict[str, Any],
    *,
    session: str = DEFAULT_SESSION,
) -> _Run:
    """Start a run on the upload behind ``upload_token``; 409 while another one runs.

    ``options`` are the ``RunJob`` fields other than the run path and the signal.
    """
    with _SLOT.lock:
        if _SLOT.run is not None:
            raise HTTPException(
                status_code=409,
                detail="A decomposition is already running; wait for it to finish or cancel it",
            )
        held = _hold_upload(upload_token)
        if held is None:
            raise HTTPException(
                status_code=400,
                detail={
                    "field": "upload_token",
                    "reason": "Token expired or missing; reload the file via /preview-by-path",
                },
            )
        run_path = held.source_path or str(Path(tempfile.gettempdir()) / "muedit_cached_input")
        job = RunJob(run_path=run_path, signal=held.signal, **options)
        run = _Run(job, session, held)
        _SLOT.run = run
    try:
        run.start()
    except BaseException:
        held.release()
        _finish(run)
        raise
    return run


def cancel_decomposition(session: str | None = None) -> bool:
    """Stop the run in progress if ``session`` started it (any run when None)."""
    run = active_run()
    if run is None or (session is not None and run.session != session):
        return False
    run.cancel()
    return True


def stop_decompositions(timeout: float = SHUTDOWN_WAIT_SEC) -> None:
    """Cancel the run in progress and wait for it to release its stores, at shutdown."""
    run = active_run()
    if run is not None:
        run.cancel()
        run.join(timeout)


async def decomposition_event_stream(
    run: _Run, is_disconnected: Callable[[], Awaitable[bool]] | None = None
) -> AsyncGenerator[str, None]:
    """Yield the run's events as NDJSON lines; a client that goes away cancels the run."""
    ended = False
    try:
        while True:
            try:
                event = await asyncio.to_thread(run.events.get, timeout=DISCONNECT_POLL_SEC)
            except queue.Empty:
                if is_disconnected is not None and await is_disconnected():
                    break
                continue
            if event is None:
                ended = True
                break
            yield json.dumps(make_json_safe(event)) + "\n"
    finally:
        if not ended:
            run.cancel()


def parse_stream_options(
    *,
    roi_start: int | None,
    roi_end: int | None,
    rois: str | None,
    discard_channels: str | None,
    bids_entities: str | None,
    bids_metadata: str | None,
    artifact_regions: str | None = None,
) -> tuple[
    tuple[int, int] | None,
    list[tuple[int, int]] | None,
    list[list[int]] | None,
    dict | None,
    dict | None,
    list[tuple[int, int]] | None,
]:
    """Parse optional stream route form inputs into typed decomposition options."""
    roi = None
    if roi_start is not None and roi_end is not None:
        roi = (int(roi_start), int(roi_end))

    return (
        roi,
        parse_rois(rois),
        parse_discard_channels(discard_channels),
        parse_json_object(bids_entities, "bids_entities"),
        parse_json_object(bids_metadata, "bids_metadata"),
        parse_rois(artifact_regions, "artifact_regions"),
    )
