"""The decomposition files MUedit writes (a run's ``_decomp.npz``, an edit's ``_edited.npz``
with its ``.json`` edit log) and the older files it still opens."""

from __future__ import annotations

import json
import os
import zipfile
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

import h5py
import numpy as np
import pytest

from muedit import storage
from muedit.api.schemas import BidsSaveFields
from muedit.api.services.editing_service import _file_extras, _save, _SaveRequest
from muedit.decomp import decomposition_file as df
from muedit.decomp.postprocess import export_step
from muedit.decomp.preprocess import preprocess_step
from muedit.decomp.types import DecompositionParameters, LoadStepOutput, PostprocessStepOutput
from muedit.io import npz
from muedit.io.npz import LegacyPickleError, NpzArchive
from muedit.models import SignalImport
from muedit.signal.grid import format_hdemg_signal
from muedit.storage import RamStore

FSAMP = 2048.0
T = 6001
GRIDS = ["GR08MM1305", "HD08MM1305"]
N_EMG = sum(len(mask) for mask in format_hdemg_signal(GRIDS)[2])
SPIKES = [[10, 20, 30], [], [5999]]
LOADER_META = {
    "manufacturer": "OT Bioelettronica",
    "units": "mV",
    "gains": [150.0] * N_EMG,
    "acquisition_date": "2026-03-26T14:31:26",
    "electrode_impedances": [52.5] + [None] * (N_EMG - 1),
    "intan_notes": "not a BIDS field",
}


@pytest.fixture(autouse=True)
def _small_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Many blocks per array, for writing and reading."""
    monkeypatch.setattr(npz, "WRITE_BLOCK_BYTES", 4096)
    monkeypatch.setattr(df, "COMPRESSED_BLOCK_BYTES", 4096)
    monkeypatch.setattr(storage, "BLOCK_BYTES", 4096)


def _emg() -> np.ndarray:
    return (np.random.default_rng(0).standard_normal((N_EMG, T)) * 0.05).astype(np.float32)


def _pulse() -> np.ndarray:
    return np.random.default_rng(1).random((3, T)).astype(np.float32)


def _run_save(tmp_path: Path) -> tuple[Path, dict[str, Any]]:
    """What a run saves: its windows, artifacts, SIL and raw EMG, beside the recording."""
    t = np.arange(T) / FSAMP
    target = 30.0 * np.clip(t - 0.5, 0, 1) * np.clip(2.5 - t, 0, 1)
    sig = SignalImport.build(
        data=_emg(),
        fsamp=FSAMP,
        grid_names=GRIDS,
        muscle_names=["soleus", "tibialis anterior"],
        aux_data=target[None, :],
        aux_names=["Force - Original Path"],
        metadata=LOADER_META,
    )
    loaded = LoadStepOutput(full_path=str(tmp_path / "rec.otb+"), signal=sig)
    params = DecompositionParameters()
    prep = preprocess_step(
        loaded=loaded,
        duration=None,
        manual_roi=False,
        roi=None,
        rois=[(0, 3000), (3500, T)],
        params=params,
        discard_overrides=[[0] * 64, [1] + [0] * 63],
        artifact_regions=[(100, 150)],
        target_index=0,
    )
    post = PostprocessStepOutput(
        pulse_trains=_pulse(),
        spike_times=[np.asarray(s, dtype=np.int32) for s in SPIKES],
        mu_grid_index=[0, 1, 1],
        sil_by_window={0: [0.9, 0.8], 1: [0.7]},
        sil=[0.9, 0.8, 0.7],
    )
    result = export_step(loaded, prep, post, params, False, True, sig.data, None)
    assert result.save_path is not None
    expected = {
        "parameters": asdict(params),
        "muscle_names": ["soleus", "tibialis anterior"],
        "sil": [0.9, 0.8, 0.7],
        "rois": [(0, 3000), (3500, T)],
        "target_segments": prep.target_segments,
        "edit_log": None,
    }
    return Path(result.save_path), expected


def _edit_save(tmp_path: Path) -> tuple[Path, dict[str, Any]]:
    """An edit of the run's file: one MU flagged and removed, one discharge added."""
    run_path, _ = _run_save(tmp_path)
    decomp, ctx = df.load_decomposition(str(run_path), RamStore(), binary_trains=False)
    pulse_trains = decomp.pulse_trains_full
    reads: list[tuple[int, int]] = []

    def rows(kept: list[int]) -> npz.RowSource:
        def read(first: int, stop: int) -> np.ndarray:
            reads.append((first, stop))
            return np.asarray(pulse_trains[kept[first:stop]])

        return npz.RowSource((len(kept), T), read)

    history = [{"type": "add_spikes", "mu_uid": "g0_mu0", "samples": [40]}]
    request = _SaveRequest(
        spike_times=[np.array([10, 20, 30, 40]), np.array([], dtype=int), np.array([5999])],
        flagged=[False, True, False],
        mu_grid_index=decomp.mu_grid_index,
        mu_uids=["g0_mu0", "g1_mu0", "g1_mu1"],
        edit_history=history,
        artifact_times=[np.array([12]), np.array([], dtype=int), np.array([], dtype=int)],
        fsamp=FSAMP,
        total_samples=T,
        grid_names=decomp.grid_names,
        parameters=decomp.parameters,
        muscle_names=decomp.muscle_names,
        sil=decomp.sil,
        rois=decomp.rois,
        artifact_mask=ctx.artifact_mask if ctx is not None else None,
        form=BidsSaveFields(entity_label="sub-01_task-ramp", remove_flagged=True),
        pulse_trains=rows,
        bids_root=tmp_path / "study",
    )
    result = _save(request)[0]
    assert len(reads) > 1  # the kept rows are written a block at a time, never stacked
    expected = {
        # A save names the muscles in the parameters too.
        "parameters": {**decomp.parameters, "target_muscle": ["soleus", "tibialis anterior"]},
        "muscle_names": ["soleus", "tibialis anterior"],
        "sil": [0.9, 0.7],
        "rois": [(0, 3000), (3500, T)],
        "target_segments": None,
        "edit_log": {
            "mu_uids": ["g0_mu0", "g1_mu1"],
            "history": [*history, {"type": "remove_flagged", "removed_mu_uids": ["g1_mu0"]}],
            "artifact_times": [[12], []],
        },
    }
    return Path(result["path"]), expected


