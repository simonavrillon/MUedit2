"""Small synthetic recordings in every format MUedit imports, written for the I/O tests."""

from __future__ import annotations

import json
import shutil
import struct
import tarfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import h5py
import numpy as np
import pyedflib
import pytest
import scipy.io

from muedit.models import SignalImport
from muedit.signal.grid import GridSpec, format_hdemg_signal

# ── What a loaded recording must hold ────────────────────────────────────────


@dataclass(frozen=True)
class Expected:
    """What one import test expects of a loaded recording; ``None`` leaves a field unchecked."""

    shape: tuple[int, int]
    fsamp: float
    grid_names: list[str]
    muscle_names: list[str] | None = None
    aux_names: list[str] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    data: np.ndarray | None = None  # the float32 EMG, exact unless ``data_atol``
    data_atol: float = 0.0  # EDF/BDF store 16/24-bit samples
    aux_rows: dict[int, np.ndarray] = field(default_factory=dict)  # exact float32 aux rows


def check_signal(sig: SignalImport, expected: Expected) -> None:
    """``sig`` holds float32 EMG whose grids cover every channel, and what ``expected`` lists."""
    assert sig.data.dtype == np.float32
    assert sig.data.shape == expected.shape
    assert float(np.std(sig.data)) > 0, "loaded EMG is flat"
    assert sig.fsamp == expected.fsamp
    assert sig.grid_names == expected.grid_names
    discard = format_hdemg_signal(sig.grid_names)[2]
    assert sum(len(mask) for mask in discard) == sig.data.shape[0]
    assert sig.aux_data.shape[1] == sig.data.shape[1]
    if expected.muscle_names is not None:
        assert sig.muscle_names == expected.muscle_names
    if expected.aux_names is not None:
        assert sig.aux_names == expected.aux_names
        assert sig.aux_data.shape[0] == len(expected.aux_names)
    for key, value in expected.metadata.items():
        assert sig.metadata.get(key) == value, key
    if expected.data is not None:
        np.testing.assert_allclose(sig.data, expected.data, rtol=0, atol=expected.data_atol)
    for row, values in expected.aux_rows.items():
        np.testing.assert_array_equal(sig.aux_data[row], values, err_msg=f"aux row {row}")


#: A ``_FILES`` entry's writer: the case's fixture request and temp folder in, the file to load out.
Write = Callable[[pytest.FixtureRequest, Path], Path]


# ── MATLAB .mat ──────────────────────────────────────────────────────────────


def write_mat(path: Path, version: str, struct_name: str, fields: dict[str, Any]) -> Path:
    """A ``.mat`` (v5 or v7.3) holding one struct of numeric fields."""
    if version == "v5":
        scipy.io.savemat(path, {struct_name: fields})
    else:
        with h5py.File(path, "w") as f:
            group = f.create_group(struct_name)
            for name, value in fields.items():
                group.create_dataset(name, data=value)
    return path


def _mat73_text(text: str) -> np.ndarray:
    """A MATLAB char array as v7.3 stores it: one uint16 column."""
    return np.frombuffer(text.encode("ascii"), dtype=np.uint8).astype(np.uint16).reshape(-1, 1)


def write_mat_signal(path: Path, version: str, data: np.ndarray, aux: np.ndarray) -> Path:
    """A MUedit ``signal`` struct: one GR08MM1305 grid on ``ta``, two aux rows, at 2048 Hz."""
    if version == "v5":
        scipy.io.savemat(
            path,
            {
                "signal": {
                    "data": data,
                    "fsamp": 2048.0,
                    "gridname": ["GR08MM1305"],
                    "muscle": ["ta"],
                    "auxiliary": aux,
                    "auxiliaryname": np.array(["Force", "Trigger"], dtype=object),
                    "device_name": "Quattrocento",
                }
            },
        )
        return path
    with h5py.File(path, "w") as f:
        group = f.create_group("signal")
        group.create_dataset("data", data=data.T)  # MATLAB writes column-major
        group.create_dataset("fsamp", data=np.array([[2048.0]]))
        group.create_dataset("auxiliary", data=aux.T)
        group.create_dataset("gridname", data=_mat73_text("GR08MM1305"))
        group.create_dataset("muscle", data=_mat73_text("ta"))
        group.create_dataset("device_name", data=_mat73_text("Quattrocento"))
    return path


