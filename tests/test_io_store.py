"""T1 session store (``muedit.io.store``) and the loaders that write into it."""

from __future__ import annotations

import json
import os
import pickle
import sys
import tracemalloc
from multiprocessing.reduction import ForkingPickler
from pathlib import Path
from typing import cast

import h5py
import numpy as np
import pytest
import scipy.io

from muedit.io import factory, store
from muedit.io._otb import _fit_trace, _read_feedback_traces, _read_sip, _Trace
from muedit.io.store import RamStore, SessionStore
from muedit.models import SignalImport, resident_nbytes
from tests._platform import deleted
from tests.test_io_intan import (
    _GRID,
    _reference_signals,
    _write_header,
    _write_per_channel,
    _write_per_signal_type,
    _write_traditional,
)

N_CH = 64
N_SAMPLES = 20_011  # not a multiple of any block size


@pytest.fixture
def cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = tmp_path / "cache"
    monkeypatch.setenv(store.CACHE_DIR_ENV, str(folder))
    monkeypatch.delenv(store.DISK_RESERVE_ENV, raising=False)
    return folder


@pytest.fixture
def small_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force many blocks per load on test-sized inputs."""
    monkeypatch.setattr(store, "BLOCK_BYTES", 4096)


# ── The store ────────────────────────────────────────────────────────────────


class TestSessionStore:
    def test_arrays_are_zero_filled_memory_maps(self, cache_dir: Path) -> None:
        st = SessionStore.create("t")
        arr = st.allocate("emg", (3, 5), np.float32, zero=True)
        assert isinstance(arr, np.memmap)
        assert arr.dtype == np.float32
        np.testing.assert_array_equal(arr, 0)
        assert st.path.parent == cache_dir / "sessions"

    def test_seal_reopens_read_only_off_the_heap(self, cache_dir: Path) -> None:
        st = SessionStore.create("t")
        arr = st.allocate("emg", (3, 5), np.float32)
        arr[:] = np.arange(15).reshape(3, 5)
        sealed = st.seal(arr)
        assert resident_nbytes(sealed) == 0
        assert resident_nbytes(sealed[1:, 2:]) == 0
        assert not sealed.flags.writeable
        np.testing.assert_array_equal(sealed, np.arange(15).reshape(3, 5))
        assert st.disk_bytes >= 15 * 4

    def test_a_reused_name_never_truncates_a_live_map(self, cache_dir: Path) -> None:
        st = SessionStore.create("t")
        first = st.allocate("pulse", (2, 4), np.float32)
        first[:] = 7
        sealed = st.seal(first)
        second = st.allocate("pulse", (2, 4), np.float32, zero=True)
        assert cast(np.memmap, second).filename != cast(np.memmap, sealed).filename
        np.testing.assert_array_equal(sealed, 7)

    def test_discard_deletes_only_its_own_files(self, cache_dir: Path, tmp_path: Path) -> None:
        st = SessionStore.create("t")
        arr = st.seal(st.allocate("x", (2, 2), np.float64, zero=True))
        path = Path(str(cast(np.memmap, arr).filename))
        st.discard(arr)
        assert deleted(path)
        np.testing.assert_array_equal(arr, 0)  # the live map keeps its pages
        outside = np.lib.format.open_memmap(
            tmp_path / "y.npy", mode="w+", dtype=np.float32, shape=(2,)
        )
        st.discard(outside)
        assert (tmp_path / "y.npy").exists()
        st.discard(np.zeros(3))

    def test_close_removes_the_folder(self, cache_dir: Path) -> None:
        st = SessionStore.create("t")
        st.seal(st.allocate("x", (4, 4), np.float32, zero=True))
        st.close()
        assert not st.path.exists()

    def test_falls_back_to_ram_when_the_disk_reserve_would_be_cut(
        self, cache_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(store.DISK_RESERVE_ENV, str(10**12))
        st = SessionStore.create("t")
        arr = st.allocate("x", (4, 4), np.float32, zero=True)
        assert not isinstance(arr, np.memmap)
        np.testing.assert_array_equal(arr, 0)
        sealed = st.seal(arr)
        assert not sealed.flags.writeable
        assert resident_nbytes(sealed) == arr.nbytes

    def test_empty_arrays_stay_on_the_heap(self, cache_dir: Path) -> None:
        arr = SessionStore.create("t").allocate("aux", (0, 100), np.float32)
        assert arr.shape == (0, 100)
        assert not isinstance(arr, np.memmap)

    def test_ram_store_keeps_writable_heap_arrays(self) -> None:
        ram = RamStore()
        arr = ram.allocate("x", (2, 3), np.float32, zero=True)
        assert ram.seal(arr) is arr
        assert arr.flags.writeable

    def test_close_waits_for_the_last_hold(self, cache_dir: Path) -> None:
        st = SessionStore.create("t")
        st.hold()
        st.hold()
        st.close()
        st.release()
        assert st.path.exists()
        st.release()
        assert not st.path.exists()

    def test_release_without_a_pending_close_keeps_the_folder(self, cache_dir: Path) -> None:
        st = SessionStore.create("t")
        st.hold()
        st.release()
        assert st.path.exists()
        st.close()
        assert not st.path.exists()


class TestMemmapPickling:
    """Stored arrays cross to the worker process as file locations."""

    def _stored(self) -> np.ndarray:
        st = SessionStore.create("t")
        arr = st.allocate("emg", (7, 50_001), np.float32)
        arr[:] = np.random.default_rng(0).random(arr.shape, dtype=np.float32)
        return st.seal(arr)

    @staticmethod
    def _roundtrip(arr: np.ndarray) -> tuple[np.ndarray, int]:
        data = ForkingPickler.dumps(arr)
        return pickle.loads(data), len(data)  # noqa: S301 - bytes this test just made

    def test_arrays_and_row_blocks_go_by_location(self, cache_dir: Path) -> None:
        sealed = self._stored()
        for view in (sealed, sealed.view(), sealed[2:5], sealed[6]):
            got, size = self._roundtrip(view)
            assert size < 1024
            assert isinstance(got, np.memmap)
            assert not got.flags.writeable
            np.testing.assert_array_equal(got, view)

    def test_strided_views_go_by_value(self, cache_dir: Path) -> None:
        sealed = self._stored()
        got, size = self._roundtrip(sealed[:, 10:20])
        assert size > 7 * 10 * 4
        np.testing.assert_array_equal(got, sealed[:, 10:20])

    @pytest.mark.skipif(sys.platform == "win32", reason="Windows cannot delete a mapped file")
    def test_deleted_files_go_by_value(self, cache_dir: Path) -> None:
        sealed = self._stored()
        Path(str(cast(np.memmap, sealed).filename)).unlink()
        got, size = self._roundtrip(sealed)
        assert size > sealed.nbytes
        np.testing.assert_array_equal(got, sealed)

    def test_plain_pickle_is_unchanged(self, cache_dir: Path) -> None:
        sealed = self._stored()
        assert len(pickle.dumps(sealed)) > sealed.nbytes


class TestStaleSessions:
    def _folder(self, root: Path, name: str, owner: object | None) -> Path:
        folder = root / "sessions" / name
        folder.mkdir(parents=True)
        if owner is not None:
            (folder / "owner.json").write_text(json.dumps(owner), encoding="utf-8")
        (folder / "emg.npy").write_bytes(b"\0" * 64)
        return folder

    def test_purge_removes_folders_of_exited_or_unknown_owners(self, cache_dir: Path) -> None:
        dead = self._folder(cache_dir, "dead", {"pid": 2**22 + 12345})
        orphan = self._folder(cache_dir, "orphan", None)
        mine = SessionStore.create("mine")
        store.purge_stale_sessions()
        assert not dead.exists()
        assert not orphan.exists()
        assert mine.path.exists()

    def test_usage_counts_this_process_only(self, cache_dir: Path) -> None:
        self._folder(cache_dir, "other", {"pid": 2**22 + 12345})
        mine = SessionStore.create("mine")
        mine.seal(mine.allocate("x", (16, 16), np.float32, zero=True))
        usage = store.store_usage()
        assert [s["name"] for s in usage["stores"]] == [mine.path.name]
        assert usage["bytes"] == mine.disk_bytes > 0
        assert usage["free_bytes"] > 0

    def test_own_pid_is_alive(self) -> None:
        assert store._pid_alive(os.getpid())


# ── Loaders writing into the store ───────────────────────────────────────────


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
    amp, aux_i, digital = _reference_signals()
    for name, writer in (
        ("per_channel", _write_per_channel),
        ("per_signal_type", _write_per_signal_type),
    ):
        folder = root / name
        folder.mkdir()
        writer(folder, amp, aux_i, digital)
        out[f"intan_{name}"] = folder
    (root / "traditional").mkdir()
    _write_traditional(root / "traditional" / "rec.rhd", amp, aux_i, digital)
    out["intan_traditional"] = root / "traditional" / "rec.rhd"
    for key in ("intan_per_channel", "intan_per_signal_type", "intan_traditional"):
        folder = out[key] if out[key].is_dir() else out[key].parent
        (folder / "muedit_grids.json").write_text(json.dumps({"gridname": [_GRID]}))
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
    np.testing.assert_array_equal(stored.auxiliary, heap.auxiliary)
    assert (stored.fsamp, stored.gridname, stored.auxiliaryname) == (
        heap.fsamp,
        heap.gridname,
        heap.auxiliaryname,
    )
    assert stored.nbytes == 0
    assert not stored.data.flags.writeable
    assert {p.name for p in st.path.glob("*.npy")} == {"emg.npy", "aux.npy"}


def test_heap_load_scales_in_float64_then_rounds_once(recordings: dict[str, Path]) -> None:
    """float32 output is exactly the float64 result the loaders used to return, rounded."""
    rng = np.random.default_rng(0)
    data = rng.normal(size=(N_CH, N_SAMPLES)) * 50.0
    for key in ("mat_v5", "mat_v73"):
        sig = factory.load_signal(str(recordings[key]))
        np.testing.assert_array_equal(sig.data, data.astype(np.float32), err_msg=key)


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
    _write_header(per_type / "info.rhd")
    np.arange(n, dtype=np.int32).tofile(per_type / "time.dat")
    amp.T.tofile(per_type / "amplifier.dat")
    aux[:, ::4].T.tofile(per_type / "auxiliary.dat")
    digital.tofile(per_type / "digitalin.dat")

    traditional = root / "traditional" / "rec.rhd"
    traditional.parent.mkdir()
    _write_header(traditional)
    with open(traditional, "ab") as handle:
        for b in range(n_blocks):
            lo, hi = b * 128, (b + 1) * 128
            handle.write(np.arange(lo, hi, dtype=np.int32).tobytes())
            handle.write((amp[:, lo:hi].astype(np.int32) + 32768).astype(np.uint16).tobytes())
            handle.write(aux[:, lo:hi:4].tobytes())
            handle.write(digital[lo:hi].tobytes())
    for folder in (per_type, traditional.parent):
        (folder / "muedit_grids.json").write_text(json.dumps({"gridname": [_GRID]}))
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


def test_a_loader_without_a_store_parameter_is_copied_in(
    cache_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def plain_loader(path: str) -> SignalImport:
        return SignalImport.build(data=np.ones((2, 10)), fsamp=100.0, gridname=["G"])

    monkeypatch.setitem(factory._LOADERS, ".fake", plain_loader)
    st = SessionStore.create("copy")
    sig = factory.load_signal("rec.fake", store=st)
    assert sig.data.dtype == np.float32
    assert resident_nbytes(sig.data) == 0
    np.testing.assert_array_equal(sig.data, 1)


def test_build_keeps_float32_and_memory_maps(cache_dir: Path) -> None:
    st = SessionStore.create("build")
    mapped = st.seal(st.allocate("emg", (2, 8), np.float32, zero=True))
    sig = SignalImport.build(data=mapped, auxiliary=np.ones((1, 4), dtype=np.float32))
    assert sig.data.dtype == np.float32
    assert np.shares_memory(sig.data, mapped)
    assert sig.auxiliary.shape == (1, 8) and sig.auxiliary.dtype == np.float32
    assert SignalImport.build(data=[[1, 2, 3]]).data.dtype == np.float64


class TestSipTraces:
    """OTB+ ``.sip`` target traces are fitted to the EMG length, never dropped for it."""

    def _sip(self, tmp_path: Path, values: list[float]) -> str:
        path = tmp_path / "trapezoidalpath_1.sip"
        np.asarray(values, dtype=np.float64).tofile(path)
        return str(path)

    def test_longer_trace_is_cut(self, tmp_path: Path) -> None:
        fitted = _read_sip(self._sip(tmp_path, [1.0, 2.0, 3.0, 4.0]), 3)
        np.testing.assert_array_equal(fitted, [1.0, 2.0, 3.0])

    def test_shorter_trace_holds_its_last_value(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        fitted = _read_sip(self._sip(tmp_path, [0.0, 5.0, 10.0]), 6)
        np.testing.assert_array_equal(fitted, [0.0, 5.0, 10.0, 10.0, 10.0, 10.0])
        assert "holds 3 samples, the EMG 6" in caplog.text

    def test_empty_trace_is_skipped_with_a_warning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        assert _read_sip(self._sip(tmp_path, []), 6) is None
        assert "holds no samples" in caplog.text


class TestOtb4FeedbackTraces:
    """OTB4 feedback tracks (target and performed path) are read from their own track file."""

    def _archive(self, tmp_path: Path, sample_size: int = 8) -> Path:
        tracks = "".join(
            f"<TrackInfo><Title>Trapezoidal track</Title><SubTitle>{sub}</SubTitle>"
            f"<SignalStreamPath>FB.sig</SignalStreamPath><TotalChannelsInFile>2"
            f"</TotalChannelsInFile><AcquisitionChannel>{col}</AcquisitionChannel>"
            f"<NumberOfChannels>1</NumberOfChannels><SampleSize>{sample_size}</SampleSize>"
            f"<SamplingFrequency>10</SamplingFrequency></TrackInfo>"
            for col, sub in enumerate(["Performed Path", "Original Path"])
        )
        (tmp_path / "TrapezoidalTracks_010.xml").write_text(
            f"<ArrayOfTrackInfo>{tracks}</ArrayOfTrackInfo>"
        )
        (tmp_path / "Tracks_000.xml").write_text("<ArrayOfTrackInfo/>")
        np.array([[1.0, 0.0], [2.0, 5.0], [3.0, 10.0]]).tofile(tmp_path / "FB.sig")
        return tmp_path

    def test_reads_each_channel_by_its_column(self, tmp_path: Path) -> None:
        traces = _read_feedback_traces(str(self._archive(tmp_path)))
        assert [t.name for t in traces] == [
            "Trapezoidal track - Performed Path",
            "Trapezoidal track - Original Path",
        ]
        np.testing.assert_array_equal(traces[0].values, [1.0, 2.0, 3.0])
        np.testing.assert_array_equal(traces[1].values, [0.0, 5.0, 10.0])
        assert traces[0].fs == 10.0

    def test_unknown_sample_size_is_skipped(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        assert _read_feedback_traces(str(self._archive(tmp_path, sample_size=4))) == []
        assert "not float64" in caplog.text

    def test_fit_interpolates_and_holds_the_last_value(self) -> None:
        trace = _Trace("t", np.array([0.0, 10.0]), fs=1.0)
        fitted = _fit_trace(trace, fs_out=4.0, n_samples=8)
        np.testing.assert_allclose(fitted, [0.0, 2.5, 5.0, 7.5, 10.0, 10.0, 10.0, 10.0])
