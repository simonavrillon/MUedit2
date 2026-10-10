"""Open Ephys binary-format loading on a synthetic Record Node folder."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from muedit.io import get_loader, load_signal
from muedit.io._openephys import load_openephys
from muedit.io.store import SessionStore
from muedit.models import resident_nbytes

_N_EMG = 64
_N_ADC = 2
_N_SAMPLES = 5000
_FSAMP = 20000.0
_FIRST_SAMPLE = 1_000_000
_EMG_STEP = 0.195  # uV per bit
_ADC_STEP = 0.0001525879  # V per bit
_STREAM = "Acquisition_Board-100.acquisition_board"

_SETTINGS = """<?xml version="1.0" encoding="UTF-8"?>
<SETTINGS>
  <INFO><VERSION>1.0.2</VERSION></INFO>
  <SIGNALCHAIN>
    <PROCESSOR name="Acquisition Board" pluginName="Acquisition Board" nodeId="100">
      <EDITOR LowCut="2.5" HighCut="7600.0" DSPOffset="1" DSPCutoffFreq="0.15"/>
    </PROCESSOR>
    <PROCESSOR name="Record Node" pluginName="Record Node" nodeId="101"/>
  </SIGNALCHAIN>
</SETTINGS>
"""


def _channel(name: str, step: float, units: str, kind: int) -> dict[str, object]:
    return {
        "channel_name": name,
        "bit_volts": step,
        "units": units,
        "type": kind,
        "history": "Acquisition Board -> Record Node",
    }


def _raw() -> np.ndarray:
    """Samples-by-channels int16 frames with a per-channel signature."""
    rng = np.random.default_rng(7)
    return rng.integers(-3000, 3000, size=(_N_SAMPLES, _N_EMG + _N_ADC), dtype=np.int16)


def _write_recording(directory: Path, raw: np.ndarray, ttl: list[tuple[int, int]]) -> None:
    """Write one recording folder: structure.oebin, continuous data, TTL edges, sync messages."""
    channels = [_channel(f"CH{i + 1}", _EMG_STEP, "uV", 0) for i in range(_N_EMG)]
    channels += [_channel(f"ADC{i + 1}", _ADC_STEP, "V", 2) for i in range(_N_ADC)]
    structure = {
        "GUI version": "1.0.2",
        "continuous": [
            {
                "folder_name": f"{_STREAM}/",
                "sample_rate": _FSAMP,
                "source_processor_name": "Acquisition Board",
                "source_processor_id": 100,
                "recorded_processor_id": 101,
                "num_channels": len(channels),
                "channels": channels,
            },
            {
                "folder_name": "Acquisition_Board-100.memory_usage/",
                "sample_rate": 100.0,
                "source_processor_id": 100,
                "num_channels": 1,
                "channels": [_channel("MEM", 1.0, "%", 1)],
            },
        ],
        "events": [
            {"folder_name": f"{_STREAM}/TTL/", "initial_state": 0},
            {"folder_name": "MessageCenter/"},
        ],
        "spikes": [],
    }
    directory.mkdir(parents=True)
    (directory / "structure.oebin").write_text(json.dumps(structure), encoding="utf-8")
    (directory / "sync_messages.txt").write_text(
        "Software Time (milliseconds since midnight Jan 1st 1970 UTC): 1786984281641\n"
    )

    stream = directory / "continuous" / _STREAM
    stream.mkdir(parents=True)
    raw.tofile(stream / "continuous.dat")
    np.save(stream / "sample_numbers.npy", _FIRST_SAMPLE + np.arange(len(raw), dtype=np.int64))

    events = directory / "events" / _STREAM / "TTL"
    events.mkdir(parents=True)
    np.save(events / "sample_numbers.npy", np.array([_FIRST_SAMPLE + s for s, _ in ttl]))
    np.save(events / "states.npy", np.array([state for _, state in ttl], dtype=np.int16))


@pytest.fixture
def record_node(tmp_path: Path) -> Path:
    """A Record Node folder holding one recording, with the settings.xml the GUI writes."""
    node = tmp_path / "Record Node 101"
    _write_recording(
        node / "experiment1" / "1_trial",
        _raw(),
        ttl=[(1000, 1), (2500, -1), (4000, 3)],
    )
    (node / "settings.xml").write_text(_SETTINGS, encoding="utf-8")
    return node


def _recording(node: Path) -> Path:
    return node / "experiment1" / "1_trial"


def test_scales_emg_to_mv_and_adc_to_volts(record_node: Path) -> None:
    raw = _raw().astype(np.float64)
    sig = load_openephys(str(_recording(record_node) / "structure.oebin"))

    assert sig.fsamp == _FSAMP
    assert sig.data.shape == (_N_EMG, _N_SAMPLES) and sig.data.dtype == np.float32
    np.testing.assert_array_equal(
        sig.data, (raw[:, :_N_EMG].T * (_EMG_STEP * 1e-3)).astype(np.float32)
    )
    np.testing.assert_array_equal(
        sig.auxiliary[:_N_ADC], (raw[:, _N_EMG:].T * _ADC_STEP).astype(np.float32)
    )
    assert sig.metadata["units"] == "mV"
    assert sig.metadata["openephys_first_sample"] == _FIRST_SAMPLE


def test_ttl_edges_become_step_lines(record_node: Path) -> None:
    sig = load_openephys(str(_recording(record_node)))

    assert sig.auxiliaryname == ["ADC1", "ADC2", "TTL1", "TTL3"]
    line1 = np.zeros(_N_SAMPLES, np.float32)
    line1[1000:2500] = 1
    line3 = np.zeros(_N_SAMPLES, np.float32)
    line3[4000:] = 1
    np.testing.assert_array_equal(sig.auxiliary[2], line1)
    np.testing.assert_array_equal(sig.auxiliary[3], line3)


def test_folder_and_structure_file_load_alike(record_node: Path) -> None:
    folder = _recording(record_node)
    assert get_loader(folder) is load_openephys
    assert get_loader(folder / "structure.oebin") is load_openephys
    np.testing.assert_array_equal(
        load_signal(str(folder)).data, load_signal(str(folder / "structure.oebin")).data
    )


def test_session_store_load_matches_the_heap_load(record_node: Path) -> None:
    heap = load_openephys(str(_recording(record_node)))
    store = SessionStore.create("openephys")
    try:
        stored = load_signal(str(_recording(record_node)), store=store)
        np.testing.assert_array_equal(stored.data, heap.data)
        np.testing.assert_array_equal(stored.auxiliary, heap.auxiliary)
        assert resident_nbytes(stored.data) == 0
    finally:
        store.close()


def test_metadata_reads_settings_and_sync_messages(record_node: Path) -> None:
    md = load_openephys(str(_recording(record_node))).metadata

    assert md["manufacturer"] == "Open Ephys"
    assert md["device_name"] == "Acquisition Board"
    assert md["software_versions"] == "Open Ephys GUI 1.0.2"
    assert md["hardware_filters"] == ["HP: 2.50 Hz, LP: 7600.00 Hz, Notch: none"]
    assert md["emg_hpf"] == [2.5] * _N_EMG
    assert md["emg_lpf"] == [7600.0] * _N_EMG
    assert md["acquisition_date"] == "2026-08-17T16:31:21.641+00:00"


def test_64_channels_default_to_the_intan_adapter(record_node: Path) -> None:
    assert load_openephys(str(_recording(record_node))).gridname == ["INTAN64-1305"]


def test_32_channel_headstages_default_to_one_myomatrix_thread_each(record_node: Path) -> None:
    """Two 32-channel headstages in settings.xml (ports A and B) make two grids."""
    headstages = """
        <HSOPTIONS index="0" hs1_full_channels="1" hs2_full_channels="0"/>
        <HSOPTIONS index="1" hs1_full_channels="1" hs2_full_channels="0"/>
        <HSOPTIONS index="2" hs1_full_channels="0" hs2_full_channels="0"/>
      </EDITOR>"""
    settings = _SETTINGS.replace('DSPCutoffFreq="0.15"/>', f'DSPCutoffFreq="0.15">{headstages}')
    (record_node / "settings.xml").write_text(settings, encoding="utf-8")

    sig = load_openephys(str(_recording(record_node)))
    assert sig.gridname == ["MYOMNP-1x32", "MYOMNP-1x32"]
    assert sig.metadata["openephys_headstages"] == [32, 32]


def test_record_node_sidecar_names_grids_for_every_recording(record_node: Path) -> None:
    """``load_signal`` cannot pass arguments, so one sidecar beside settings.xml covers them all."""
    (record_node / "muedit_grids.json").write_text(
        json.dumps({"gridname": ["GR10MM0804"], "muscle": ["ta"]})
    )
    sig = load_signal(str(_recording(record_node)))
    assert sig.gridname == ["GR10MM0804", "GR10MM0804"]
    assert sig.muscle == ["ta", "ta"]


def test_grids_that_miss_channels_raise(record_node: Path) -> None:
    with pytest.raises(ValueError, match="cover 128 electrodes"):
        load_openephys(str(_recording(record_node)), grid_names=["GR10MM0804", "GR10MM0804"] * 2)


def test_unknown_channel_count_raises_actionable_error(tmp_path: Path) -> None:
    directory = tmp_path / "experiment1" / "rec"
    raw = _raw()
    _write_recording(directory, raw, ttl=[])
    structure = json.loads((directory / "structure.oebin").read_text())
    stream = structure["continuous"][0]
    for channel in stream["channels"][40:_N_EMG]:
        channel["type"] = 1
    (directory / "structure.oebin").write_text(json.dumps(structure))
    with pytest.raises(ValueError, match="records one"):
        load_openephys(str(directory))


def test_truncated_data_file_raises(record_node: Path) -> None:
    data = _recording(record_node) / "continuous" / _STREAM / "continuous.dat"
    with open(data, "ab") as handle:
        handle.write(b"\x00\x01")
    with pytest.raises(OSError, match="whole number"):
        load_openephys(str(_recording(record_node)))


def test_rejects_folder_without_structure_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match=r"structure\.oebin"):
        load_openephys(str(tmp_path))
