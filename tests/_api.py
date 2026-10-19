"""Helpers for the API tests: the app's client, its response envelopes, and stored runs."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import scipy.io
from httpx import Response
from starlette.testclient import TestClient

from muedit.api import cache
from muedit.api.binary import FRAME_FORMAT, FRAME_MEDIA_TYPE
from muedit.models import FloatArray, spike_array
from tests._frames import unpack_frame

API = "/api/v1"
FSAMP = 2000.0
GRID = "GR08MM1305"
N_CHANNELS = 64
#: Samples of the ``signal_mat`` recording.
N_SAMPLES = 6000
SESSION_HEADER = "X-MUedit-Session"
PLACEMENT = "Grid over the muscle belly, located by palpation."
#: Every run exports into a BIDS dataset, which needs a subject and the placement.
ENTITIES = {"subject": "01", "task": "smoke", "placement_scheme_description": PLACEMENT}


def write_recording(
    path: Path, data: np.ndarray, aux: np.ndarray | None = None, aux_names: list[str] | None = None
) -> Path:
    """A MUedit ``signal`` .mat: one ``GRID`` on ``ta`` at ``FSAMP``, with ``aux`` rows if given."""
    n_samples = data.shape[1]
    scipy.io.savemat(
        path,
        {
            "signal": {
                "data": data,
                "fsamp": FSAMP,
                "gridname": GRID,
                "muscle": "ta",
                "device_name": "Synthetic",
                "auxiliary": aux if aux is not None else np.zeros((0, n_samples)),
                "auxiliaryname": np.array(aux_names or [], dtype=object),
            }
        },
    )
    return path


def upload(client: TestClient, path: Path, session: str) -> str:
    """Load ``path`` in tab ``session``, as the QC stage does, and return its upload token."""
    resp = client.post(
        f"{API}/preview-by-path", json={"path": str(path)}, headers={SESSION_HEADER: session}
    )
    return str(ok(resp)["upload_token"])


@contextmanager
def api_client(data_root: Path) -> Iterator[TestClient]:
    """The app as ``cli.serve_api`` builds it, writing its projects under ``data_root``."""
    from muedit.api import config
    from muedit.api.app_factory import create_app
    from muedit.api.routes import include_routers

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(config, "DATA_ROOT", data_root)
        app = create_app()
        include_routers(app)
        with TestClient(app, raise_server_exceptions=False) as client:
            yield client


def ok(resp: Response) -> Any:
    """Assert a 200 success envelope and return its ``data``."""
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == {"data", "meta"}
    assert body["meta"] == {"api_version": "v1"}
    return body["data"]


def error(resp: Response, status: int, code: str | None = None) -> dict[str, Any]:
    """Assert an error envelope with the given status and return ``error``."""
    assert resp.status_code == status, resp.text
    body = resp.json()
    assert set(body) == {"error"}
    error = body["error"]
    assert {"code", "message"} <= set(error)
    assert error["code"] == (code or f"http_{status}")
    return dict(error)


def frame(resp: Response) -> tuple[dict[str, Any], dict[str, np.ndarray]]:
    """Assert a MUB1 frame response and decode it."""
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == FRAME_MEDIA_TYPE
    assert resp.headers["x-muedit-format"] == FRAME_FORMAT
    return unpack_frame(resp.content)


def run_result(token: str | None) -> cache.RunResult | None:
    """The run kept under ``token``, or None."""
    with cache.held_run_result(token) as run:
        return run


def stored_run(
    spike_times: list[list[int]],
    pulse_trains: FloatArray | None = None,
    session: str = "default",
    extras: cache.RunExtras | None = None,
) -> str:
    """The token of a finished run that kept these discharge times (and pulse trains)."""
    trains = pulse_trains if pulse_trains is not None else np.zeros((0, 0), np.float32)
    spikes = [spike_array(s) for s in spike_times]
    return cache.store_run_result(trains, session, None, spikes, extras)
