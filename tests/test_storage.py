"""T1 session store (``muedit.storage``): its memory-mapped arrays, holds, pickling and purge."""

from __future__ import annotations

import json
import os
import pickle
import sys
from multiprocessing.reduction import ForkingPickler
from pathlib import Path
from typing import cast

import numpy as np
import pytest

from muedit import storage as store
from muedit.models import resident_nbytes
from muedit.storage import RamStore, SessionStore, StoreClosedError
from tests._platform import deleted

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

    def test_an_unbalanced_release_does_not_block_the_close(self, cache_dir: Path) -> None:
        st = SessionStore.create("t")
        st.release()
        st.close()
        assert not st.path.exists()

    def test_a_closing_store_takes_no_new_hold(self, cache_dir: Path) -> None:
        st = SessionStore.create("t")
        st.hold()
        st.close()
        with pytest.raises(StoreClosedError):
            st.hold()
        st.release()
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

    def test_own_pid_is_alive(self) -> None:
        assert store._pid_alive(os.getpid())