@pytest.mark.parametrize("save", [_run_save, _edit_save], ids=["run", "edit"])
def test_saves_and_reloads(
    tmp_path: Path, save: Callable[[Path], tuple[Path, dict[str, Any]]]
) -> None:
    path, expected = save(tmp_path)
    edited = expected["edit_log"] is not None
    kept = [0, 2] if edited else [0, 1, 2]
    assert path.name == ("sub-01_task-ramp_edited.npz" if edited else "rec_decomp.npz")

    # The file: plain arrays only, uncompressed, each aligned so it can be memory-mapped.
    with np.load(path) as z:  # allow_pickle=False
        assert int(z["schema_version"]) == df.SCHEMA_VERSION
    with zipfile.ZipFile(path) as zf:
        assert {info.compress_type for info in zf.infolist()} == {zipfile.ZIP_STORED}
    with NpzArchive(path) as archive:
        for key in ("pulse_trains", "emg_data") if not edited else ("pulse_trains",):
            mapped = archive.memmap(key)
            assert isinstance(mapped, np.memmap) and mapped.offset % npz.DATA_ALIGN == 0

    decomp, ctx = df.load_decomposition(str(path), RamStore())
    spikes = [[10, 20, 30, 40], [5999]] if edited else SPIKES
    assert [s.tolist() for s in decomp.spike_times] == spikes
    np.testing.assert_array_equal(decomp.pulse_trains_full, _pulse()[kept])
    assert (decomp.fsamp, decomp.total_samples, decomp.grid_names) == (FSAMP, T, GRIDS)
    assert decomp.mu_grid_index == [[0, 1, 1][i] for i in kept]
    assert decomp.muscle_names == expected["muscle_names"]
    assert decomp.parameters == expected["parameters"]
    assert decomp.sil == expected["sil"]
    assert decomp.rois == expected["rois"]

    assert ctx is not None
    np.testing.assert_array_equal(ctx.artifact_mask, np.isin(np.arange(T), np.arange(100, 150)))
    if edited:
        # The edit log: what an edit session reopens the file with.
        log = json.loads(path.with_suffix(".json").read_text())
        want = expected["edit_log"]
        assert (log["mu_uids"], log["artifact_times"]) == (want["mu_uids"], want["artifact_times"])
        # The removal on save is logged with its time and count.
        assert len(log["history"]) == len(want["history"])
        for got, entry in zip(log["history"], want["history"], strict=True):
            assert entry.items() <= got.items()
        extras = _file_extras(str(path), path.name, decomp)
        assert (extras.mu_uids, extras.edit_history) == (log["mu_uids"], log["history"])
    else:
        # The raw EMG, with the loader's BIDS fields, so an edit save can export it later.
        assert not ctx.prefiltered
        np.testing.assert_array_equal(ctx.data, _emg())
        assert [m.tolist() for m in ctx.discard_channels] == [[0] * 64, [1] + [0] * 63]
        assert ctx.loader_meta == {k: v for k, v in LOADER_META.items() if k != "intan_notes"}
        assert ctx.target_segments == expected["target_segments"]


