"""The body of a decomposition run, executed in a spawned worker process."""

from __future__ import annotations

import contextlib
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Any

from muedit.api.common import make_json_safe
from muedit.app_log import log_to_inherited_file
from muedit.decomp.pipeline import run_decomposition
from muedit.decomp.types import DecompositionParameters, DecompositionResult
from muedit.models import SignalImport
from muedit.storage import ArrayStore, SessionStore

#: Stage of the message carrying the finished run, which the server turns into ``done``.
RESULT_STAGE = "result"

Send = Callable[[dict[str, Any]], None]


@dataclass
class RunJob:
    """What a run decomposes and how; memory-mapped arrays pickle as their file locations."""

    run_path: str
    signal: SignalImport
    params: DecompositionParameters
    persist_output: bool = False
    rois: list[tuple[int, int]] | None = None
    discard_channels: list[list[int]] | None = None
    bids_root: str | None = None
    bids_entities: dict[str, Any] | None = None
    bids_metadata: dict[str, Any] | None = None
    include_full_preview: bool = False
    artifact_regions: list[tuple[int, int]] | None = None
    target_index: int | None = None  # the auxiliary channel the user segmented


def execute(job: RunJob, store: ArrayStore, send: Send) -> None:
    """Run ``job`` into ``store``: progress events, then one result or error message."""

    def progress(stage: str, payload: dict[str, Any]) -> None:
        event = {"stage": stage}
        event.update({k: make_json_safe(v) for k, v in payload.items()})
        send(event)

    try:
        result = run_decomposition(
            job.run_path,
            params=job.params,
            save_npz=job.persist_output or job.bids_root is not None,
            progress_cb=progress,
            rois=job.rois,
            discard_overrides=job.discard_channels,
            bids_root=job.bids_root,
            bids_entities=job.bids_entities,
            bids_metadata=job.bids_metadata,
            include_full_preview=job.include_full_preview,
            preloaded_signal=job.signal,
            artifact_regions=job.artifact_regions,
            store=store,
            target_index=job.target_index,
        )
        summary = make_json_safe(_summary(result, job.persist_output))
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
    send({"stage": RESULT_STAGE, "preview": result.preview, "summary": summary})


def _summary(result: DecompositionResult, persisted: bool) -> dict[str, Any]:
    """What the run page's result panel shows."""
    pulse = result.pulse_trains
    return {
        "fsamp": result.fsamp,
        "grid_names": result.grid_names,
        "mu_count": len(result.discharge_times),
        "pulse_length": int(pulse.shape[1]) if pulse.size else 0,
        "discard_channels": result.discard_channels,
        "save_path": result.save_path if persisted else None,
        "parameters": result.parameters,
    }


def child_main(job: RunJob, store_path: str, conn: Connection) -> None:
    """Worker process: run ``job`` into the run store at ``store_path``, reporting over ``conn``."""
    log_to_inherited_file()
    # A closed pipe means the server cancelled the run or is shutting down.
    with contextlib.suppress(BrokenPipeError, EOFError), conn:
        execute(job, SessionStore(Path(store_path)), conn.send)
