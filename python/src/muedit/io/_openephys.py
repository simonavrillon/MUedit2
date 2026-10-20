"""Open Ephys GUI binary-format recording loader for MUedit."""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from muedit.errors import SignalLoadError
from muedit.io._common import _AMPLIFIER_GAIN, _GRID_SIDECAR, _read_grid_sidecar, _resolve_muscles
from muedit.io.aux_channels import aux_kinds
from muedit.models import SignalImport, as_name_list
from muedit.signal.grid import grid_spec
from muedit.storage import ArrayStore, RamStore, sample_blocks

logger = logging.getLogger(__name__)

_OEBIN = "structure.oebin"

# Channel "type" in structure.oebin: 0 ephys, 1 headstage AUX, 2 board ADC.
_EPHYS = 0

_TO_MV = {"uV": 1e-3, "µV": 1e-3, "mV": 1.0, "V": 1e3}
_TO_V = {"uV": 1e-6, "µV": 1e-6, "mV": 1e-3, "V": 1.0}

# Default grid per headstage size: the Intan adapter for 64 channels, a Myomatrix thread for 32.
_DEFAULT_GRIDS = {64: "INTAN64-1305", 32: "MYOMNP-1x32"}

_HEADSTAGE_SLOTS = ("hs1_full_channels", "hs2_full_channels")

_SOFTWARE_TIME = re.compile(r"Software Time.*?:\s*(\d+)")


@dataclass
class _Channel:
    """One channel entry of a continuous stream in ``structure.oebin``."""

    name: str
    bit_volts: float
    units: str
    kind: int


@dataclass
class _TtlLine:
    """Edges of one TTL line, as sample positions in the continuous file."""

    line: int
    positions: np.ndarray
    states: np.ndarray
    initial: float


@dataclass
class _OpenEphysRecording:
    """A resolved Open Ephys recording: the ephys stream and where its samples live."""

    directory: Path
    structure: dict[str, Any]
    stream: dict[str, Any]
    channels: list[_Channel]
    data_path: Path
    n_samples: int
    ttl: list[_TtlLine]


def _read_structure(filepath: str | Path) -> tuple[Path, dict[str, Any]]:
    """Locate and parse ``structure.oebin`` from the file itself or its recording folder."""
    path = Path(filepath)
    if path.is_dir():
        path = path / _OEBIN
        if not path.exists():
            raise SignalLoadError(
                f"No {_OEBIN} found in Open Ephys recording folder: {path.parent}"
            )
    elif path.suffix.lower() != ".oebin":
        raise SignalLoadError(f"Not an Open Ephys recording: {path}")
    try:
        structure = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SignalLoadError(f"Cannot read Open Ephys structure file {path}: {exc}") from exc
    if not isinstance(structure, dict):
        raise SignalLoadError(f"{path} is not an Open Ephys structure file")
    return path.parent, structure


def _parse_channels(stream: dict[str, Any]) -> list[_Channel]:
    """Return the channel entries of one continuous stream, in file column order."""
    return [
        _Channel(
            name=str(c.get("channel_name", f"CH{i + 1}")),
            bit_volts=float(c.get("bit_volts", 1.0)),
            units=str(c.get("units", "")),
            kind=int(c.get("type", _EPHYS)),
        )
        for i, c in enumerate(stream.get("channels", []))
    ]


def _read_ttl(
    directory: Path, structure: dict[str, Any], folder: str, sample_numbers: np.ndarray
) -> list[_TtlLine]:
    """Return the TTL lines of the stream in ``folder`` that carry at least one edge."""
    lines: list[_TtlLine] = []
    for event in structure.get("events", []):
        event_folder = str(event.get("folder_name", ""))
        if not event_folder.startswith(folder) or "TTL" not in event_folder:
            continue
        path = directory / "events" / event_folder.strip("/\\")
        if not (path / "states.npy").exists() or not (path / "sample_numbers.npy").exists():
            continue
        states = np.load(path / "states.npy").astype(np.int64)
        positions = np.searchsorted(sample_numbers, np.load(path / "sample_numbers.npy"))
        initial_word = int(event.get("initial_state", 0))
        # States are signed 1-based line numbers: +n is a rising edge on line n, -n a falling one.
        for line in sorted({abs(int(s)) for s in states if s}):
            mask = np.abs(states) == line
            order = np.argsort(positions[mask], kind="stable")
            lines.append(
                _TtlLine(
                    line=line,
                    positions=positions[mask][order],
                    states=(states[mask][order] > 0).astype(np.float64),
                    initial=float((initial_word >> (line - 1)) & 1),
                )
            )
    return lines