# ── Intan RHD ────────────────────────────────────────────────────────────────

INTAN_GRID = "GR10MM0804"
INTAN_N_CH = 32
INTAN_N_PORTS = 2
INTAN_SAMPLES_PER_BLOCK = 128
INTAN_N_BLOCKS = 3
INTAN_N_SAMPLES = INTAN_SAMPLES_PER_BLOCK * INTAN_N_BLOCKS
INTAN_FSAMP = 2000.0
#: Measured impedance of channel A-000, in ohms; every other channel was never measured.
INTAN_IMPEDANCE = 52_500.0


def _qstring(text: str) -> bytes:
    """A Qt ``QString`` as the RHD header stores one."""
    raw = text.encode("utf-16-le")
    return struct.pack("<I", len(raw)) + raw


def _channel_record(native: str, order: int, signal_type: int, impedance: float = 0.0) -> bytes:
    """One channel record of the header's signal-group table."""
    return (
        _qstring(native)
        + _qstring(native)
        + struct.pack("<hhhhhh", order, order, signal_type, 1, order, 0)
        + struct.pack("<hhhh", 0, 0, 0, 0)
        + struct.pack("<ff", impedance, 0.0)
    )


def write_intan_header(path: Path, notes: tuple[str, str, str] = ("", "", "")) -> None:
    """A version 3.0 RHD header: two amplifier ports, aux, and digital in; 50 Hz notch."""
    buf = struct.pack("<Ihh", 0xC6912702, 3, 0)
    buf += struct.pack("<f", INTAN_FSAMP)
    buf += struct.pack("<h", 1)
    buf += struct.pack("<ffffff", 20.0, 10.0, 500.0, 20.0, 10.0, 500.0)
    buf += struct.pack("<h", 1)
    buf += struct.pack("<ff", 1000.0, 1000.0)
    buf += b"".join(_qstring(note) for note in notes)
    buf += struct.pack("<h", 0)
    buf += struct.pack("<h", 0)
    buf += _qstring("n/a")
    buf += struct.pack("<h", INTAN_N_PORTS + 1)
    for port in ("A", "B"):
        buf += _qstring(f"Port {port}") + _qstring(port)
        buf += struct.pack("<hhh", 1, INTAN_N_CH + 1, INTAN_N_CH)
        for i in range(INTAN_N_CH):
            impedance = INTAN_IMPEDANCE if (port, i) == ("A", 0) else 0.0
            buf += _channel_record(f"{port}-{i:03d}", i, 0, impedance)
        buf += _channel_record(f"{port}-AUX1", INTAN_N_CH, 1)
    buf += _qstring("Digital In Ports") + _qstring("DIGITAL-IN")
    buf += struct.pack("<hhh", 1, 1, 0)
    buf += _channel_record("DIGITAL-IN-01", 0, 4)
    path.write_bytes(buf)


