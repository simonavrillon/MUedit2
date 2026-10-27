"""Application services for decomposition execution."""

from __future__ import annotations

import asyncio
import json
import logging
import multiprocessing
import queue
import threading
from collections.abc import AsyncGenerator, Awaitable, Callable, Generator, Iterator
from multiprocessing.connection import wait
from multiprocessing.process import BaseProcess
from typing import Any

import numpy as np

from muedit.api.cache import (
    HeldUpload,
    RunExtras,
    hold_upload,
    store_run_result,
)
from muedit.api.common import bids_root_for, make_json_safe, missing_upload
from muedit.api.memory import DEFAULT_SESSION
from muedit.api.schemas import DecomposePayload, Span
from muedit.api.services.decompose_worker import (
    INTERNAL_ERROR_DETAIL,
    RESULT_STAGE,
    RunJob,
    child_main,
)
from muedit.errors import ConflictError, MueditError
from muedit.io.bids import check_run_export
from muedit.models import spike_array
from muedit.storage import SessionStore

logger = logging.getLogger(__name__)

#: How often a stream waiting for the next event checks that its client is still connected.
DISCONNECT_POLL_SEC = 1.0
SHUTDOWN_WAIT_SEC = 10.0
CANCELLED_EVENT: dict[str, Any] = {
    "stage": "cancelled",
    "message": "Decomposition cancelled",
}


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
        try:
            self._supervisor = threading.Thread(
                target=self._supervise, name="muedit-decompose", daemon=True
            )
            self._supervisor.start()
        except BaseException:
            self.store.close()
            raise

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
        except Exception:
            logger.exception("Decomposition run failed")
            self.cancel()  # the worker must not outlive its run's store
            self.events.put(
                {
                    "stage": "error",
                    "message": "Decomposition failed",
                    "detail": INTERNAL_ERROR_DETAIL,
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
                    # Only a bug carries a traceback; it stays in the log.
                    trace = message.pop("traceback", None)
                    if trace:
                        logger.error("Decomposition failed\n%s", trace)
                    else:
                        logger.info("Decomposition failed: %s", message.get("detail"))
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
                        "message": "Decomposition worker terminated unexpectedly",
                        "detail": f"The worker process exited with code {self._exitcode}",
                    }
                )
        return kept

    def _done_event(self, message: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        """A run's ``done`` event (just its MU-to-grid index), and whether its trains were kept."""
        preview: dict[str, Any] = message.get("preview") or {}
        preview_payload = make_json_safe({"mu_grid_index": preview.get("mu_grid_index", [])})
        pulse_trains = preview.get("pulse_trains_full")
        kept = pulse_trains is not None
        if kept:
            preview_payload["run_result_token"] = store_run_result(
                np.asarray(pulse_trains, np.float32),
                self.session,
                self.store,
                [spike_array(d) for d in preview.get("spike_times") or []],
                RunExtras(
                    sil=list(message.get("sil") or []),
                    rois=[(int(s), int(e)) for s, e in message.get("rois") or []] or None,
                    target_segments=message.get("target_segments"),
                ),
            )
        done = {
            "stage": "done",
            "summary": message.get("summary"),
            "preview": preview_payload,
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


def _spans(spans: list[Span] | None) -> list[tuple[int, int]] | None:
    return None if spans is None else [(s.start, s.end) for s in spans]


def start_decomposition(payload: DecomposePayload, *, session: str = DEFAULT_SESSION) -> _Run:
    """Start a run on the upload behind ``payload.upload_token``; 409 while another one runs."""
    for i, mask in enumerate(payload.discard_channels or []):
        if mask and all(mask):
            raise MueditError(
                f"Grid {i + 1} has every channel discarded; keep at least one.", "discard_channels"
            )
    bids_root = bids_root_for(payload.project)
    with _SLOT.lock:
        if _SLOT.run is not None:
            raise ConflictError(
                "A decomposition is already running; wait for it to finish or cancel "
                "it in the tab that started it"
            )
        held = hold_upload(payload.upload_token)
        if held is None:
            raise missing_upload()
        try:
            # The error the run's BIDS export would raise, before anything is computed.
            check_run_export(bids_root, payload.bids_entities or {}, held.signal.metadata)
            job = RunJob(
                run_path=held.source_path,
                signal=held.signal,
                params=payload.params,
                bids_root=str(bids_root),
                rois=_spans(payload.rois),
                discard_channels=payload.discard_channels,
                bids_entities=payload.bids_entities,
                artifact_regions=_spans(payload.artifact_regions),
                target_index=payload.target_index,
            )
            run = _Run(job, session, held)
        except BaseException:
            held.release()
            raise
        _SLOT.run = run
    try:
        run.start()
    except BaseException:
        held.release()
        _finish(run)
        raise
    return run


def cancel_decomposition(session: str) -> bool:
    """Stop the run in progress if ``session`` started it."""
    run = active_run()
    if run is None or run.session != session:
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
                # Each open stream keeps one thread of the default executor waiting here.
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