def _resolve_openephys_recording(filepath: str | Path) -> _OpenEphysRecording:
    """Pick the continuous stream holding ephys channels and check its data file."""
    directory, structure = _read_structure(filepath)
    candidates = []
    for stream in structure.get("continuous", []):
        channels = _parse_channels(stream)
        n_ephys = sum(c.kind == _EPHYS for c in channels)
        if n_ephys:
            candidates.append((n_ephys, stream, channels))
    if not candidates:
        raise SignalLoadError(
            f"Open Ephys recording holds no continuous ephys channels: {directory}"
        )
    # max() keeps the first of equals, so ties go to the stream listed first.
    _, stream, channels = max(candidates, key=lambda c: c[0])

    folder = str(stream.get("folder_name", "")).strip("/\\")
    stream_dir = directory / "continuous" / folder
    data_path = stream_dir / "continuous.dat"
    if not data_path.exists():
        raise SignalLoadError(f"Missing continuous.dat for stream '{folder}' in {directory}")
    declared = int(stream.get("num_channels", len(channels)))
    if declared != len(channels):
        raise SignalLoadError(
            f"{_OEBIN} declares {declared} channels for '{folder}' but lists {len(channels)}"
        )
    frame_bytes = len(channels) * np.dtype(np.int16).itemsize
    size = data_path.stat().st_size
    if size % frame_bytes:
        raise SignalLoadError(
            f"{data_path.name} holds {size} bytes, not a whole number of {len(channels)}-channel "
            "int16 frames; the file is truncated or structure.oebin is stale"
        )
    n_samples = size // frame_bytes

    numbers_path = stream_dir / "sample_numbers.npy"
    sample_numbers = (
        np.load(numbers_path, mmap_mode="r")
        if numbers_path.exists()
        else np.arange(n_samples, dtype=np.int64)
    )
    ttl = _read_ttl(directory, structure, folder + "/", sample_numbers)
    return _OpenEphysRecording(directory, structure, stream, channels, data_path, n_samples, ttl)


def _settings_path(directory: Path) -> Path | None:
    """Return the ``settings.xml`` the Record Node wrote for this recording's experiment."""
    experiment = directory.parent
    match = re.fullmatch(r"experiment(\d+)", experiment.name)
    number = int(match.group(1)) if match else 1
    name = "settings.xml" if number == 1 else f"settings_{number}.xml"
    for candidate in (experiment.parent / name, experiment.parent / "settings.xml"):
        if candidate.exists():
            return candidate
    return None


def _headstage_channels(editor: ET.Element) -> list[int]:
    """Channel count of each 32-channel headstage the board editor lists, in port order."""
    options = sorted(editor.iter("HSOPTIONS"), key=lambda o: int(o.get("index", "0")))
    # A slot flagged "full channels" holds a 32-channel headstage; others are empty or ambiguous.
    return [32 for o in options for slot in _HEADSTAGE_SLOTS if o.get(slot) == "1"]


def _read_settings(directory: Path, source_id: int) -> tuple[dict[str, str], list[int]]:
    """Read the source processor's filters and headstages from the Record Node's ``settings.xml``."""
    path = _settings_path(directory)
    if path is None:
        return {}, []
    try:
        # Local file the user chose, not untrusted network input.
        root = ET.parse(path).getroot()  # noqa: S314
    except ET.ParseError as exc:
        logger.warning("Ignoring %s: %s", path, exc)
        return {}, []
    settings = {"version": root.findtext("INFO/VERSION", default="")}
    headstages: list[int] = []
    for processor in root.iter("PROCESSOR"):
        if processor.get("nodeId") != str(source_id):
            continue
        settings["plugin"] = processor.get("pluginName", "")
        editor = processor.find("EDITOR")
        if editor is not None:
            for key in ("LowCut", "HighCut", "DSPOffset", "DSPCutoffFreq"):
                if editor.get(key) is not None:
                    settings[key] = editor.get(key, "")
            headstages = _headstage_channels(editor)
        break
    return settings, headstages