def intan_signals() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Amplifier, aux and digital samples with a per-channel signature."""
    rng = np.random.default_rng(7)
    n = INTAN_N_PORTS * INTAN_N_CH
    amp = rng.integers(-2000, 2000, size=(n, INTAN_N_SAMPLES)).astype(np.int16)
    amp += (np.arange(n, dtype=np.int16) * 10)[:, None]
    aux = rng.integers(0, 3000, size=(INTAN_N_PORTS, INTAN_N_SAMPLES)).astype(np.uint16)
    digital = rng.integers(0, 2, size=(1, INTAN_N_SAMPLES)).astype(np.uint16)
    return amp, aux, digital


def write_intan_per_channel(
    directory: Path, amp: np.ndarray, aux: np.ndarray, digital: np.ndarray
) -> None:
    """The "one file per channel" layout: one ``.dat`` per saved channel."""
    write_intan_header(directory / "info.rhd")
    np.arange(amp.shape[1], dtype=np.int32).tofile(directory / "time.dat")
    for port_i, port in enumerate(("A", "B")):
        for i in range(INTAN_N_CH):
            amp[port_i * INTAN_N_CH + i].tofile(directory / f"amp-{port}-{i:03d}.dat")
        aux[port_i].tofile(directory / f"aux-{port}-AUX1.dat")
    digital[0].tofile(directory / "board-DIGITAL-IN-01.dat")


def write_intan_per_signal_type(
    directory: Path, amp: np.ndarray, aux: np.ndarray, digital: np.ndarray
) -> None:
    """The "one file per signal type" layout: channels interleaved per file."""
    write_intan_header(directory / "info.rhd")
    np.arange(amp.shape[1], dtype=np.int32).tofile(directory / "time.dat")
    amp.T.astype(np.int16).tofile(directory / "amplifier.dat")
    aux[:, ::4].T.astype(np.uint16).tofile(directory / "auxiliary.dat")
    digital[0].astype(np.uint16).tofile(directory / "digitalin.dat")


def write_intan_traditional(
    path: Path,
    amp: np.ndarray,
    aux: np.ndarray,
    digital: np.ndarray,
    notes: tuple[str, str, str] = ("", "", ""),
) -> None:
    """A monolithic ``.rhd``: fixed-size data blocks following the header."""
    write_intan_header(path, notes)
    with open(path, "ab") as handle:
        for lo in range(0, amp.shape[1], INTAN_SAMPLES_PER_BLOCK):
            hi = lo + INTAN_SAMPLES_PER_BLOCK
            handle.write(np.arange(lo, hi, dtype=np.int32).tobytes())
            handle.write((amp[:, lo:hi].astype(np.int32) + 32768).astype(np.uint16).tobytes())
            handle.write(aux[:, lo:hi:4].astype(np.uint16).tobytes())
            handle.write(digital[0, lo:hi].astype(np.uint16).tobytes())


def write_grid_sidecar(folder: Path, grid_names: list[str], muscle_names: list[str]) -> None:
    """The ``muedit_grids.json`` a recording folder holds to name its grids and muscles."""
    (folder / "muedit_grids.json").write_text(
        json.dumps({"grid_names": grid_names, "muscle_names": muscle_names}), encoding="utf-8"
    )


# ── Open Ephys ───────────────────────────────────────────────────────────────

OE_N_EMG = 64
OE_N_ADC = 2
OE_N_SAMPLES = 5000
OE_FSAMP = 20000.0
OE_FIRST_SAMPLE = 1_000_000
OE_EMG_STEP = 0.195  # uV per bit
OE_ADC_STEP = 0.0001525879  # V per bit
OE_STREAM = "Acquisition_Board-100.acquisition_board"
OE_SETTINGS = """<?xml version="1.0" encoding="UTF-8"?>
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
#: Two 32-channel headstages (ports A and B), as ``settings.xml`` lists them.
OE_HEADSTAGES = """
        <HSOPTIONS index="0" hs1_full_channels="1" hs2_full_channels="0"/>
        <HSOPTIONS index="1" hs1_full_channels="1" hs2_full_channels="0"/>
        <HSOPTIONS index="2" hs1_full_channels="0" hs2_full_channels="0"/>
      </EDITOR>"""


def _oe_channel(name: str, step: float, units: str, kind: int) -> dict[str, object]:
    return {"channel_name": name, "bit_volts": step, "units": units, "type": kind}


def openephys_raw() -> np.ndarray:
    """Samples-by-channels int16 frames with a per-channel signature."""
    rng = np.random.default_rng(7)
    return rng.integers(-3000, 3000, size=(OE_N_SAMPLES, OE_N_EMG + OE_N_ADC), dtype=np.int16)


