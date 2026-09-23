"""Edit round-trip fidelity: load -> edit -> save must not alter untouched data.

The editor works on a display copy of the pulse trains.  Whatever the browser
holds, the saved ``pulse_trains`` of every motor unit the user did not
recompute must be bit-identical to the source file.
"""

from __future__ import annotations

import json
import struct
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from starlette.testclient import TestClient

API = "/api/v1"
FSAMP = 2000.0
N_SAMPLES = 8000
GRIDS = ["GR08MM1305", "GR08MM1305"]
MU_GRID_INDEX = [0, 0, 0, 1, 1, 1]
FILE_LABEL = "sub-01_task-fidelity_decomp.npz"
ENTITY = "sub-01_task-fidelity"


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def workspace(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    import muedit.api.config as config
    import muedit.api.services.editing_service as editing_service

    root = tmp_path_factory.mktemp("edit_fidelity")
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
def source_pulse() -> np.ndarray:
    """Full-precision float64 pulse trains, none of them exactly float32-representable."""
    rng = np.random.default_rng(7)
    return rng.standard_normal((len(MU_GRID_INDEX), N_SAMPLES)) * rng.uniform(1.0, 500.0, (6, 1))


@pytest.fixture(scope="module")
def source_distimes() -> list[list[int]]:
    return [[400 + 350 * k + 40 * mu for k in range(20)] for mu in range(len(MU_GRID_INDEX))]


@pytest.fixture(scope="module")
def decomp_npz(workspace: Path, source_pulse: np.ndarray, source_distimes: list[list[int]]) -> Path:
    from muedit.decomp.decomposition_file import save_decomposition_npz

    path = workspace / FILE_LABEL
    save_decomposition_npz(
        path,
        pulse_trains=source_pulse,
        distimes=source_distimes,
        fsamp=FSAMP,
        grid_names=GRIDS,
        mu_grid_index=MU_GRID_INDEX,
        muscles=["ta"],
        parameters={},
        total_samples=N_SAMPLES,
    )
    return path


# ── A minimal stand-in for the browser ───────────────────────────────────────


def _decode_meld(blob: bytes) -> tuple[dict[str, Any], np.ndarray]:
    """Decode the MELD edit-load payload the way the frontend does (float64 matrix)."""
    assert blob[:4] == b"MELD"
    version, meta_len, rows, cols = struct.unpack("<IIII", blob[4:20])
    assert version == 1
    meta = json.loads(blob[20 : 20 + meta_len])
    matrix = np.frombuffer(blob[20 + meta_len :], dtype="<f8").reshape(rows, cols)
    return meta, matrix.astype(np.float64)


def _uids(mu_grid_index: list[int]) -> list[str]:
    counts: dict[int, int] = {}
    out = []
    for g in mu_grid_index:
        out.append(f"g{g}_mu{counts.get(g, 0)}")
        counts[g] = counts.get(g, 0) + 1
    return out


def _load_like_browser(client: TestClient, path: Path) -> tuple[dict[str, Any], np.ndarray]:
    resp = client.post(f"{API}/edit/load-by-path", json={"path": str(path)})
    assert resp.status_code == 200, resp.text
    return _decode_meld(resp.content)


def _save_like_browser(
    client: TestClient,
    meta: dict[str, Any],
    browser_pulse: np.ndarray,
    distimes: list[list[int]],
    **overrides: Any,
) -> np.ndarray:
    """Send what ``saveEditedFile`` sends, then read the written ``pulse_trains`` back."""
    payload: dict[str, Any] = {
        "distimes": distimes,
        "flagged": [False] * len(distimes),
        "total_samples": meta["total_samples"],
        "fsamp": meta["fsamp"],
        "grid_names": meta["grid_names"],
        "mu_grid_index": meta["mu_grid_index"],
        "mu_uids": _uids(meta["mu_grid_index"]),
        "parameters": {},
        "muscle": [],
        "edit_history": [],
        "artifact_times": [[] for _ in distimes],
        "file_label": FILE_LABEL,
        "entity_label": ENTITY,
        "project": "fidelity",
        "remove_flagged": False,
        "remove_duplicates": False,
    }
    # The browser only uploads the matrix when the server did not hand it a session.
    if meta.get("session_token"):
        payload["session_token"] = meta["session_token"]
    else:
        payload["pulse_trains"] = browser_pulse.tolist()
    payload.update(overrides)
    resp = client.post(f"{API}/edit/save", json=payload)
    assert resp.status_code == 200, resp.text
    saved = np.load(resp.json()["data"]["path"], allow_pickle=True)
    return np.asarray(saved["pulse_trains"])


# ── The contract ─────────────────────────────────────────────────────────────


def test_save_without_edits_is_bit_identical(
    client: TestClient, decomp_npz: Path, source_pulse: np.ndarray
) -> None:
    meta, browser_pulse = _load_like_browser(client, decomp_npz)
    out = _save_like_browser(client, meta, browser_pulse, meta["distime_all"])
    assert out.shape == source_pulse.shape
    assert np.array_equal(out, source_pulse)


def test_editing_one_mu_leaves_every_other_mu_bit_identical(
    client: TestClient, decomp_npz: Path, source_pulse: np.ndarray, source_distimes: list[list[int]]
) -> None:
    meta, browser_pulse = _load_like_browser(client, decomp_npz)
    edited = [list(t) for t in meta["distime_all"]]
    edited[2] = sorted(edited[2] + [1234])  # a spike added to MU 2: integers only
    out = _save_like_browser(client, meta, browser_pulse, edited)
    others = [i for i in range(len(edited)) if i != 2]
    assert np.array_equal(out[others], source_pulse[others])


def test_removing_a_flagged_mu_keeps_the_others_bit_identical(
    client: TestClient, decomp_npz: Path, source_pulse: np.ndarray
) -> None:
    meta, browser_pulse = _load_like_browser(client, decomp_npz)
    flagged = [False] * len(meta["distime_all"])
    flagged[1] = True
    out = _save_like_browser(
        client, meta, browser_pulse, meta["distime_all"], flagged=flagged, remove_flagged=True
    )
    kept = [i for i in range(len(flagged)) if i != 1]
    assert out.shape[0] == len(kept)
    assert np.array_equal(out, source_pulse[kept])


def test_discharge_times_and_metadata_survive_the_round_trip(
    client: TestClient, decomp_npz: Path, source_distimes: list[list[int]]
) -> None:
    """Integers and labels are exact today: this must keep holding at every step."""
    meta, browser_pulse = _load_like_browser(client, decomp_npz)
    edited = [list(t) for t in source_distimes]
    edited[0] = sorted(edited[0] + [999])
    payload_path = client.post(
        f"{API}/edit/save",
        json={
            "distimes": edited,
            "flagged": [False] * len(edited),
            "total_samples": meta["total_samples"],
            "fsamp": meta["fsamp"],
            "grid_names": meta["grid_names"],
            "mu_grid_index": meta["mu_grid_index"],
            "mu_uids": _uids(meta["mu_grid_index"]),
            "parameters": {},
            "muscle": [],
            "edit_history": [],
            "artifact_times": [[] for _ in edited],
            "file_label": FILE_LABEL,
            "entity_label": ENTITY,
            "project": "fidelity",
            "remove_flagged": False,
            "remove_duplicates": False,
            **(
                {"session_token": meta["session_token"]}
                if meta.get("session_token")
                else {"pulse_trains": browser_pulse.tolist()}
            ),
        },
    ).json()["data"]["path"]
    saved = np.load(payload_path, allow_pickle=True)
    assert [list(map(int, d)) for d in saved["discharge_times"]] == edited
    assert float(saved["fsamp"]) == FSAMP
    assert list(saved["mu_grid_index"]) == MU_GRID_INDEX
    assert int(saved["total_samples"]) == N_SAMPLES
