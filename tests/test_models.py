"""The shared data models (``muedit.models``): how they keep, share and count their arrays."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from muedit.models import EditSignalContext, SignalImport
from muedit.storage import SessionStore


def test_build_keeps_float32_and_memory_maps(cache_dir: Path) -> None:
    st = SessionStore.create("build")
    mapped = st.seal(st.allocate("emg", (2, 8), np.float32, zero=True))
    sig = SignalImport.build(data=mapped, aux_data=np.ones((1, 4), dtype=np.float32))
    assert sig.data.dtype == np.float32
    assert np.shares_memory(sig.data, mapped)
    assert sig.aux_data.shape == (1, 8) and sig.aux_data.dtype == np.float32
    assert SignalImport.build(data=[[1, 2, 3]]).data.dtype == np.float64


def test_without_data_shares_nothing_writable() -> None:
    sig = SignalImport.build(
        data=np.ones((2, 8)), grid_names=["G"], aux_data=np.ones((1, 8)), metadata={"k": 1}
    )
    bare = sig.without_data()
    assert bare.data.shape == (0, 8)
    bare.grid_names.append("other")
    bare.metadata["k"] = 2
    assert (sig.grid_names, sig.metadata["k"]) == (["G"], 1)
    assert np.shares_memory(bare.aux_data, sig.aux_data)
    assert not bare.aux_data.flags.writeable


def test_edit_context_counts_every_mapped_array_as_zero(cache_dir: Path) -> None:
    st = SessionStore.create("ctx")
    sealed = [
        st.seal(st.allocate(name, (64,), np.int64, zero=True)) for name in ("mask", "emgmask")
    ]
    ctx = EditSignalContext(
        data=np.zeros((0, 64), np.float32),
        fsamp=2048.0,
        artifact_mask=sealed[0],
        discard_channels=[sealed[1]],
        coordinates=[np.zeros((64, 2))],
    )
    assert ctx.nbytes == 64 * 2 * 8  # the heap coordinates only