def write_openephys_recording(
    directory: Path, raw: np.ndarray, ttl: Iterable[tuple[int, int]], n_ephys: int = OE_N_EMG
) -> None:
    """One recording folder: structure.oebin, continuous data, TTL edges, sync messages."""
    channels = [_oe_channel(f"CH{i + 1}", OE_EMG_STEP, "uV", 0) for i in range(n_ephys)]
    channels += [_oe_channel(f"CH{i + 1}", OE_EMG_STEP, "uV", 1) for i in range(n_ephys, OE_N_EMG)]
    channels += [_oe_channel(f"ADC{i + 1}", OE_ADC_STEP, "V", 2) for i in range(OE_N_ADC)]
    structure = {
        "GUI version": "1.0.2",
        "continuous": [
            {
                "folder_name": f"{OE_STREAM}/",
                "sample_rate": OE_FSAMP,
                "source_processor_name": "Acquisition Board",
                "source_processor_id": 100,
                "num_channels": len(channels),
                "channels": channels,
            },
            {
                "folder_name": "Acquisition_Board-100.memory_usage/",
                "sample_rate": 100.0,
                "source_processor_id": 100,
                "num_channels": 1,
                "channels": [_oe_channel("MEM", 1.0, "%", 1)],
            },
        ],
        "events": [
            {"folder_name": f"{OE_STREAM}/TTL/", "initial_state": 0},
            {"folder_name": "MessageCenter/"},
        ],
        "spikes": [],
    }
    directory.mkdir(parents=True)
    (directory / "structure.oebin").write_text(json.dumps(structure), encoding="utf-8")
    (directory / "sync_messages.txt").write_text(
        "Software Time (milliseconds since midnight Jan 1st 1970 UTC): 1786984281641\n"
    )
    stream = directory / "continuous" / OE_STREAM
    stream.mkdir(parents=True)
    raw.tofile(stream / "continuous.dat")
    np.save(stream / "sample_numbers.npy", OE_FIRST_SAMPLE + np.arange(len(raw), dtype=np.int64))
    ttl = list(ttl)
    events = directory / "events" / OE_STREAM / "TTL"
    events.mkdir(parents=True)
    np.save(events / "sample_numbers.npy", np.array([OE_FIRST_SAMPLE + s for s, _ in ttl]))
    np.save(events / "states.npy", np.array([state for _, state in ttl], dtype=np.int16))


def write_openephys_record_node(root: Path, *, headstages: bool = False) -> Path:
    """A Record Node folder with one recording and its settings.xml; returns the recording."""
    node = root / "Record Node 101"
    recording = node / "experiment1" / "1_trial"
    write_openephys_recording(recording, openephys_raw(), ttl=[(1000, 1), (2500, -1), (4000, 3)])
    settings = OE_SETTINGS
    if headstages:
        settings = settings.replace(
            'DSPCutoffFreq="0.15"/>', f'DSPCutoffFreq="0.15">{OE_HEADSTAGES}'
        )
    (node / "settings.xml").write_text(settings, encoding="utf-8")
    return recording


# ── OT Bioelettronica OTB+ and OTB4 ──────────────────────────────────────────

OTB_FSAMP = 2048.0
OTB_N_SAMPLES = 3000
#: Two 64-channel grids, one force channel, and a control adapter OTBioLab+ appends last.
OTB_PLUS_N_EMG = 128


def _tar(path: Path, files: dict[str, bytes]) -> Path:
    """A tar archive holding ``files``, as OTBioLab writes an ``.otb+`` / ``.otb4``."""
    staging = path.parent / f"{path.name}.files"
    staging.mkdir()
    with tarfile.open(path, "w") as tar:
        for name, data in files.items():
            (staging / name).write_bytes(data)
            tar.add(staging / name, arcname=name)
    shutil.rmtree(staging)
    return path


def otb_plus_frames() -> np.ndarray:
    """Samples-by-channels int16 frames: 128 grid channels, force, then the control channel."""
    rng = np.random.default_rng(3)
    return rng.integers(-3000, 3000, size=(OTB_N_SAMPLES, OTB_PLUS_N_EMG + 2), dtype=np.int16)


