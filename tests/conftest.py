"""Shared fixtures for the test suite."""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import scipy.io
from starlette.testclient import TestClient

from muedit import storage
from muedit.decomp.types import DecomposeStepOutput, PreprocessStepOutput
from muedit.models import SignalImport
from muedit.paths import CACHE_DIR_ENV

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data"


#: Fixtures that resolve git-ignored sample files.  A test whose fixture
#: closure reaches any of these is given the ``data`` marker.
DATA_FIXTURES = frozenset(
    {
        "bdf_file",
        "otb_plus_file",
        "otb4_file",
        "intan_dir",
        "openephys_dir",
        "decomp_mat_file",
        "novecento_otb4_file",
        "passiflore_otb4_file",
        "simulation_mat_files",
        "real_reference",
    }
)


def require_sample(path: Path) -> Path:
    if not path.exists():
        message = f"sample data file not present: {path}"
        if os.environ.get("MUEDIT_REQUIRE_DATA"):
            pytest.fail(message)
        pytest.skip(message)
    return path


def pytest_configure(config: pytest.Config) -> None:
    """Keep session stores in a throwaway folder, not the user's cache."""
    if not os.environ.get("MUEDIT_CACHE_DIR"):
        folder = tempfile.mkdtemp(prefix="muedit-test-cache-")
        os.environ["MUEDIT_CACHE_DIR"] = folder
        config.add_cleanup(lambda: shutil.rmtree(folder, ignore_errors=True))


@pytest.fixture
def cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A per-test cache folder for session stores, with the default disk reserve."""
    folder = tmp_path / "cache"
    monkeypatch.setenv(CACHE_DIR_ENV, str(folder))
    monkeypatch.delenv(storage.DISK_RESERVE_ENV, raising=False)
    return folder


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if DATA_FIXTURES.intersection(getattr(item, "fixturenames", ())):
            item.add_marker(pytest.mark.data)


@pytest.fixture(scope="session")
def bdf_file() -> Path:
    """Small BIDS-EMG BDF recording (64 MiB, 6x HD08MM1305 grids)."""
    return require_sample(
        DATA_DIR / "Test" / "sub-1" / "ses-1" / "emg" / "sub-1_ses-1_task-triangle_run-1_emg.bdf"
    )


@pytest.fixture(scope="session")
def otb_plus_file() -> Path:
    """OT Bioelettronica ``.otb+`` archive (Quattrocento, 6x GR08MM1305)."""
    return require_sample(DATA_DIR / "Quattrocento.otb+")


@pytest.fixture(scope="session")
def otb4_file() -> Path:
    """OT Bioelettronica ``.otb4`` archive (Quattrocento, 2x GR04MM1305)."""
    return require_sample(DATA_DIR / "Quattrocento.otb4")


@pytest.fixture(scope="session")
def intan_dir() -> Path:
    """Intan RHD recording directory ("one file per channel", 2x64 ch @ 10 kHz)."""
    return require_sample(DATA_DIR / "Intan")


@pytest.fixture(scope="session")
def openephys_dir() -> Path:
    """Open Ephys binary recording folder (2x MYOMNP-1x32 + 8 ADC @ 20 kHz, grid sidecar)."""
    return require_sample(DATA_DIR / "Record Node 101" / "experiment1" / "21_con_fs")


@pytest.fixture(scope="session")
def decomp_mat_file() -> Path:
    """Real MATLAB v7.3 decomposition artifact (Novecento, 54 MUs)."""
    return require_sample(DATA_DIR / "Novecento.otb4_decomp.mat")


@pytest.fixture(scope="session")
def novecento_otb4_file() -> Path:
    """OT Bioelettronica ``.otb4`` archive (Novecento, 6x HD08MM1305, 2000 Hz)."""
    return require_sample(DATA_DIR / "Novecento.otb4")


@pytest.fixture(scope="session")
def passiflore_otb4_file() -> Path:
    """OT Bioelettronica ``.otb4`` archive (Quattrocento, 6x GR08MM1305, a trapezoid track)."""
    return require_sample(DATA_DIR / "Passiflore_03_6_trap20.otb4")


@pytest.fixture(scope="session")
def novecento_emg(novecento_otb4_file: Path) -> SignalImport:
    """Loaded Novecento signal — 384 channels (6x HD08MM1305), 2000 Hz."""
    from muedit.io import load_signal

    return load_signal(str(novecento_otb4_file))


# Seconds of real EMG to keep when deriving the signal fixture from the decomp
# mat.  5 s @ 2000 Hz is enough for a load -> BIDS export -> decomposition run
# while staying light (64 x 20000 float64 ~ 10 MiB).
_REAL_EMG_SECONDS = 5.0


@pytest.fixture(scope="session")
def real_emg_mat_file(decomp_mat_file: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A pure-signal ``.mat`` carrying genuine EMG from the Novecento recording."""
    from muedit.decomp.decomposition_file import load_decomposition

    _, ctx = load_decomposition(str(decomp_mat_file))
    assert ctx is not None, "decomp mat did not yield an embedded signal context"

    fsamp = ctx.fsamp
    n_samples = int(round(_REAL_EMG_SECONDS * fsamp))
    data = ctx.data[:64, :n_samples]

    signal = {
        "data": data,
        "fsamp": fsamp,
        "gridname": "HD08MM1305",
        "muscle": "ta",
        "device_name": "Novecento",
        "auxiliary": np.zeros((0, n_samples)),
        "auxiliaryname": [],
    }
    out = tmp_path_factory.mktemp("real_emg_mat") / "real_emg_signal.mat"
    scipy.io.savemat(out, {"signal": signal})
    return out


# ── Simulated HD-EMG with ground-truth spike trains ──────────────────────────

