"""Shared fixtures for the I/O test suite.

The EMG data files under ``data/`` are git-ignored (see ``.gitignore``:
``*.bdf``, ``*.mat``, ``*.otb+``, ``*.otb4``, ``*.npz`` and ``data/*/``), so a
fresh checkout does not contain them.  These fixtures resolve the local sample
files and skip the tests that need them when the files are absent, so the suite
runs on machines that hold the data and skips cleanly everywhere else.

Every test that depends on one of those fixtures is marked ``data``
automatically (see ``pytest_collection_modifyitems``), so CI can deselect the
whole tier with ``-m "not data"`` instead of reporting a green run full of
skips.  Set ``MUEDIT_REQUIRE_DATA=1`` to turn a missing file into a failure:
locally that proves a full run really exercised the recordings, and in CI it
catches a data-dependent test that slipped past the marker.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import scipy.io

from muedit.models import SignalImport

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
        "decomp_mat_file",
        "novecento_otb4_file",
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


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if DATA_FIXTURES.intersection(getattr(item, "fixturenames", ())):
            item.add_marker(pytest.mark.data)


@pytest.fixture(scope="session")
def bdf_file() -> Path:
    """Small BIDS-EMG BDF recording (64 MiB, 6x HD08MM1305 grids)."""
    return require_sample(
        DATA_DIR / "Test" / "sub-1" / "ses-1" / "emg" / "sub-1_ses-1_task-trapezoid_run-1_emg.bdf"
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
def decomp_mat_file() -> Path:
    """Real MATLAB v7.3 decomposition artifact (Novecento, 54 MUs)."""
    return require_sample(DATA_DIR / "Novecento.otb4_decomp.mat")


@pytest.fixture(scope="session")
def novecento_otb4_file() -> Path:
    """OT Bioelettronica ``.otb4`` archive (Novecento, 6x HD08MM1305, 2000 Hz)."""
    return require_sample(DATA_DIR / "Novecento.otb4")


@pytest.fixture(scope="session")
def novecento_emg(novecento_otb4_file: Path) -> SignalImport:
    """Loaded Novecento signal — 384 channels (6x HD08MM1305), 2000 Hz.

    Session-scoped so the 215 MB OTB4 archive is parsed only once across the
    preprocessing tests.  Returns the full ``load_signal`` result so individual
    tests can slice grids, channels, or sample windows as needed.
    """
    from muedit.io import load_signal

    return load_signal(str(novecento_otb4_file))


# Seconds of real EMG to keep when deriving the signal fixture from the decomp
# mat.  5 s @ 2000 Hz is enough for a load -> BIDS export -> decomposition run
# while staying light (64 x 20000 float64 ~ 10 MiB).
_REAL_EMG_SECONDS = 5.0


@pytest.fixture(scope="session")
def real_emg_mat_file(decomp_mat_file: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A pure-signal ``.mat`` carrying genuine EMG from the Novecento recording.

    The only ``.mat`` files in the repo are OT-Bioelettronica *decomposition*
    artifacts, and ``load_mat`` deliberately refuses files whose ``signal``
    struct also holds pulse trains + discharge times.  This fixture extracts
    the real EMG embedded in ``Novecento.otb4_decomp.mat`` (via the supported
    ``load_decomposition_signal_context`` path), drops the decomposition
    markers, and writes a plain MATLAB v5 signal struct so ``load_mat`` can
    read it.

    A single 64-channel grid (HD08MM1305) is kept so the struct round-trips
    through the v5 loader and ``SignalImport.from_mapping`` (which use
    ``or []`` coercion that is ambiguous for multi-element arrays).
    """
    from muedit.decomp.decomposition_file import load_decomposition_signal_context

    ctx = load_decomposition_signal_context(str(decomp_mat_file))
    assert ctx is not None, "decomp mat did not yield an embedded signal context"

    fsamp = ctx.fsamp
    n_samples = int(round(_REAL_EMG_SECONDS * fsamp))
    data = ctx.data[:64, :n_samples]  # grid 0 (HD08MM1305)

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


# ---------------------------------------------------------------------------
# Simulated HD-EMG with ground-truth spike trains
# ---------------------------------------------------------------------------

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
    """Load a simulated-EMG HDF5 .mat and extract EMG data + ground-truth spikes.

    The .mat stores ``signal.data`` as (samples, channels) = (122 880, 65):
    64 monopolar electrodes of a GR08MM1305 grid plus one extra channel that
    the pipeline ignores (the grid catalogue declares 64 channels).  We
    transpose to the (channels, samples) layout the pipeline expects and keep
    the first 64 channels.

    ``signal.spikes`` is a (122 880, 150) binary matrix — one column per
    simulated motor unit.  We convert it to a list of spike sample indices
    per MU so the tests can match decomposition output against ground truth.
    """
    import h5py

    with h5py.File(str(path), "r") as f:
        sig = f["signal"]
        fsamp = float(sig["fsamp"][0, 0])
        data = sig["data"][:].T[:64, :]  # (64, 122880)
        spikes = sig["spikes"][:]  # (122880, 150)

    # Binary spike train -> list of sample indices per MU
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


# ---------------------------------------------------------------------------
# Measurement reporting
# ---------------------------------------------------------------------------


def pytest_terminal_summary(terminalreporter: Any) -> None:
    """Print the quantitative measurement tables and write them as CSVs.

    Several decomposition properties (peel-off yield, dedup removal, agreement
    with ground truth, benchmark accuracy) are stochastic: real in expectation,
    but not guaranteed on any single seed/ROI.  Rather than assert
    a threshold on them, the tests record them via ``tests._report`` and this
    hook surfaces the numbers.

    Written to the terminal reporter (not ``print``) so the tables appear even
    on a passing run with output capture on.
    """
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
