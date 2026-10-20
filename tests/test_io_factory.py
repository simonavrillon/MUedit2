"""Loaders writing into a session store (``factory.load_signal(store=...)``), block by block."""

from __future__ import annotations

import json
import tracemalloc
from pathlib import Path

import h5py
import numpy as np
import pytest
import scipy.io

from muedit import storage as store
from muedit.io import factory
from muedit.storage import SessionStore
from tests._io_files import (
    INTAN_GRID,
    intan_signals,
    write_intan_header,
    write_intan_per_channel,
    write_intan_per_signal_type,
    write_intan_traditional,
)

N_CH = 64
N_SAMPLES = 20_011  # not a multiple of any block size


@pytest.fixture
def small_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force many blocks per load on test-sized inputs."""
    monkeypatch.setattr(store, "BLOCK_BYTES", 4096)


def _mat_v5(path: Path, data: np.ndarray, aux: np.ndarray) -> Path:
    scipy.io.savemat(
        path,
        {
            "signal": {
                "data": data,
                "fsamp": 2048.0,
                "gridname": ["GR08MM1305"],
                "muscle": ["ta"],
                "auxiliary": aux,
                "auxiliaryname": ["a", "b"],
            }
        },
    )
    return path


def _mat_v73(path: Path, data: np.ndarray, aux: np.ndarray) -> Path:
    with h5py.File(path, "w") as f:
        group = f.create_group("signal")
        group.create_dataset("data", data=data.T)
        group.create_dataset("fsamp", data=np.array([[2048.0]]))
        group.create_dataset("auxiliary", data=aux.T)
        name = np.frombuffer(b"GR08MM1305", dtype=np.uint8).astype(np.uint16)
        group.create_dataset("gridname", data=name.reshape(-1, 1))
    return path


@pytest.fixture(scope="module")
def recordings(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """One small recording per synthetic format, including all three Intan layouts."""
    root = tmp_path_factory.mktemp("store_formats")
    rng = np.random.default_rng(0)
    data = rng.normal(size=(N_CH, N_SAMPLES)) * 50.0
    aux = rng.normal(size=(2, N_SAMPLES))
    out = {
        "mat_v5": _mat_v5(root / "v5.mat", data, aux),
        "mat_v73": _mat_v73(root / "v73.mat", data, aux),
    }
    amp, aux_i, digital = intan_signals()
    for name, writer in (
        ("per_channel", write_intan_per_channel),
        ("per_signal_type", write_intan_per_signal_type),
    ):
        folder = root / name
        folder.mkdir()
        writer(folder, amp, aux_i, digital)
        out[f"intan_{name}"] = folder
    (root / "traditional").mkdir()
    write_intan_traditional(root / "traditional" / "rec.rhd", amp, aux_i, digital)
    out["intan_traditional"] = root / "traditional" / "rec.rhd"
    for key in ("intan_per_channel", "intan_per_signal_type", "intan_traditional"):
        folder = out[key] if out[key].is_dir() else out[key].parent
        (folder / "muedit_grids.json").write_text(json.dumps({"gridname": [INTAN_GRID]}))
    return out


@pytest.fixture(
    params=["mat_v5", "mat_v73", "intan_per_channel", "intan_per_signal_type", "intan_traditional"]
)
def recording(request: pytest.FixtureRequest, recordings: dict[str, Path]) -> Path:
    return recordings[request.param]


@pytest.mark.usefixtures("small_blocks")
def test_store_load_matches_the_heap_load(cache_dir: Path, recording: Path) -> None:
    heap = factory.load_signal(str(recording))
    st = SessionStore.create("load")
    stored = factory.load_signal(str(recording), store=st)

    assert heap.data.dtype == stored.data.dtype == np.float32
    np.testing.assert_array_equal(stored.data, heap.data)
    np.testing.assert_array_equal(stored.aux_data, heap.aux_data)
    assert (stored.fsamp, stored.grid_names, stored.aux_names) == (
        heap.fsamp,
        heap.grid_names,
        heap.aux_names,
    )
    assert stored.nbytes == 0
    assert not stored.data.flags.writeable
    assert {p.name for p in st.path.glob("*.npy")} == {"emg.npy", "aux.npy"}


@pytest.fixture(scope="module")
def long_recordings(
    tmp_path_factory: pytest.TempPathFactory, recordings: dict[str, Path]
) -> dict[str, Path]:
    """Intan recordings long enough (~33 MB as float32) for fixed overheads not to matter."""
    root = tmp_path_factory.mktemp("store_long")
    n_blocks = 1000
    n = 128 * n_blocks
    rng = np.random.default_rng(1)
    amp = rng.integers(-2000, 2000, size=(64, n)).astype(np.int16)
    aux = rng.integers(0, 3000, size=(2, n)).astype(np.uint16)
    digital = rng.integers(0, 2, size=n).astype(np.uint16)

    per_type = root / "per_signal_type"
    per_type.mkdir()
    write_intan_header(per_type / "info.rhd")
    np.arange(n, dtype=np.int32).tofile(per_type / "time.dat")
    amp.T.tofile(per_type / "amplifier.dat")
    aux[:, ::4].T.tofile(per_type / "auxiliary.dat")
    digital.tofile(per_type / "digitalin.dat")

    traditional = root / "traditional" / "rec.rhd"
    traditional.parent.mkdir()
    write_intan_header(traditional)
    with open(traditional, "ab") as handle:
        for b in range(n_blocks):
            lo, hi = b * 128, (b + 1) * 128
            handle.write(np.arange(lo, hi, dtype=np.int32).tobytes())
            handle.write((amp[:, lo:hi].astype(np.int32) + 32768).astype(np.uint16).tobytes())
            handle.write(aux[:, lo:hi:4].tobytes())
            handle.write(digital[lo:hi].tobytes())
    for folder in (per_type, traditional.parent):
        (folder / "muedit_grids.json").write_text(json.dumps({"gridname": [INTAN_GRID]}))
    return {
        "mat_v73": recordings["mat_v73"],
        "intan_per_signal_type": per_type,
        "intan_traditional": traditional,
    }


def test_streamed_loaders_hold_no_full_copy(
    cache_dir: Path, long_recordings: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Blocks go straight into the memory map: the heap peak is a fraction of the recording."""
    monkeypatch.setattr(store, "BLOCK_BYTES", 1024 * 1024)
    for key, path in long_recordings.items():
        st = SessionStore.create("peak")
        tracemalloc.start()
        try:
            sig = factory.load_signal(str(path), store=st)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        assert peak < 0.25 * sig.data.size * 4, (key, peak)