def _otb_plus_xml() -> str:
    def channels(prefix: str, muscle: str) -> str:
        return "".join(
            f'<Channel Index="{i}" ID="GR08MM1305" Prefix="{prefix}" Muscle="{muscle}" '
            f'Side="Right" Description="64 Adhesive Array 8 mm i.e.d." Gain="1"/>'
            for i in range(64)
        )

    # The prefixes OTBioLab+ writes: a single input (grid 1 or 2) or a multiple input (3+).
    return (
        '<Device Name="QUATTROCENTO; ID=Unknown" SampleFrequency="2048" ad_bits="16">'
        "<Channels>"
        '<Adapter ID="AdapterInSplitter64" ChannelStartIndex="0" HighPassFilter="10" '
        'LowPassFilter="500" Gain="150" AdapterIndex="0">'
        + channels("IN 1 (Channel 1-&gt;16) - ", "Tibialis Anterior")
        + "</Adapter>"
        '<Adapter ID="AdapterInSplitter64" ChannelStartIndex="64" HighPassFilter="10" '
        'LowPassFilter="500" Gain="150" AdapterIndex="1">'
        + channels("MULTIPLE IN 1 (Channel 1-&gt;64) - ", "Soleus")
        + "</Adapter>"
        '<Adapter ID="AdapterAux" ChannelStartIndex="128" HighPassFilter="0" '
        'LowPassFilter="100" Gain="0.5" AdapterIndex="2">'
        '<Channel Index="0" ID="AUX Force" Prefix="AUX 1 (Channel 1) - " '
        'Description="Force Transducer" Gain="1"/>'
        "</Adapter>"
        '<Adapter ID="AdapterControl" ChannelStartIndex="129" Gain="1" AdapterIndex="3">'
        '<Channel Index="0" ID="Buffer" Description="Control" Gain="1"/>'
        "</Adapter>"
        "</Channels></Device>"
    )


#: The ``.sip`` feedback traces of the synthetic OTB+: longer, shorter and empty.
OTB_PLUS_SIPS = {
    "trapezoidalpath_1": np.linspace(0.0, 30.0, OTB_N_SAMPLES + 10),
    "trapezoidalpath_2": np.array([0.0, 5.0, 10.0]),
    "trapezoidalpath_3": np.array([]),
}


def write_otb_plus(path: Path, *, extra_sig: bool = False, xml: bool = True) -> Path:
    """An ``.otb+`` named after its start time, with its XML, signal and feedback traces."""
    stem = "20230130115031_03"
    files = {f"{stem}.sig": otb_plus_frames().tobytes()}
    if xml:
        files[f"{stem}.xml"] = _otb_plus_xml().encode()
    if extra_sig:
        files["zz_extra.sig"] = otb_plus_frames().tobytes()
    files |= {
        f"{name}.sip": values.astype(np.float64).tobytes() for name, values in OTB_PLUS_SIPS.items()
    }
    return _tar(path, files)


def _otb4_track(**fields: Any) -> str:
    """One ``<TrackInfo>`` element; dict values become nested elements."""
    body = ""
    for key, value in fields.items():
        if isinstance(value, dict):
            inner = "".join(f"<{k}>{v}</{k}>" for k, v in value.items())
            body += f"<{key}>{inner}</{key}>"
        else:
            body += f"<{key}>{value}</{key}>"
    return f"<TrackInfo>{body}</TrackInfo>"


def otb4_frames(dtype: type[np.integer[Any]], n_channels: int) -> np.ndarray:
    rng = np.random.default_rng(5)
    return rng.integers(-3000, 3000, size=(OTB_N_SAMPLES, n_channels)).astype(dtype)


#: The feedback traces of the synthetic OTB4, sampled at 10 Hz: performed path, then target.
OTB4_FEEDBACK = np.array([[0.0, 0.0], [5.0, 10.0], [10.0, 20.0]])


