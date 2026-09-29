"""tracemalloc peak per app stage on a mid-size synthetic recording, against a scaled budget."""

from __future__ import annotations

import gc
import tracemalloc
from collections.abc import Callable
from pathlib import Path
from typing import Any

import h5py
import numpy as np
import pytest
import scipy.io

from muedit.api.schemas import EditFilterPayload, QcAutoPayload
from muedit.api.services import editing_service, preview_service
from muedit.api.services.series_service import series_frame
from muedit.decomp.core import decompose_step
from muedit.decomp.pipeline import run_decomposition
from muedit.decomp.postprocess import export_step, postprocess_step
from muedit.decomp.preprocess import load_step, preprocess_step
from muedit.decomp.types import DEFAULT_NBEXTCHAN, DecompositionParameters
from muedit.io.factory import load_signal
from muedit.io.store import SessionStore
from tests._report import record
from tests._synthetic_emg import FSAMP, N_CHANNELS, motor_unit_emg

# ── Mid-size scale ────────────────────────────────────────────────────────────

#: 60 s @ 2000 Hz, one 64-channel grid.  Big enough that every stage holds a
#: real working set (the extended window alone is ~0.5 GB), small enough for a
#: CI runner.
N_SAMPLES = 120_000
#: Central 30-s decomposition ROI.
ROI = (30_000, 90_000)
#: FastICA iterations: enough to populate filters for every postprocess branch.
NITER = 10
GRID_NAME = "GR08MM1305"

EXT_FACTOR = round(DEFAULT_NBEXTCHAN / N_CHANNELS)  # 16, as in the app default
EXT_ROWS = N_CHANNELS * EXT_FACTOR  # extended rows per window/full trace
ROI_SAMPLES = ROI[1] - ROI[0]

RAW_MB = N_CHANNELS * N_SAMPLES * 8 / 1e6
EXT_WINDOW_MB = EXT_ROWS * ROI_SAMPLES * 8 / 1e6

# ── Per-stage budgets (latest baseline x 1.25) ────────────────────────────────
#
# Each budget is ``factor x working-set MB`` where the working set is the
# smallest full-size array the stage must materialise.  The factors are the
# tracemalloc baseline on this input plus 25% headroom, re-measured when a
# memory-plan stage lowers them (stage 3: preprocess, edit_load, update_filter;
# stages 4-5: decompose, update_filter and the three postprocess branches;
# stages 6-7: preview, preprocess and save; stages 9-10: preview, preprocess,
# full-trace and adaptive, with the loaded, filtered and pulse arrays memory-mapped
# in session stores; stage 12: qc_auto, and ``series`` replacing ``qc_window``).
# A factor that grows means the stage started keeping an
# extra full copy.  ``load`` reads a v5 .mat, which scipy can only read whole;
# ``load_store`` reads the same recording as v7.3 into a store, slice by slice.
# Full-trace and adaptive stream the recording, so their budgets are no longer
# multiples of the full-length extension: full-trace holds one ~64 MB batch,
# adaptive one calibration chunk; their pulse trains are in the run store.
# ``series`` reads the min/max pyramids, so it depends on the bins asked for, not on
# the recording: its budget is in MB.

BUDGETS_MB: dict[str, float] = {
    "load": 1.9 * RAW_MB,
    "load_store": 0.35 * RAW_MB,
    "preview": 0.65 * RAW_MB,
    "series": 2.0,
    "qc_auto": 3.2 * RAW_MB,
    "preprocess": 0.47 * RAW_MB,
    "decompose": 1.55 * EXT_WINDOW_MB,
    "post_windowed": 1.45 * EXT_WINDOW_MB,
    "save": 1.55 * RAW_MB,
    "post_full": 1.5 * RAW_MB,
    "post_adaptive": 2.05 * RAW_MB,
    "edit_load": 2.0 * RAW_MB,
    "update_filter": 6.7 * RAW_MB,
}


# ── Measurement helpers ───────────────────────────────────────────────────────


def _peak_mb(fn: Callable[[], Any]) -> tuple[Any, float]:
    """Run ``fn`` under tracemalloc and return ``(result, peak traced MB)``."""
    gc.collect()
    tracemalloc.start()
    try:
        out = fn()
    finally:
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
    return out, peak / 1e6


