"""Decomposition .npz schema v2, the legacy v1 reader and one-pass loading (plan stage 13)."""

from __future__ import annotations

import os
import zipfile
from pathlib import Path
from typing import Any
from unittest import mock

import h5py
import numpy as np
import pytest

from muedit.decomp import decomposition_file as df
from muedit.editing import operations
from muedit.io import npz, store
from muedit.io.npz import LegacyPickleError, NpzArchive
from muedit.io.store import SessionStore

FSAMP = 2048.0
N_CH = 5
T = 3001
GRIDS = ["GR08MM1305", "HD04MM1305"]


@pytest.fixture
def cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = tmp_path / "cache"
    monkeypatch.setenv(store.CACHE_DIR_ENV, str(folder))
    monkeypatch.delenv(store.DISK_RESERVE_ENV, raising=False)
    return folder


@pytest.fixture
def small_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Many blocks per array on test-sized inputs, for both writing and reading."""
    monkeypatch.setattr(npz, "WRITE_BLOCK_BYTES", 1000)
    monkeypatch.setattr(df, "COMPRESSED_BLOCK_BYTES", 1000)
    monkeypatch.setattr(store, "BLOCK_BYTES", 1000)


def _emg() -> np.ndarray:
    return np.random.default_rng(0).standard_normal((N_CH, T)) * 100


def _pulse() -> np.ndarray:
    return np.random.default_rng(1).random((3, T))


def _save_v2(path: Path, **kwargs: Any) -> None:
    fields: dict[str, Any] = {
        "pulse_trains": _pulse(),
        "distimes": [[30, 10, 20, 20, -4], [], [2999]],
        "fsamp": FSAMP,
        "grid_names": GRIDS,
        "mu_grid_index": [0, 1, 1],
        "muscles": ["TA", "GM"],
        "parameters": {"nbextchan": 1000, "duplicatesthresh": 0.3, "tag": np.float64(2.5)},
        "total_samples": T,
    }
    fields.update(kwargs)
    df.save_decomposition_npz(path, **fields)


def _save_v1(path: Path, **extras: Any) -> None:
    """The layout ``save_decomposition_npz`` wrote before schema v2: object arrays, compressed."""
    distimes = np.empty(3, dtype=object)
    for i, times in enumerate([[10, 20, 30], [], [2999]]):
        distimes[i] = np.asarray(times, dtype=int)
    np.savez_compressed(
        path,
        pulse_trains=_pulse(),
        discharge_times=distimes,
        fsamp=FSAMP,
        grid_names=np.array(GRIDS, dtype=object),
        mu_grid_index=np.array([0, 1, 1]),
        muscle=np.array(["TA", "GM"], dtype=object),
        parameters=np.array([{"nbextchan": 1000, "duplicatesthresh": 0.3}], dtype=object),
        total_samples=T,
        **extras,
    )


class TestSchemaV2:
    def test_round_trip(self, tmp_path: Path) -> None:
        path = tmp_path / "d.npz"
        _save_v2(path, sil=[0.91, 0.8, 0.95], rois=[(0, 1000), (1500, 3000)])
        loaded = df.load_decomposition_file(str(path))
        assert loaded.distime_all == [[10, 20, 30], [], [2999]]
        assert loaded.pulse_trains_full.dtype == np.float32
        np.testing.assert_array_equal(loaded.pulse_trains_full, _pulse().astype(np.float32))
        assert loaded.fsamp == FSAMP
        assert loaded.total_samples == T
        assert loaded.grid_names == GRIDS
        assert loaded.mu_grid_index == [0, 1, 1]
        assert loaded.muscle == ["TA", "GM"]
        assert loaded.parameters == {"nbextchan": 1000, "duplicatesthresh": 0.3, "tag": 2.5}
        assert loaded.rois == [(0, 1000), (1500, 3000)]
        assert loaded.sil == [0.91, 0.8, 0.95]

    def test_file_is_pickle_free_uncompressed_and_aligned(
        self, tmp_path: Path, small_blocks: None
    ) -> None:
        path = tmp_path / "d.npz"
        _save_v2(
            path,
            sil_by_window={1: [0.9], 0: [0.8, 0.7]},
            adaptive_losses={"grid0": [1.0, 0.5]},
            emg_data=_emg(),
            discard_channels=[np.zeros(3, int), np.ones(2, int)],
            coordinates=[np.zeros((3, 2)), np.ones((2, 2))],
        )
        with np.load(path) as z:  # allow_pickle=False
            arrays = {key: z[key] for key in z.files}
        assert int(arrays["schema_version"]) == df.SCHEMA_VERSION
        assert arrays["sil_keys"].tolist() == [0, 1]
        assert arrays["sil_by_window"].tolist() == [0.8, 0.7, 0.9]
        assert arrays["sil_by_window_offsets"].tolist() == [0, 2, 3]
        assert arrays["discard_channel_offsets"].tolist() == [0, 3, 5]
        with zipfile.ZipFile(path) as zf:
            assert {info.compress_type for info in zf.infolist()} == {zipfile.ZIP_STORED}
        with NpzArchive(path) as archive:
            for key in ("pulse_trains", "emg_data", "spike_times"):
                mapped = archive.memmap(key)
                assert isinstance(mapped, np.memmap)
                assert mapped.offset % npz.DATA_ALIGN == 0
            np.testing.assert_array_equal(archive.memmap("emg_data"), _emg().astype(np.float32))

    def test_spike_times_are_csr(self, tmp_path: Path) -> None:
        path = tmp_path / "d.npz"
        _save_v2(path)
        with np.load(path) as z:
            assert z["spike_times"].dtype == np.int32
            assert z["spike_times"].tolist() == [10, 20, 30, 2999]
            assert z["spike_offsets"].tolist() == [0, 3, 3, 4]

    def test_spikes_only_file_loads_binary_trains(self, tmp_path: Path) -> None:
        path = tmp_path / "d.npz"
        _save_v2(path, pulse_trains=None)
        with np.load(path) as z:
            assert "pulse_trains" not in z.files
        loaded = df.load_decomposition_file(str(path))
        assert loaded.pulse_trains_full.shape == (3, T)
        assert loaded.pulse_trains_full.dtype == np.float32
        assert [np.flatnonzero(row).tolist() for row in loaded.pulse_trains_full] == [
            [10, 20, 30],
            [],
            [2999],
        ]

    def test_artifact_mask_is_stored_as_intervals(self, tmp_path: Path) -> None:
        mask = np.zeros(T, dtype=bool)
        mask[100:150] = mask[2990:] = True
        path = tmp_path / "d.npz"
        _save_v2(path, artifact_mask=mask)
        with np.load(path) as z:
            assert z["artifact_intervals"].tolist() == [[100, 150], [2990, T]]
        ctx = df.load_decomposition_signal_context(str(path))
        assert ctx is not None and ctx.artifact_mask is not None
        np.testing.assert_array_equal(ctx.artifact_mask, mask)

    def test_embedded_emg_is_raw_and_goes_into_the_store(
        self, tmp_path: Path, cache_dir: Path, small_blocks: None
    ) -> None:
        path = tmp_path / "d.npz"
        _save_v2(path, emg_data=_emg(), discard_channels=[np.array([0, 1, 0]), np.array([1, 0])])
        st = SessionStore.create("edit")
        _, ctx = df.load_decomposition(str(path), st)
        assert ctx is not None
        assert not ctx.prefiltered
        assert isinstance(ctx.data, np.memmap)
        assert ctx.data.filename is not None
        assert Path(ctx.data.filename).parent == st.path
        np.testing.assert_array_equal(ctx.data, _emg().astype(np.float32))
        assert [m.tolist() for m in ctx.emgmask] == [[0, 1, 0], [1, 0]]
        assert ctx.nbytes < ctx.data.nbytes  # memory-mapped EMG is not heap

    def test_save_replaces_the_file_whole(self, tmp_path: Path) -> None:
        path = tmp_path / "d.npz"
        _save_v2(path)
        with pytest.raises(ValueError, match="could not convert"):
            _save_v2(path, emg_data=np.array([["x"]], dtype=object))  # fails mid-write
        assert df.load_decomposition_file(str(path)).distime_all == [[10, 20, 30], [], [2999]]
        assert [p.name for p in tmp_path.iterdir()] == ["d.npz"]

    def test_newer_schema_is_refused(self, tmp_path: Path) -> None:
        path = tmp_path / "d.npz"
        with npz.NpzWriter(path) as writer:
            writer.add(df.SCHEMA_KEY, np.int64(df.SCHEMA_VERSION + 1))
        with pytest.raises(ValueError, match="newer than this MUedit"):
            df.load_decomposition_file(str(path))


class TestLegacyV1:
    def test_fields_load_as_before(self, tmp_path: Path) -> None:
        path = tmp_path / "d.npz"
        _save_v1(path, sil=np.array([0.9, 0.8, 0.95]))
        loaded = df.load_decomposition_file(str(path))
        assert loaded.distime_all == [[10, 20, 30], [], [2999]]
        np.testing.assert_array_equal(loaded.pulse_trains_full, _pulse())
        assert loaded.grid_names == GRIDS
        assert loaded.muscle == ["TA", "GM"]
        assert loaded.parameters == {"nbextchan": 1000, "duplicatesthresh": 0.3}
        assert loaded.sil == [0.9, 0.8, 0.95]

    @pytest.mark.parametrize(
        "layout",
        [
            lambda emg: emg,
            lambda emg: np.ascontiguousarray(emg.T),
            lambda emg: emg.T,  # saved Fortran-ordered
            np.asfortranarray,
        ],
        ids=["channels-rows", "samples-rows", "fortran-samples-rows", "fortran-channels-rows"],
    )
    def test_embedded_emg_streams_into_the_store(
        self, tmp_path: Path, cache_dir: Path, small_blocks: None, layout: Any
    ) -> None:
        path = tmp_path / "d.npz"
        _save_v1(path, emg_data=layout(_emg()))
        ctx = df.load_decomposition_signal_context(str(path), SessionStore.create("edit"))
        assert ctx is not None
        assert ctx.prefiltered  # v1 files embed the notch- and bandpass-filtered EMG
        assert isinstance(ctx.data, np.memmap)
        np.testing.assert_array_equal(ctx.data, _emg().astype(np.float32))

    def test_crafted_pickle_is_refused(self, tmp_path: Path) -> None:
        marker = tmp_path / "ran"

        class Payload:
            def __reduce__(self) -> tuple[Any, ...]:
                return (os.mkdir, (str(marker),))

        evil = np.empty(1, dtype=object)
        evil[0] = Payload()
        path = tmp_path / "d.npz"
        _save_v1(path, parameters_extra=evil)
        with (
            NpzArchive(path) as archive,
            pytest.raises(LegacyPickleError, match=r"posix\.mkdir|nt\.mkdir"),
        ):
            archive.get("parameters_extra")
        path2 = tmp_path / "e.npz"
        np.savez(path2, discharge_times=evil, pulse_trains=np.zeros((1, 4)))
        with pytest.raises(LegacyPickleError):
            df.load_decomposition_file(str(path2))
        assert not marker.exists()


def test_mat73_emg_is_read_by_slice(decomp_mat_file: Path, small_blocks: None) -> None:
    """The v7.3 EMG read block by block equals a whole read of the dataset."""
    with h5py.File(decomp_mat_file, "r") as h5f:
        expected = np.asarray(h5f["signal"]["data"][()]).T.astype(np.float32)
    ctx = df.load_decomposition_signal_context(str(decomp_mat_file))
    assert ctx is not None
    assert not ctx.prefiltered
    np.testing.assert_array_equal(ctx.data, expected)


@pytest.mark.parametrize("bandpass", [True, False])
def test_update_filter_bandpasses_only_raw_emg(bandpass: bool) -> None:
    emg = _emg()
    with mock.patch.object(
        operations, "bandpass_signals", side_effect=lambda x, _fs: x
    ) as bandpass_mock:
        operations.update_motor_unit_filter_window(
            emg,
            np.zeros(N_CH, dtype=int),
            [500, 900, 1300, 1700, 2100],
            FSAMP,
            0,
            T,
            nbextchan=20,
            bandpass=bandpass,
        )
    assert bandpass_mock.called == bandpass