#: Excitation levels (% max) for the simulated surface EMG datasets.  Each
#: file is a MATLAB v7.3 (HDF5) struct with ``signal.data`` (monopolar EMG,
#: 65 channels × 122 880 samples @ 10 240 Hz = 12 s) and ``signal.spikes``
#: (122 880 × 150 binary spike train of 150 simulated motor units).  The
#: contraction occupies the central 10 s (samples 10 240–112 639).
SIMULATION_EXCITATION_LEVELS: list[int] = [20, 40, 60]


@pytest.fixture(scope="session")
def simulation_mat_files() -> dict[int, Path]:
    """Resolve the three simulated-EMG .mat files keyed by excitation level (%)."""
    files: dict[int, Path] = {}
    for pct in SIMULATION_EXCITATION_LEVELS:
        path = DATA_DIR / f"simulation_{pct}_surface.mat_decomp.mat_edited.mat"
        files[pct] = require_sample(path)
    return files


def _load_simulation_mat(path: Path) -> dict[str, Any]:
    """Load a simulated-EMG HDF5 .mat and extract EMG data + ground-truth spikes."""
    import h5py

    with h5py.File(str(path), "r") as f:
        sig = f["signal"]
        fsamp = float(sig["fsamp"][0, 0])
        data = sig["data"][:].T[:64, :]
        spikes = sig["spikes"][:]

    gt_spike_times = [np.where(spikes[:, mu] > 0)[0] for mu in range(spikes.shape[1])]

    return {
        "data": data,
        "fsamp": fsamp,
        "spikes": spikes,
        "gt_spike_times": gt_spike_times,
        "n_total_mus": spikes.shape[1],
    }


@pytest.fixture(scope="session")
def simulation_loaded(simulation_mat_files: dict[int, Path]) -> dict[int, dict[str, Any]]:
    """Session-scoped parsed simulation data for all three excitation levels."""
    return {pct: _load_simulation_mat(path) for pct, path in simulation_mat_files.items()}


# ── Measurement reporting ────────────────────────────────────────────────────


def pytest_terminal_summary(terminalreporter: Any) -> None:
    """Print the quantitative measurement tables and write them as CSVs."""
    from tests import _report

    if not any(_report.tables().values()):
        return

    terminalreporter.write_sep("=", "measurements")
    terminalreporter.write_line(_report.format_all())

    paths = _report.write_csvs()
    if paths:
        terminalreporter.write_line("")
        terminalreporter.write_line(f"CSVs written to {_report.report_dir()}:")
        for path in paths:
            terminalreporter.write_line(f"  {path.name}")


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory) -> Iterator[TestClient]:
    """One app client for all of a module's tests, which share its uploads (``api``: one per test)."""
    from tests._api import api_client

    with api_client(tmp_path_factory.mktemp("data")) as client:
        yield client


@pytest.fixture(scope="module")
def signal_mat(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A recording of noise on one 64-channel grid, without auxiliary channels."""
    from tests._api import N_CHANNELS, N_SAMPLES, write_recording

    emg = np.random.default_rng(0).normal(0, 1, (N_CHANNELS, N_SAMPLES))
    return write_recording(tmp_path_factory.mktemp("recording") / "synthetic_signal.mat", emg)


@pytest.fixture(scope="module")
def upload_token(client: TestClient, signal_mat: Path) -> str:
    """``signal_mat`` loaded in its own tab: a tab keeps one upload."""
    from tests._api import upload

    return upload(client, signal_mat, "fixture-upload")


@pytest.fixture
def api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """A fresh app client: its projects under ``tmp_path / "data"``, its stores and logs in ``tmp_path / "cache"``."""
    from tests._api import api_client

    monkeypatch.setenv("MUEDIT_CACHE_DIR", str(tmp_path / "cache"))
    with api_client(tmp_path / "data") as client:
        yield client


# ── Synthetic decomposition inputs ───────────────────────────────────────────


@pytest.fixture(scope="module")
def short_emg() -> np.ndarray:
    """``short_recording``: ten channels of MU activity, for the extender and adaptive tests."""
    from tests._synthetic_emg import short_recording

    return short_recording()


@pytest.fixture(scope="module")
def synthetic_mu() -> dict[str, np.ndarray]:
    """EMG with one target MU, one interfering MU and noise, for the edit operations."""
    from tests import _editing_data

    return _editing_data.synthetic_mu()


@pytest.fixture(scope="module")
def store_prep() -> dict[str, Any]:
    """``synthetic_recording`` preprocessed on the heap and into a session store."""
    from muedit.decomp.preprocess import load_step, preprocess_step
    from tests._synthetic_emg import ACTIVE_ROI, STORE_PARAMS, synthetic_recording

    loaded = load_step("synthetic.mat", None, synthetic_recording(), None)

    def prep(store: storage.SessionStore | None) -> PreprocessStepOutput:
        return preprocess_step(
            loaded=loaded,
            duration=None,
            manual_roi=False,
            rois=[ACTIVE_ROI],
            params=STORE_PARAMS,
            discard_overrides=None,
            store=store,
        )

    store = storage.SessionStore.create("parity")
    return {"heap": prep(None), "stored": prep(store), "store": store}


@pytest.fixture(scope="module")
def decomposed(store_prep: dict[str, Any]) -> DecomposeStepOutput:
    """One decomposition of ``store_prep``'s heap preprocessing, with filters to apply."""
    from muedit.decomp.core import decompose_step
    from tests._synthetic_emg import STORE_PARAMS

    out = decompose_step(
        prep=store_prep["heap"], params=STORE_PARAMS, rng=np.random.default_rng(0), progress_cb=None
    )
    assert sum(f.shape[1] for f in out.mu_filters_by_window.values() if f.size), "no filters"
    return out