def _check(stage: str, peak_mb: float) -> None:
    budget_mb = BUDGETS_MB[stage]
    record(
        "memory",
        caption=(
            "tracemalloc peak per stage, mid-size synthetic "
            f"({N_CHANNELS} ch x {N_SAMPLES} samples, ROI {ROI_SAMPLES})"
        ),
        stage=stage,
        peak_mb=round(peak_mb, 1),
        budget_mb=round(budget_mb, 1),
        headroom=round(budget_mb - peak_mb, 1),
    )
    assert peak_mb <= budget_mb, (
        f"{stage}: tracemalloc peak {peak_mb:.1f} MB exceeds budget "
        f"{budget_mb:.1f} MB (a new full copy in this stage?)"
    )


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def mat_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The synthetic recording on disk, as a loader-readable ``.mat``."""
    path = tmp_path_factory.mktemp("memory_regression") / "mid_size.mat"
    scipy.io.savemat(
        str(path),
        {
            "signal": {
                "data": motor_unit_emg(seed=7, n_samples=N_SAMPLES, fsamp=FSAMP, activity=ROI),
                "fsamp": FSAMP,
                "gridname": [GRID_NAME],
                "muscle": ["ta"],
                "auxiliary": np.zeros((0, N_SAMPLES)),
                "auxiliaryname": [],
            }
        },
        do_compression=False,
    )
    return path


@pytest.fixture(scope="session")
def mat73_path(mat_path: Path) -> Path:
    """The same recording as a v7.3 (HDF5) ``.mat``, which loaders read by slices."""
    data = scipy.io.loadmat(str(mat_path))["signal"]["data"][0, 0]
    path = mat_path.with_name("mid_size_v73.mat")
    with h5py.File(path, "w") as f:
        group = f.create_group("signal")
        group.create_dataset("data", data=data.T)
        group.create_dataset("fsamp", data=np.array([[FSAMP]]))
        name = np.frombuffer(GRID_NAME.encode(), dtype=np.uint8).astype(np.uint16)
        group.create_dataset("gridname", data=name.reshape(-1, 1))
    return path


@pytest.fixture(scope="session")
def run_store() -> SessionStore:
    """The T1 folder a decompose run writes its filtered EMG and pulse trains into."""
    return SessionStore.create("memory-regression")


@pytest.fixture(scope="session")
def upload_token(mat_path: Path) -> str:
    """Preview the recording once; keeps the upload/QC caches warm."""
    payload = preview_service._build_preview_core(str(mat_path))
    return str(payload["upload_token"])


@pytest.fixture(scope="session")
def prepared(mat_path: Path, upload_token: str, run_store: SessionStore) -> Any:
    """``(loaded, prep)`` from the upload cache into the run's store, as the decompose run does."""
    from muedit.api.cache import _get_upload_signal

    loaded = load_step(str(mat_path), None, _get_upload_signal(upload_token), None)
    prep = preprocess_step(
        loaded=loaded,
        duration=None,
        manual_roi=False,
        roi=ROI,
        rois=None,
        params=DecompositionParameters(niter=NITER),
        discard_overrides=None,
        bids_root=None,
        bids_entities=None,
        bids_metadata=None,
        store=run_store,
    )
    return loaded, prep


@pytest.fixture(scope="session")
def decomposed(prepared: Any) -> Any:
    """Decomposition filters for the postprocess stages."""
    _, prep = prepared
    return decompose_step(
        prep=prep,
        params=DecompositionParameters(niter=NITER),
        rng=np.random.default_rng(0),
        progress_cb=None,
    )


@pytest.fixture(scope="session")
def post_windowed(prepared: Any, decomposed: Any, run_store: SessionStore) -> Any:
    """Windowed postprocess output for the save stage."""
    _, prep = prepared
    return postprocess_step(
        prep=prep,
        decomposed=decomposed,
        params=DecompositionParameters(niter=NITER),
        progress_cb=None,
        store=run_store,
    )