def _v1(path: Path, emg: np.ndarray | None = None, **extras: Any) -> Path:
    """The layout MUedit wrote before schema v2: object arrays, compressed."""
    spike_times = np.empty(3, dtype=object)
    for i, times in enumerate(SPIKES):
        spike_times[i] = np.asarray(times, dtype=int)
    fields: dict[str, Any] = {
        "pulse_trains": _pulse().astype(np.float64),
        "discharge_times": spike_times,
        "fsamp": FSAMP,
        "grid_names": np.array(GRIDS, dtype=object),
        "mu_grid_index": np.array([0, 1, 1]),
        "muscle": np.array(["TA", "GM"], dtype=object),
        "parameters": np.array([{"nbextchan": 1000, "duplicatesthresh": 0.3}], dtype=object),
        "total_samples": T,
        "sil": np.array([0.9, 0.8, 0.95]),
        **extras,
    }
    if emg is not None:
        fields["emg_data"] = emg
    np.savez_compressed(path, **fields)
    return path


_V1_LAYOUTS: dict[str, Callable[[np.ndarray], np.ndarray]] = {
    "channels-rows": lambda emg: emg,
    "samples-rows": lambda emg: np.ascontiguousarray(emg.T),
    "fortran-samples-rows": lambda emg: emg.T,
    "fortran-channels-rows": np.asfortranarray,
}


@pytest.mark.parametrize("case", [*_V1_LAYOUTS, pytest.param("matlab", marks=pytest.mark.data)])
def test_loads_older_decompositions(
    request: pytest.FixtureRequest, tmp_path: Path, case: str
) -> None:
    if case == "matlab":
        # A MATLAB MUedit decomposition (v7.3): its EMG is read by slices.
        path = request.getfixturevalue("decomp_mat_file")
        decomp, ctx = df.load_decomposition(str(path), RamStore())
        assert len(decomp.spike_times) == len(decomp.mu_grid_index) == 54
        assert decomp.fsamp == 2000.0
        assert all(s.size == 0 or s.min() >= 0 for s in decomp.spike_times)
        with h5py.File(path, "r") as h5f:
            expected = np.asarray(h5f["signal"]["data"][()]).T.astype(np.float32)
        assert ctx is not None and not ctx.prefiltered
        np.testing.assert_array_equal(ctx.data, expected)
        return
    path = _v1(tmp_path / "old.npz", _V1_LAYOUTS[case](_emg()))
    decomp, ctx = df.load_decomposition(str(path), RamStore())
    assert [s.tolist() for s in decomp.spike_times] == SPIKES
    np.testing.assert_array_equal(decomp.pulse_trains_full, _pulse())
    assert (decomp.grid_names, decomp.muscle_names) == (GRIDS, ["TA", "GM"])
    assert decomp.parameters == {"nbextchan": 1000, "duplicatesthresh": 0.3}
    assert decomp.sil == [0.9, 0.8, 0.95]
    assert ctx is not None and ctx.prefiltered  # v1 files embed the filtered EMG
    np.testing.assert_array_equal(ctx.data, _emg())


class _Payload:
    """A pickle that would create a folder when loaded."""

    def __init__(self, marker: Path) -> None:
        self.marker = marker

    def __reduce__(self) -> tuple[Any, ...]:
        return (os.mkdir, (str(self.marker),))


