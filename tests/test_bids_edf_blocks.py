"""The BIDS EMG file is written in data-record blocks, identical to pyedflib's ``writeSamples``."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import numpy as np
import pyedflib
import pytest

import muedit.io.bids as bids
import muedit.io.store as store
from muedit.io.bids import _physical_range, export_bids_emg

FSAMP = 2048  # one data record per second, 2048 samples each
N_EMG = 5
START = datetime(2020, 1, 2, 3, 4, 5)
AUX_NAMES = ["Force", "Trig"]


def _export(tmp_path: Path, data: np.ndarray, aux: np.ndarray | None, fmt: str | None) -> Path:
    kwargs = {} if fmt is None else {"file_format": fmt}
    out = export_bids_emg(
        data,
        FSAMP,
        ["GR08MM1305"],
        [np.zeros((N_EMG, 2))],
        [np.zeros(N_EMG, dtype=int)],
        tmp_path,
        start_time=START,
        aux_data=aux,
        aux_names=AUX_NAMES if aux is not None else None,
        **kwargs,
    )
    return out["edf"]


def _reference(path: Path, data: np.ndarray, aux: np.ndarray | None, fmt: str) -> None:
    """What the export wrote before it read blocks: every channel stacked, then ``writeSamples``."""
    rows = np.asarray(data, dtype=np.float64)
    labels = [f"Ch{i + 1:02d}" for i in range(N_EMG)]
    if aux is not None:
        aux = aux[:, : rows.shape[1]]
        pad = np.zeros((aux.shape[0], rows.shape[1] - aux.shape[1]))
        rows = np.vstack([rows, np.hstack([aux, pad])])
        labels += AUX_NAMES
    use_bdf = fmt == "bdf"
    writer = pyedflib.EdfWriter(
        str(path),
        n_channels=rows.shape[0],
        file_type=pyedflib.FILETYPE_BDFPLUS if use_bdf else pyedflib.FILETYPE_EDFPLUS,
    )
    writer.setStartdatetime(START)
    headers = []
    for i, row in enumerate(rows):
        lo, hi = _physical_range(float(row.min()), float(row.max()))
        headers.append(
            {
                "label": labels[i],
                "dimension": "uV" if i < N_EMG else "a.u.",
                "sample_frequency": FSAMP,
                "physical_min": lo,
                "physical_max": hi,
                "digital_min": -8388608 if use_bdf else -32768,
                "digital_max": 8388607 if use_bdf else 32767,
                "transducer": "",
                "prefilter": "n/a",
            }
        )
    writer.setSignalHeaders(headers)
    writer.writeSamples(list(rows))
    writer.close()


@pytest.fixture(autouse=True)
def _small_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Blocks of two records, so every export crosses several blocks."""
    size = 2 * (N_EMG + len(AUX_NAMES)) * FSAMP * 8
    monkeypatch.setattr(store, "BLOCK_BYTES", size)
    monkeypatch.setattr(bids, "BLOCK_BYTES", size)


@pytest.mark.parametrize("fmt", ["edf", "bdf"])
@pytest.mark.parametrize(
    "n_samples", [5 * FSAMP, 5 * FSAMP + 700, 700], ids=["whole", "tail", "short"]
)
@pytest.mark.parametrize("aux_len", [None, 1.5, 0.5], ids=["no-aux", "aux-longer", "aux-shorter"])
def test_matches_write_samples(
    tmp_path: Path, fmt: str, n_samples: int, aux_len: float | None
) -> None:
    rng = np.random.default_rng(3)
    data = (rng.standard_normal((N_EMG, n_samples)) * 50).astype(np.float32)
    data[2] = 7.0  # a flat channel gets a widened physical range
    aux = None
    if aux_len is not None:
        aux = rng.standard_normal((len(AUX_NAMES), int(n_samples * aux_len))) + 3.0
    written = _export(tmp_path / "new", data, aux, fmt)
    expected = tmp_path / f"expected.{fmt}"
    _reference(expected, data, aux, fmt)
    assert written.suffix == f".{fmt}"
    assert written.read_bytes() == expected.read_bytes()


def test_reads_a_memmap(tmp_path: Path) -> None:
    """A memory-mapped float32 source is read in place, block by block."""
    data = np.lib.format.open_memmap(
        tmp_path / "emg.npy", mode="w+", dtype=np.float32, shape=(N_EMG, 3 * FSAMP + 11)
    )
    data[:] = np.random.default_rng(0).standard_normal(data.shape)
    data.flush()
    source = np.load(tmp_path / "emg.npy", mmap_mode="r")
    written = _export(tmp_path / "new", source, None, None)
    expected = tmp_path / "expected.edf"
    _reference(expected, np.asarray(source), None, "edf")
    assert written.read_bytes() == expected.read_bytes()


def test_edf_is_the_default(tmp_path: Path) -> None:
    data = np.ones((N_EMG, FSAMP), dtype=np.float32)
    assert _export(tmp_path, data, None, None).suffix == ".edf"


def test_existing_recording_keeps_its_format(tmp_path: Path) -> None:
    """Exporting again over a BDF recording rewrites the BDF, never adds an EDF beside it."""
    data = np.random.default_rng(1).standard_normal((N_EMG, 2 * FSAMP)).astype(np.float32)
    first = _export(tmp_path, data, None, "bdf")
    again = _export(tmp_path, data, None, None)
    assert again == first
    assert sorted(p.name for p in first.parent.glob("*_emg.*df")) == [first.name]