@pytest.fixture(scope="session")
def saved_decomposition(mat_path: Path, tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """A saved decomposition (NPZ with embedded EMG) for the edit stages."""
    result, save_path = run_decomposition(
        str(mat_path),
        roi=ROI,
        params=DecompositionParameters(niter=NITER),
        save_npz=True,
    )
    distimes = result["signal"]["Dischargetimes"]
    return {
        "npz": Path(save_path),
        "distimes": distimes,
        "mu_grid_index": result["mu_grid_index"],
        "fsamp": float(result["signal"]["fsamp"]),
    }


# ── Stages ────────────────────────────────────────────────────────────────────


def test_load_stage(mat_path: Path) -> None:
    signal, peak = _peak_mb(lambda: load_signal(str(mat_path)))
    assert signal.data.shape == (N_CHANNELS, N_SAMPLES)
    _check("load", peak)


def test_load_store_stage(mat73_path: Path) -> None:
    """A v7.3 file goes into the session store by slices: no full-size heap copy."""
    store = SessionStore.create("memory-regression-load")
    signal, peak = _peak_mb(lambda: load_signal(str(mat73_path), store=store))
    assert signal.data.shape == (N_CHANNELS, N_SAMPLES)
    assert signal.nbytes == 0
    store.close()
    _check("load_store", peak)


def test_preview_stage(mat_path: Path) -> None:
    _, peak = _peak_mb(lambda: preview_service._build_preview_core(str(mat_path)))
    _check("preview", peak)


def test_series_stage(upload_token: str) -> None:
    """What the QC stage draws for the whole recording: served from the pyramids."""

    def _fetch() -> Any:
        return [
            series_frame("emg", upload_token, 0, 0, 1024),
            series_frame("overview", upload_token, 0, 0, 1024),
            series_frame("aux", upload_token, 0, 0, 1024),
        ]

    _, peak = _peak_mb(_fetch)
    _check("series", peak)


def test_qc_auto_stage(upload_token: str) -> None:
    _, peak = _peak_mb(
        lambda: preview_service.run_auto_qc_on_token(QcAutoPayload(upload_token=upload_token))
    )
    _check("qc_auto", peak)


def test_preprocess_stage(mat_path: Path, upload_token: str, run_store: SessionStore) -> None:
    from muedit.api.cache import _get_upload_signal

    def _run() -> Any:
        loaded = load_step(str(mat_path), None, _get_upload_signal(upload_token), None)
        return preprocess_step(
            loaded=loaded,
            duration=None,
            manual_roi=False,
            roi=ROI,
            rois=None,
            params=DecompositionParameters(niter=NITER),
            discard_overrides=None,
            bids_root=None,
            bids_entities=None,
            bids_metadata=None,
            store=run_store,
        )

    _, peak = _peak_mb(_run)
    _check("preprocess", peak)


def test_decompose_stage(prepared: Any) -> None:
    _, prep = prepared

    _, peak = _peak_mb(
        lambda: decompose_step(
            prep=prep,
            params=DecompositionParameters(niter=NITER),
            rng=np.random.default_rng(0),
            progress_cb=None,
        )
    )
    _check("decompose", peak)


def test_postprocess_windowed_stage(
    prepared: Any, decomposed: Any, run_store: SessionStore
) -> None:
    _, prep = prepared

    _, peak = _peak_mb(
        lambda: postprocess_step(
            prep=prep,
            decomposed=decomposed,
            params=DecompositionParameters(niter=NITER),
            progress_cb=None,
            store=run_store,
        )
    )
    _check("post_windowed", peak)


def test_postprocess_full_trace_stage(
    prepared: Any, decomposed: Any, run_store: SessionStore
) -> None:
    _, prep = prepared
    params = DecompositionParameters(niter=NITER, full_trace=True)

    _, peak = _peak_mb(
        lambda: postprocess_step(
            prep=prep, decomposed=decomposed, params=params, progress_cb=None, store=run_store
        )
    )
    _check("post_full", peak)


def test_postprocess_adaptive_stage(
    prepared: Any, decomposed: Any, run_store: SessionStore
) -> None:
    _, prep = prepared
    params = DecompositionParameters(niter=NITER, use_adaptive=True)

    _, peak = _peak_mb(
        lambda: postprocess_step(
            prep=prep, decomposed=decomposed, params=params, progress_cb=None, store=run_store
        )
    )
    _check("post_adaptive", peak)


def test_save_stage(prepared: Any, post_windowed: Any) -> None:
    loaded, prep = prepared

    _, peak = _peak_mb(
        lambda: export_step(
            loaded=loaded,
            prep=prep,
            post=post_windowed,
            params=DecompositionParameters(niter=NITER),
            include_full_preview=False,
            save_npz=True,
            save_emg_data=True,
            progress_cb=None,
        )
    )
    _check("save", peak)


def test_edit_load_stage(saved_decomposition: dict[str, Any]) -> None:
    def _load() -> Any:
        return editing_service.load_decomposition_binary_from_path(str(saved_decomposition["npz"]))

    _, peak = _peak_mb(_load)
    _check("edit_load", peak)


def test_update_filter_stage(saved_decomposition: dict[str, Any]) -> None:
    """One update-filter click: the heaviest per-click edit operation."""
    saved = saved_decomposition
    assert len(saved["distimes"]) > 0, "decomposition produced no motor units"
    view_span = int(10.0 * saved["fsamp"])
    view_start = (ROI[0] + ROI[1]) // 2 - view_span // 2

    # Warm the edit-signal context cache exactly as an edit session would.
    editing_service.load_decomposition_from_path(str(saved["npz"]))

    payload = EditFilterPayload(
        file_label=saved["npz"].name,
        grid_index=0,
        mu_index=0,
        distimes=[[int(x) for x in d] for d in saved["distimes"]],
        mu_grid_index=[int(g) for g in saved["mu_grid_index"]],
        view_start=view_start,
        view_end=view_start + view_span,
        use_peeloff=True,
    )
    _, peak = _peak_mb(lambda: editing_service.update_filter(payload))
    _check("update_filter", peak)