def _crafted_pickle(tmp_path: Path) -> Path:
    evil = np.empty(1, dtype=object)
    evil[0] = _Payload(tmp_path / "ran")
    np.savez(tmp_path / "evil.npz", discharge_times=evil, pulse_trains=np.zeros((1, 4)))
    return tmp_path / "evil.npz"


def _newer_schema(tmp_path: Path) -> Path:
    with npz.NpzWriter(tmp_path / "new.npz") as writer:
        writer.add(df.SCHEMA_KEY, np.int64(df.SCHEMA_VERSION + 1))
    return tmp_path / "new.npz"


def _not_npz(tmp_path: Path) -> Path:
    (tmp_path / "x.npz").write_bytes(b"junk" * 100)
    return tmp_path / "x.npz"


_BAD_FILES: dict[str, tuple[Callable[[Path], Path], str]] = {
    "a-crafted-pickle": (_crafted_pickle, r"posix\.mkdir|nt\.mkdir"),
    "a-newer-schema": (_newer_schema, "newer than this MUedit"),
    "not-an-npz": (_not_npz, "File is not a zip file"),
}


@pytest.mark.parametrize("case", [*_BAD_FILES, "a-failed-save"])
def test_refuses_bad_decomposition(tmp_path: Path, case: str) -> None:
    if case == "a-failed-save":
        # A save that fails mid-write leaves the previous file whole, and no partial one.
        path, _ = _run_save(tmp_path)
        before = path.read_bytes()
        with pytest.raises(ValueError, match=r"rows 0:\d+ read as"):
            df.save_decomposition_npz(
                path,
                pulse_trains=npz.RowSource((3, T), lambda a, b: np.zeros((b - a, 5))),
                spike_times=SPIKES,
                fsamp=FSAMP,
                grid_names=GRIDS,
                mu_grid_index=[0, 1, 1],
                muscle_names=[],
                parameters={},
                total_samples=T,
            )
        assert path.read_bytes() == before
        assert not [p for p in path.parent.iterdir() if p.name.startswith(".")]
        return
    write, message = _BAD_FILES[case]
    with pytest.raises(df.DecompositionFileError, match=message) as raised:
        df.load_decomposition(str(write(tmp_path)), with_signal=False)
    if case == "a-crafted-pickle":
        assert isinstance(raised.value.__cause__, LegacyPickleError)
        assert not (tmp_path / "ran").exists()


GRID = "GR08MM1305"


def _save_npz(path: Path, **extras: Any) -> None:
    spike_times = [[100, 300], [200]]
    pulse_trains = np.zeros((2, 1000))
    df.save_decomposition_npz(
        path,
        pulse_trains=pulse_trains,
        spike_times=spike_times,
        fsamp=FSAMP,
        grid_names=[GRID],
        mu_grid_index=[0, 0],
        muscle_names=["ta"],
        parameters={},
        total_samples=1000,
        **extras,
    )


class TestNpzArtifactMask:
    def test_mask_only_file_returns_the_mask(self, tmp_path: Path) -> None:
        mask = np.zeros(1000, dtype=bool)
        mask[400:450] = True
        path = tmp_path / "d.npz"
        _save_npz(path, artifact_mask=mask)
        ctx = df.load_decomposition(str(path))[1]
        assert ctx is not None
        assert ctx.data.size == 0
        assert ctx.artifact_mask is not None
        np.testing.assert_array_equal(ctx.artifact_mask, mask)
        assert ctx.artifact_mask.dtype == bool

    def test_signal_context_with_emg_and_mask(self, tmp_path: Path) -> None:
        emg = np.arange(3 * 1000, dtype=float).reshape(3, 1000)
        mask = np.zeros(1000, dtype=bool)
        mask[5:7] = True
        path = tmp_path / "d.npz"
        _save_npz(
            path,
            emg_data=emg.T,
            discard_channels=[np.array([0, 1, 0])],
            artifact_mask=mask,
        )
        ctx = df.load_decomposition(str(path))[1]
        assert ctx is not None
        np.testing.assert_array_equal(ctx.data, emg)
        assert not ctx.prefiltered
        assert ctx.fsamp == FSAMP
        assert ctx.grid_names == [GRID]
        assert ctx.artifact_mask is not None
        np.testing.assert_array_equal(ctx.artifact_mask, mask)
        assert len(ctx.discard_channels) == 1
        np.testing.assert_array_equal(ctx.discard_channels[0], [0, 1, 0])