def _acquisition_date(directory: Path) -> str | None:
    """Return the recording start, in UTC ISO 8601, from ``sync_messages.txt``."""
    path = directory / "sync_messages.txt"
    if not path.exists():
        return None
    match = _SOFTWARE_TIME.search(path.read_text(encoding="utf-8", errors="ignore"))
    if match is None:
        return None
    start = dt.datetime.fromtimestamp(int(match.group(1)) / 1000.0, tz=dt.UTC)
    return start.isoformat(timespec="milliseconds")


def _sidecar(directory: Path) -> tuple[list[str], list[str]]:
    """Read the nearest grid sidecar: the recording, its experiment, then its Record Node folder."""
    for folder in (directory, directory.parent, directory.parent.parent):
        grid_names, muscle_names = _read_grid_sidecar(folder / _GRID_SIDECAR)
        if grid_names or muscle_names:
            return grid_names, muscle_names
    return [], []


def _resolve_openephys_grids(
    directory: Path,
    n_emg: int,
    headstages: list[int],
    grid_names: str | list[str] | None,
    muscle_names: str | list[str] | None,
) -> tuple[list[str], list[str]]:
    """Split the ephys channels, in file order, across the grids that cover them."""
    sidecar_grid_names, sidecar_muscle_names = _sidecar(directory)
    resolved = as_name_list(grid_names) or sidecar_grid_names
    if not resolved:
        groups = headstages if sum(headstages) == n_emg else []
        if not groups:
            # No headstage list that accounts for every channel: tile the largest default size.
            per_grid = next(
                (n for n in sorted(_DEFAULT_GRIDS, reverse=True) if n_emg % n == 0), None
            )
            groups = [per_grid] * (n_emg // per_grid) if per_grid else []
        if not groups or any(n not in _DEFAULT_GRIDS for n in groups):
            raise SignalLoadError(
                f"Cannot infer the electrode array for {n_emg} Open Ephys ephys channels: "
                "neither structure.oebin nor settings.xml records one. Pass grid_names=... "
                "to load_openephys, "
                f"or write a '{_GRID_SIDECAR}' sidecar into {directory} (or its experiment or "
                'Record Node folder) of the form {"grid_names": ["GR08MM1305", ...], "muscle_names": '
                '["ta", ...]}.'
            )
        resolved = [_DEFAULT_GRIDS[n] for n in groups]

    counts = [grid_spec(name).n_channels for name in resolved]
    if len(resolved) == 1 and n_emg % counts[0] == 0:
        resolved = resolved * (n_emg // counts[0])
        counts = counts * len(resolved)
    covered = sum(counts)
    if covered != n_emg:
        raise SignalLoadError(
            f"Grid(s) {', '.join(resolved)} cover {covered} electrodes, but the Open Ephys "
            f"recording holds {n_emg} ephys channels."
        )

    return resolved, _resolve_muscles(muscle_names, sidecar_muscle_names, len(resolved), "grid(s)")


def _ttl_row(line: _TtlLine, start: int, stop: int) -> np.ndarray:
    """The 0/1 state of one TTL line over samples ``[start, stop)``."""
    last = np.searchsorted(line.positions, np.arange(start, stop), side="right") - 1
    return np.where(last >= 0, line.states[np.maximum(last, 0)], line.initial)


def load_openephys(
    filepath: str,
    grid_names: str | list[str] | None = None,
    muscle_names: str | list[str] | None = None,
    store: ArrayStore | None = None,
) -> SignalImport:
    """Load an Open Ephys binary recording as a ``SignalImport``, written into ``store`` block by block."""
    store = store if store is not None else RamStore()
    rec = _resolve_openephys_recording(filepath)
    channels = rec.channels
    emg_rows = [i for i, c in enumerate(channels) if c.kind == _EPHYS]
    aux_rows = [i for i, c in enumerate(channels) if c.kind != _EPHYS]

    unknown = sorted({channels[i].units for i in emg_rows} - set(_TO_MV))
    if unknown:
        raise SignalLoadError(
            f"Open Ephys ephys channels in unsupported units: {', '.join(unknown)}"
        )
    emg_scale = np.array([channels[i].bit_volts * _TO_MV[channels[i].units] for i in emg_rows])
    # Channels in units with no volt equivalent (e.g. buffer usage in %) keep their own scale.
    aux_scale = np.array(
        [channels[i].bit_volts * _TO_V.get(channels[i].units, 1.0) for i in aux_rows]
    )

    stream = rec.stream
    settings, headstages = _read_settings(rec.directory, int(stream.get("source_processor_id", -1)))
    grid_names, muscle_names = _resolve_openephys_grids(
        rec.directory, len(emg_rows), headstages, grid_names, muscle_names
    )

    n_samples = rec.n_samples
    data = store.allocate("emg", (len(emg_rows), n_samples), np.float32)
    aux_data = store.allocate("aux", (len(aux_rows) + len(rec.ttl), n_samples), np.float32)
    if n_samples:
        raw = np.memmap(rec.data_path, dtype="<i2", mode="r", shape=(n_samples, len(channels)))
        for start, stop in sample_blocks(n_samples, len(channels)):
            block = np.asarray(raw[start:stop]).T.astype(np.float64)
            data[:, start:stop] = block[emg_rows] * emg_scale[:, None]
            aux_data[: len(aux_rows), start:stop] = block[aux_rows] * aux_scale[:, None]
            for row, line in enumerate(rec.ttl, start=len(aux_rows)):
                aux_data[row, start:stop] = _ttl_row(line, start, stop)
        del raw
    data = store.seal(data)
    aux_data = store.seal(aux_data)
    aux_names = [channels[i].name for i in aux_rows] + [f"TTL{line.line}" for line in rec.ttl]
    aux_digital = [False] * len(aux_rows) + [True] * len(rec.ttl)

    low_cut = float(settings["LowCut"]) if "LowCut" in settings else None
    high_cut = float(settings["HighCut"]) if "HighCut" in settings else None
    if low_cut is not None and settings.get("DSPOffset") == "1" and "DSPCutoffFreq" in settings:
        low_cut = max(low_cut, float(settings["DSPCutoffFreq"]))
    hardware_filter = (
        f"HP: {low_cut:.2f} Hz, LP: {high_cut:.2f} Hz, Notch: none"
        if low_cut is not None and high_cut is not None
        else "n/a"
    )

    device_name = str(stream.get("source_processor_name", "")) or "Open Ephys"
    gui_version = str(rec.structure.get("GUI version", "")) or settings.get("version", "")
    n_emg, n_aux = len(emg_rows), len(aux_names)
    metadata: dict[str, Any] = {
        "acquisition_date": _acquisition_date(rec.directory),
        "manufacturer": "Open Ephys",
        "device_name": device_name,
        "software_versions": f"Open Ephys GUI {gui_version}" if gui_version else "Open Ephys GUI",
        "hardware_filters": [hardware_filter],
        "powerline_freq": None,
        "units": "mV",
        "gains": [_AMPLIFIER_GAIN] * n_emg,
        "emg_hpf": ["n/a" if low_cut is None else low_cut] * n_emg,
        "emg_lpf": ["n/a" if high_cut is None else high_cut] * n_emg,
        "aux_units": "V",
        "aux_gains": [1.0] * n_aux,
        "aux_hpf": ["n/a"] * n_aux,
        "aux_lpf": ["n/a"] * n_aux,
        "aux_kinds": aux_kinds(aux_names, aux_digital),
        "recording_type": "continuous",
        "software_filters": "n/a",
    }

    return SignalImport.build(
        data=data,
        fsamp=float(stream.get("sample_rate", 0.0)),
        grid_names=grid_names,
        muscle_names=muscle_names,
        aux_data=aux_data,
        aux_names=aux_names,
        metadata=metadata,
    )