def write_otb4(path: Path, device: str, *, emg: bool = True, missing_signal: bool = False) -> Path:
    """An ``.otb4``: two 64-channel grids and one aux track, plus feedback track files."""
    # Novecento+ keeps each stream's tracks in its own int32 file; every other device writes
    # one int16 file of all tracks.
    filters = {
        "HighPassFilter": "10 Hz",
        "LowPassFilter": "Fsamp/4",
        "StartDate": "2026-03-26 14:31:26",
        "OriginalSensor": "HD08MM1305" if device == "Novecento+" else "GR04MM1305",
    }
    novecento = device == "Novecento+"
    # Without an IN… title or a GR…/HD… sensor name, no track is EMG.
    grid_title, grid_model = (
        ("IN", "HD08MM1305" if novecento else "GR04MM1305") if emg else ("Track", "Track")
    )
    common = {
        "Device": f"{device};ID=1",
        "Gain": "256",
        "ADC_Nbits": "24" if novecento else "16",
        "ADC_Range": "5",
        "SamplingFrequency": "2048",
    }
    if novecento:
        tracks = [
            _otb4_track(
                Title=f"{grid_title}{g + 1}",
                NumberOfChannels=64,
                AcquisitionChannel=64 * g,
                ChannelsInBlock=128,
                SignalStreamPath="Grids.sig",
                Description={"Name": grid_model},
                StringsDescriptions=filters,
                **common,
            )
            for g in range(2)
        ] + [
            _otb4_track(
                Title="Aux",
                NumberOfChannels=1,
                AcquisitionChannel=0,
                ChannelsInBlock=1,
                SignalStreamPath="Aux.sig",
                Description={"Name": "AUX Force"},
                StringsDescriptions=filters,
                **common,
            )
        ]
        files = {"Grids.sig": otb4_frames(np.int32, 128).tobytes()}
        if not missing_signal:
            files["Aux.sig"] = otb4_frames(np.int32, 1).tobytes()
    else:
        tracks = [
            _otb4_track(
                Title=f"{grid_title}{g + 1}",
                NumberOfChannels=64,
                AcquisitionChannel=64 * g,
                SignalStreamPath="20260326143126.sig",
                Description={"Name": grid_model},
                StringsDescriptions=filters,
                **common,
            )
            for g in range(2)
        ] + [
            _otb4_track(
                Title="Force",
                NumberOfChannels=1,
                AcquisitionChannel=128,
                SignalStreamPath="20260326143126.sig",
                StringsDescriptions=filters,
                **common,
            ),
            _otb4_track(
                Title="AdapterControl",
                NumberOfChannels=1,
                AcquisitionChannel=129,
                SignalStreamPath="20260326143126.sig",
                StringsDescriptions=filters,
                **common,
            ),
        ]
        files = {"20260326143126.sig": otb4_frames(np.int16, 130).tobytes()}
    feedback = "".join(
        _otb4_track(
            Title="Trapezoidal track",
            SubTitle=sub,
            SignalStreamPath="FB.sig",
            TotalChannelsInFile=2,
            AcquisitionChannel=col,
            NumberOfChannels=1,
            SampleSize=size,
            SamplingFrequency=10,
        )
        for col, sub, size in [(0, "Performed Path", 8), (1, "Original Path", 8)]
    )
    skipped = _otb4_track(
        Title="Old track",
        SubTitle="Int32",
        SignalStreamPath="FB.sig",
        TotalChannelsInFile=2,
        AcquisitionChannel=0,
        NumberOfChannels=1,
        SampleSize=4,
        SamplingFrequency=10,
    )
    files |= {
        "Tracks_000.xml": f"<ArrayOfTrackInfo>{''.join(tracks)}</ArrayOfTrackInfo>".encode(),
        "TrapezoidalTracks_010.xml": f"<ArrayOfTrackInfo>{feedback}</ArrayOfTrackInfo>".encode(),
        "OldTracks_020.xml": f"<ArrayOfTrackInfo>{skipped}</ArrayOfTrackInfo>".encode(),
        "FB.sig": OTB4_FEEDBACK.tobytes(),
    }
    return _tar(path, files)


