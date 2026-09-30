"""The body of a decomposition run, executed in a worker process (or a thread as a fallback)."""

from __future__ import annotations

import contextlib
import threading
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Any

from muedit.api.common import build_params, make_json_safe, summarize_result
from muedit.app_log import log_to_inherited_file
from muedit.decomp.pipeline import run_decomposition
from muedit.io.store import ArrayStore, SessionStore
from muedit.models import SignalImport

#: Stage of the message carrying the finished run, which the server turns into ``done``.
RESULT_STAGE = "result"
CANCELLED_EVENT: dict[str, Any] = {
    "stage": "cancelled",
    "pct": 0,
    "message": "Decomposition cancelled",
}

Send = Callable[[dict[str, Any]], None]


@dataclass
class RunJob:
    """What a run decomposes and how; memory-mapped arrays pickle as their file locations."""

    run_path: str
    signal: SignalImport
    params_raw: str | None
    duration: float | None
    persist_output: bool
    roi: tuple[int, int] | None = None
    rois: list[tuple[int, int]] | None = None
    discard_channels: list[list[int]] | None = None
    bids_root: str | None = None
    bids_entities: dict[str, Any] | None = None
    bids_metadata: dict[str, Any] | None = None
    include_full_preview: bool = False
    artifact_regions: list[tuple[int, int]] | None = None


class RunCancelled(Exception):
    """Stops a thread run at its next progress event."""


def execute(
    job: RunJob, store: ArrayStore, send: Send, cancelled: threading.Event | None = None
) -> None:
    """Run ``job`` into ``store``: progress events, then one result, error or cancelled message."""

    def progress(stage: str, payload: dict[str, Any]) -> None:
        if cancelled is not None and cancelled.is_set():
            raise RunCancelled
        if stage == "done":
            return
        event = {"stage": stage}
        event.update({k: make_json_safe(v) for k, v in payload.items()})
        send(event)

    try:
        result, save_path = run_decomposition(
            job.run_path,
            duration=job.duration,
            manual_roi=False,
            params=build_params(job.params_raw),
            save_npz=job.persist_output or job.bids_root is not None,
            progress_cb=progress,
            roi=job.roi,
            rois=job.rois,
            discard_overrides=job.discard_channels,
            bids_root=job.bids_root,
            bids_entities=job.bids_entities,
            bids_metadata=job.bids_metadata,
            include_full_preview=job.include_full_preview,
            preloaded_signal=job.signal,
            artifact_regions=job.artifact_regions,
            store=store,
        )
        summary = make_json_safe(summarize_result(result, save_path, job.persist_output))
    except RunCancelled:
        send(dict(CANCELLED_EVENT))
        return
    except Exception as exc:  # noqa: BLE001
        send(
            {
                "stage": "error",
                "pct": 100,
                "message": "Decomposition failed",
                "detail": str(exc),
                "traceback": traceback.format_exc(),
            }
        )
        return
    send({"stage": RESULT_STAGE, "preview": result.get("preview", {}), "summary": summary})


def child_main(job: RunJob, store_path: str, conn: Connection) -> None:
    """Worker process: run ``job`` into the run store at ``store_path``, reporting over ``conn``."""
    log_to_inherited_file()
    # A closed pipe means the server cancelled the run or is shutting down.
    with contextlib.suppress(BrokenPipeError, EOFError), conn:
        execute(job, SessionStore(Path(store_path)), conn.send)