def write_not_an_archive(tmp_path: Path, name: str) -> Path:
    """A file that is not a tar archive, for the loaders that unpack one."""
    path = tmp_path / name
    path.write_bytes(b"not an archive" * 100)
    return path


# ── BIDS EMG ─────────────────────────────────────────────────────────────────

MUNITQUEST = Path(__file__).parent / "bids_fixtures" / "munitquest"
MUNITQUEST_SUB01 = "sub-01/emg/sub-01_task-isometric30percentMVC_run-01"
MUNITQUEST_SUB02 = "sub-02/ses-01/emg/sub-02_ses-01_task-isometric30percentMVC_run-01"
#: Catalogue entries a user adds for the tutorial's grids, channels numbered row by row.
MUNITQUEST_GRIDS = {
    "MQ8MM0304": GridSpec(
        np.arange(1, 13).reshape(3, 4), 12, 8.0, 1, "n/a", "surface array", "n/a"
    ),
    "MQ8MM0303": GridSpec(np.arange(1, 10).reshape(3, 3), 9, 8.0, 1, "n/a", "surface array", "n/a"),
    "MQWIRE": GridSpec(np.ones((1, 1), dtype=int), 1, 0.0, 2, "n/a", "intramuscular", "n/a"),
}
#: The grid names a user adds to each recording's ``channels.tsv`` for the tutorial's grids.
MUNITQUEST_GRID_NAMES = {
    MUNITQUEST_SUB01: {"grid1": "MQ8MM0304", "intramuscular": "MQWIRE"},
    MUNITQUEST_SUB02: {"grid1": "MQ8MM0303", "grid2": "MQ8MM0303"},
}


def tsv_rows(path: Path) -> list[dict[str, str]]:
    import csv

    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def munitquest_trace(row: int, n_samples: int) -> np.ndarray:
    """``channels.tsv`` row ``row`` carries ``10 * (row + 1)`` plus a small wave."""
    return 10.0 * (row + 1) + np.sin(np.arange(n_samples) / 50.0)


def _write_munitquest_edf(root: Path, stem: str) -> None:
    rows = tsv_rows(root / f"{stem}_channels.tsv")
    fsamp = int(json.loads((root / f"{stem}_emg.json").read_text())["SamplingFrequency"])
    writer = pyedflib.EdfWriter(
        str(root / f"{stem}_emg.edf"), n_channels=len(rows), file_type=pyedflib.FILETYPE_EDFPLUS
    )
    try:
        writer.setSignalHeaders(
            [
                {
                    "label": row["name"],
                    "dimension": row["units"],
                    "sample_frequency": fsamp,
                    "physical_min": -500.0,
                    "physical_max": 500.0,
                    "digital_min": -32768,
                    "digital_max": 32767,
                    "transducer": "",
                    "prefilter": "",
                }
                for row in rows
            ]
        )
        # 40 s: long enough for the task phases the tutorial's events.tsv lists.
        writer.writeSamples([munitquest_trace(i, 40 * fsamp) for i in range(len(rows))])
    finally:
        writer.close()


def write_munitquest(root: Path, *, named: bool = True) -> Path:
    """The MUnitQuest tutorial dataset with short EDFs; ``named`` adds the ``grid_name`` column."""
    shutil.copytree(MUNITQUEST, root)
    for stem in (MUNITQUEST_SUB01, MUNITQUEST_SUB02):
        _write_munitquest_edf(root, stem)
        if not named:
            continue
        path = root / f"{stem}_channels.tsv"
        header, *body = path.read_text(encoding="utf-8").splitlines()
        group = header.split("\t").index("group")
        names = MUNITQUEST_GRID_NAMES[stem]
        lines = [f"{header}\tgrid_name"]
        lines += [line + "\t" + names.get(line.split("\t")[group], "n/a") for line in body]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return root
