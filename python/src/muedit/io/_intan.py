"""Intan Technologies RHD recording loaders for MUedit."""

from __future__ import annotations

import json
import struct
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from muedit.signal.grid import format_hdemg_signal

_RHD_MAGIC = 0xC6912702

_AMPLIFIER = 0
_AUX_INPUT = 1
_SUPPLY_VOLTAGE = 2
_BOARD_ADC = 3
_DIGITAL_IN = 4
_DIGITAL_OUT = 5

_FILE_PREFIX = {
    _AMPLIFIER: "amp",
    _AUX_INPUT: "aux",
    _SUPPLY_VOLTAGE: "vdd",
    _BOARD_ADC: "board",
    _DIGITAL_IN: "board",
    _DIGITAL_OUT: "board",
}

_SIGNAL_TYPE_FILES = {
    _AMPLIFIER: "amplifier.dat",
    _AUX_INPUT: "auxiliary.dat",
    _SUPPLY_VOLTAGE: "supply.dat",
    _BOARD_ADC: "analogin.dat",
    _DIGITAL_IN: "digitalin.dat",
    _DIGITAL_OUT: "digitalout.dat",
}

_AMP_STEP_MV = 0.195e-3
_AUX_STEP_V = 37.4e-6
_SUPPLY_STEP_V = 74.8e-6

_ADC_STEP_V = {0: 50.354e-6, 1: 152.59e-6, 13: 312.5e-6}
_ADC_OFFSET = {0: 0, 1: 32768, 13: 32768}
_ADC_STEP_DEFAULT = 50.354e-6

_NOTCH_HZ = {0: None, 1: 50.0, 2: 60.0}

_RHD_AMPLIFIER_GAIN = 192.0

_GRID_SIDECAR = "muedit_grids.json"

_DEFAULT_GRIDS = {64: "INTAN64-1305"}


class _HeaderCursor:
    """Little-endian byte cursor over an RHD header block."""

    def __init__(self, buf: bytes) -> None:
        self._buf = buf
        self.offset = 0

    def scalar(self, fmt: str) -> Any:
        """Read one packed value of ``fmt`` and advance past it."""
        size = struct.calcsize(fmt)
        if self.offset + size > len(self._buf):
            raise OSError("RHD header ended mid-field; file is truncated or not an RHD header")
        (value,) = struct.unpack_from("<" + fmt, self._buf, self.offset)
        self.offset += size
        return value

    def qstring(self) -> str:
        """Read a Qt ``QString``: a byte length followed by UTF-16LE text."""
        length = self.scalar("I")
        if length == 0xFFFFFFFF:
            return ""
        if self.offset + length > len(self._buf):
            raise OSError("RHD header string ran past end of file")
        text = self._buf[self.offset : self.offset + length].decode("utf-16-le", errors="ignore")
        self.offset += length
        return text


@dataclass
class _IntanChannel:
    """One channel record from the header's signal-group table."""

    native_name: str
    custom_name: str
    native_order: int
    signal_type: int
    enabled: bool
    port_name: str
    port_prefix: str
    chip_channel: int
    board_stream: int
    impedance_magnitude: float
    impedance_phase: float


@dataclass
class _IntanHeader:
    """Everything the RHD header states about a recording."""

    version: tuple[int, int]
    fsamp: float
    channels: list[_IntanChannel]
    header_bytes: int
    dsp_enabled: bool
    dsp_cutoff: float
    lower_bandwidth: float
    upper_bandwidth: float
    notch_hz: float | None
    eval_board_mode: int
    reference_channel: str
    notes: list[str] = field(default_factory=list)
    n_temp_sensors: int = 0

    def enabled_channels(self, signal_type: int) -> list[_IntanChannel]:
        """Return enabled channels of one signal type in header (acquisition) order."""
        return [c for c in self.channels if c.signal_type == signal_type and c.enabled]

    @property
    def samples_per_block(self) -> int:
        """Samples per data block, which grew from 60 to 128 in version 1.2."""
        return 60 if self.version < (1, 2) else 128


def _parse_rhd_header(buf: bytes) -> _IntanHeader:
    """Parse an RHD header block into an :class:`_IntanHeader`."""
    cur = _HeaderCursor(buf)
    if cur.scalar("I") != _RHD_MAGIC:
        raise OSError("Not an Intan RHD file: magic number mismatch")

    version = (int(cur.scalar("h")), int(cur.scalar("h")))
    fsamp = float(cur.scalar("f"))

    dsp_enabled = bool(cur.scalar("h"))
    dsp_cutoff = float(cur.scalar("f"))
    lower_bandwidth = float(cur.scalar("f"))
    upper_bandwidth = float(cur.scalar("f"))
    cur.scalar("f")
    cur.scalar("f")
    cur.scalar("f")

    notch_hz = _NOTCH_HZ.get(int(cur.scalar("h")))
    cur.scalar("f")
    cur.scalar("f")

    notes = [cur.qstring(), cur.qstring(), cur.qstring()]

    n_temp_sensors = int(cur.scalar("h")) if version >= (1, 1) else 0
    eval_board_mode = int(cur.scalar("h")) if version >= (1, 3) else 0
    reference_channel = cur.qstring() if version >= (2, 0) else ""

    channels: list[_IntanChannel] = []
    for _ in range(int(cur.scalar("h"))):
        port_name = cur.qstring()
        port_prefix = cur.qstring()
        group_enabled = int(cur.scalar("h"))
        n_channels = int(cur.scalar("h"))
        cur.scalar("h")
        if not (group_enabled and n_channels):
            continue
        for _ in range(n_channels):
            native_name = cur.qstring()
            custom_name = cur.qstring()
            native_order = int(cur.scalar("h"))
            cur.scalar("h")
            signal_type = int(cur.scalar("h"))
            channel_enabled = bool(cur.scalar("h"))
            chip_channel = int(cur.scalar("h"))
            board_stream = int(cur.scalar("h"))
            for _ in range(4):
                cur.scalar("h")
            impedance_magnitude = float(cur.scalar("f"))
            impedance_phase = float(cur.scalar("f"))
            channels.append(
                _IntanChannel(
                    native_name=native_name,
                    custom_name=custom_name or native_name,
                    native_order=native_order,
                    signal_type=signal_type,
                    enabled=channel_enabled,
                    port_name=port_name,
                    port_prefix=port_prefix,
                    chip_channel=chip_channel,
                    board_stream=board_stream,
                    impedance_magnitude=impedance_magnitude,
                    impedance_phase=impedance_phase,
                )
            )

    return _IntanHeader(
        version=version,
        fsamp=fsamp,
        channels=channels,
        header_bytes=cur.offset,
        dsp_enabled=dsp_enabled,
        dsp_cutoff=dsp_cutoff,
        lower_bandwidth=lower_bandwidth,
        upper_bandwidth=upper_bandwidth,
        notch_hz=notch_hz,
        eval_board_mode=eval_board_mode,
        reference_channel=reference_channel,
        notes=[n for n in notes if n],
        n_temp_sensors=n_temp_sensors,
    )


def _read_settings_xml(directory: Path) -> dict[str, str]:
    """Read the optional ``settings.xml`` RHX writes beside a recording."""
    path = directory / "settings.xml"
    if not path.exists():
        return {}
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return {}
    general = root.find("GeneralConfig")
    return {
        "software_version": root.get("Version", ""),
        "controller_type": root.get("Type", ""),
        "file_format": general.get("FileFormat", "") if general is not None else "",
    }


@dataclass
class _Recording:
    """A resolved Intan recording: its header, its layout, and where data lives."""

    header: _IntanHeader
    layout: str
    directory: Path
    data_path: Path
    settings: dict[str, str]


def _resolve_recording(filepath: str | Path) -> _Recording:
    """Locate the header and decide which of the three save layouts applies."""
    path = Path(filepath)
    if path.is_dir():
        directory = path
        candidates = sorted(directory.glob("*.rhd"))
        if not candidates:
            raise FileNotFoundError(f"No .rhd header found in Intan directory: {directory}")
        header_path = next((c for c in candidates if c.name == "info.rhd"), candidates[0])
    elif path.suffix.lower() == ".rhd":
        header_path, directory = path, path.parent
    else:
        raise ValueError(f"Not an Intan recording: {path}")

    with open(header_path, "rb") as handle:
        header = _parse_rhd_header(handle.read())

    settings = _read_settings_xml(directory)
    if header_path.stat().st_size > header.header_bytes:
        return _Recording(header, "traditional", directory, header_path, settings)

    if any(directory.glob("amp-*.dat")):
        layout = "per_channel"
    elif (directory / "amplifier.dat").exists():
        layout = "per_signal_type"
    else:
        raise FileNotFoundError(
            f"{header_path.name} carries no samples and no amplifier data files were found "
            f"in {directory}. Expected 'amp-<port>-<nnn>.dat' or 'amplifier.dat'."
        )
    return _Recording(header, layout, directory, directory, settings)


def _channel_filename(channel: _IntanChannel) -> str:
    """Return the per-channel filename Intan writes for one channel."""
    return f"{_FILE_PREFIX[channel.signal_type]}-{channel.native_name}.dat"


def _read_per_channel(rec: _Recording) -> tuple[dict[int, np.ndarray], int]:
    """Read the one-file-per-channel layout."""
    time_path = rec.directory / "time.dat"
    if not time_path.exists():
        raise FileNotFoundError(f"Missing time.dat in Intan recording: {rec.directory}")
    n_samples = time_path.stat().st_size // np.dtype(np.int32).itemsize

    raw: dict[int, np.ndarray] = {}
    for signal_type in (_AMPLIFIER, _AUX_INPUT, _SUPPLY_VOLTAGE, _BOARD_ADC, _DIGITAL_IN,
                        _DIGITAL_OUT):
        channels = rec.header.enabled_channels(signal_type)
        if not channels:
            continue
        dtype = np.int16 if signal_type == _AMPLIFIER else np.uint16
        block = np.zeros((len(channels), n_samples), dtype=np.float64)
        for row, channel in enumerate(channels):
            path = rec.directory / _channel_filename(channel)
            if not path.exists():
                raise FileNotFoundError(
                    f"Header lists channel '{channel.native_name}' as saved, but "
                    f"{path.name} is missing from {rec.directory}"
                )
            values = np.fromfile(path, dtype=dtype)
            if values.size < n_samples:
                raise OSError(
                    f"{path.name} holds {values.size} samples but time.dat declares {n_samples}"
                )
            block[row] = values[:n_samples]
        raw[signal_type] = block
    return raw, n_samples


def _read_per_signal_type(rec: _Recording) -> tuple[dict[int, np.ndarray], int]:
    """Read the one-file-per-signal-type layout."""
    time_path = rec.directory / "time.dat"
    if not time_path.exists():
        raise FileNotFoundError(f"Missing time.dat in Intan recording: {rec.directory}")
    n_samples = time_path.stat().st_size // np.dtype(np.int32).itemsize

    raw: dict[int, np.ndarray] = {}
    for signal_type, filename in _SIGNAL_TYPE_FILES.items():
        channels = rec.header.enabled_channels(signal_type)
        path = rec.directory / filename
        if not channels or not path.exists():
            continue
        dtype = np.int16 if signal_type == _AMPLIFIER else np.uint16
        values = np.fromfile(path, dtype=dtype)
        n_rows = 1 if signal_type in (_DIGITAL_IN, _DIGITAL_OUT) else len(channels)
        if values.size % n_rows:
            raise OSError(f"{filename} does not divide into {n_rows} channels")
        block = values.reshape((-1, n_rows)).T.astype(np.float64)
        if signal_type in (_DIGITAL_IN, _DIGITAL_OUT):
            block = _split_digital_word(block[0], channels)
        raw[signal_type] = _expand_to(block, n_samples)
    return raw, n_samples


def _read_traditional(rec: _Recording) -> tuple[dict[int, np.ndarray], int]:
    """Read a monolithic ``.rhd`` with fixed-size data blocks following the header."""
    header = rec.header
    n_per_block = header.samples_per_block
    counts = {t: len(header.enabled_channels(t)) for t in _SIGNAL_TYPE_FILES}

    stamp_dtype = "<u4" if header.version < (1, 2) else "<i4"
    fields: list[tuple[str, str, tuple[int, ...]]] = [("timestamp", stamp_dtype, (n_per_block,))]
    if counts[_AMPLIFIER]:
        fields.append(("amplifier", "<u2", (counts[_AMPLIFIER], n_per_block)))
    if counts[_AUX_INPUT]:
        fields.append(("aux", "<u2", (counts[_AUX_INPUT], n_per_block // 4)))
    if counts[_SUPPLY_VOLTAGE]:
        fields.append(("supply", "<u2", (counts[_SUPPLY_VOLTAGE], 1)))
    if header.n_temp_sensors:
        fields.append(("temp", "<i2", (header.n_temp_sensors, 1)))
    if counts[_BOARD_ADC]:
        fields.append(("adc", "<u2", (counts[_BOARD_ADC], n_per_block)))
    if counts[_DIGITAL_IN]:
        fields.append(("digital_in", "<u2", (n_per_block,)))
    if counts[_DIGITAL_OUT]:
        fields.append(("digital_out", "<u2", (n_per_block,)))

    block_dtype = np.dtype(fields)
    file_bytes = rec.data_path.stat().st_size - header.header_bytes
    if file_bytes % block_dtype.itemsize:
        raise OSError(
            f"{rec.data_path.name} holds {file_bytes} data bytes, not a whole number of "
            f"{block_dtype.itemsize}-byte blocks; the file is truncated or the header is stale"
        )
    with open(rec.data_path, "rb") as handle:
        handle.seek(header.header_bytes)
        blocks = np.fromfile(handle, dtype=block_dtype)

    n_samples = int(blocks.size) * n_per_block
    raw: dict[int, np.ndarray] = {}
    for signal_type, key in (
        (_AMPLIFIER, "amplifier"),
        (_AUX_INPUT, "aux"),
        (_SUPPLY_VOLTAGE, "supply"),
        (_BOARD_ADC, "adc"),
    ):
        if key not in block_dtype.names:
            continue
        stacked = np.concatenate(list(blocks[key]), axis=1).astype(np.float64)
        raw[signal_type] = _expand_to(stacked, n_samples)
    for signal_type, key in ((_DIGITAL_IN, "digital_in"), (_DIGITAL_OUT, "digital_out")):
        if key not in block_dtype.names:
            continue
        words = blocks[key].reshape(-1).astype(np.float64)
        raw[signal_type] = _split_digital_word(words, header.enabled_channels(signal_type))

    if _AMPLIFIER in raw:
        raw[_AMPLIFIER] -= 32768.0
    return raw, n_samples


def _split_digital_word(words: np.ndarray, channels: list[_IntanChannel]) -> np.ndarray:
    """Expand a packed digital word into one 0/1 row per saved channel."""
    codes = words.astype(np.uint16)
    return np.vstack([((codes >> c.native_order) & 1).astype(np.float64) for c in channels])


def _expand_to(values: np.ndarray, n_samples: int) -> np.ndarray:
    """Stretch a sub-sampled stream to the amplifier sample count by repetition."""
    have = values.shape[-1]
    if have == n_samples:
        return values
    if have == 0:
        return np.zeros((values.shape[0], n_samples), dtype=np.float64)
    factor = -(-n_samples // have)
    return np.repeat(values, factor, axis=-1)[..., :n_samples]


def _scale_auxiliary(
    raw: dict[int, np.ndarray], header: _IntanHeader
) -> tuple[np.ndarray, list[str]]:
    """Convert every non-amplifier stream to physical units and label it."""
    adc_step = _ADC_STEP_V.get(header.eval_board_mode, _ADC_STEP_DEFAULT)
    adc_offset = _ADC_OFFSET.get(header.eval_board_mode, 0)
    scaling: dict[int, tuple[float, float]] = {
        _AUX_INPUT: (_AUX_STEP_V, 0.0),
        _SUPPLY_VOLTAGE: (_SUPPLY_STEP_V, 0.0),
        _BOARD_ADC: (adc_step, float(adc_offset)),
        _DIGITAL_IN: (1.0, 0.0),
        _DIGITAL_OUT: (1.0, 0.0),
    }

    rows: list[np.ndarray] = []
    names: list[str] = []
    for signal_type, (step, offset) in scaling.items():
        block = raw.get(signal_type)
        if block is None:
            continue
        rows.append((block - offset) * step)
        names.extend(c.custom_name for c in header.enabled_channels(signal_type))
    if not rows:
        return np.zeros((0, 0), dtype=np.float64), []
    return np.vstack(rows), names


def _resolve_grids(
    rec: _Recording,
    ports: list[str],
    channels_per_port: list[int],
    grid_names: str | list[str] | None,
    muscles: str | list[str] | None,
) -> tuple[list[str], list[str]]:
    """Resolve the grid model and muscle label of each amplifier port."""
    sidecar_names: list[str] = []
    sidecar_muscles: list[str] = []
    sidecar = rec.directory / _GRID_SIDECAR
    if sidecar.exists():
        try:
            payload = json.loads(sidecar.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise OSError(f"Cannot read grid sidecar {sidecar}: {exc}") from exc
        sidecar_names = [str(g) for g in _as_list(payload.get("gridname"))]
        sidecar_muscles = [str(m) for m in _as_list(payload.get("muscle"))]

    resolved = [str(g) for g in _as_list(grid_names)] or sidecar_names
    if not resolved:
        missing = sorted({n for n in channels_per_port if n not in _DEFAULT_GRIDS})
        if missing:
            raise ValueError(
                f"Cannot infer the electrode array for Intan port(s) with {missing} channels: "
                "the RHD header does not record one. Pass grid_names=... to load_intan, or "
                f"write a '{_GRID_SIDECAR}' sidecar into {rec.directory} of the form "
                '{"gridname": ["GR08MM1305", ...], "muscle": ["ta", ...]}.'
            )
        resolved = [_DEFAULT_GRIDS[n] for n in channels_per_port]

    if len(resolved) == 1 and len(ports) > 1:
        resolved = resolved * len(ports)
    if len(resolved) != len(ports):
        raise ValueError(
            f"Got {len(resolved)} grid name(s) for {len(ports)} amplifier port(s) "
            f"({', '.join(ports)}); pass one name per port or a single shared name."
        )

    resolved_muscles = [str(m) for m in _as_list(muscles)] or sidecar_muscles
    if resolved_muscles and len(resolved_muscles) == 1 and len(ports) > 1:
        resolved_muscles = resolved_muscles * len(ports)
    if resolved_muscles and len(resolved_muscles) != len(ports):
        raise ValueError(
            f"Got {len(resolved_muscles)} muscle label(s) for {len(ports)} amplifier port(s)"
        )
    return resolved, resolved_muscles


def _as_list(value: Any) -> list[Any]:
    """Normalize ``None`` / scalar / sequence inputs to a list."""
    if value is None:
        return []
    if isinstance(value, (str, bytes)):
        return [value]
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def load_intan(
    filepath: str,
    grid_names: str | list[str] | None = None,
    muscles: str | list[str] | None = None,
) -> dict[str, Any]:
    """Load an Intan RHD recording into the MUedit signal dictionary format."""
    rec = _resolve_recording(filepath)
    header = rec.header
    if not header.enabled_channels(_AMPLIFIER):
        raise ValueError(f"Intan recording holds no enabled amplifier channels: {filepath}")

    readers = {
        "per_channel": _read_per_channel,
        "per_signal_type": _read_per_signal_type,
        "traditional": _read_traditional,
    }
    raw, n_samples = readers[rec.layout](rec)

    amp_channels = header.enabled_channels(_AMPLIFIER)
    data = raw[_AMPLIFIER] * _AMP_STEP_MV
    auxiliary, aux_names = _scale_auxiliary(raw, header)

    ports: list[str] = []
    channels_per_port: list[int] = []
    for channel in amp_channels:
        if channel.port_prefix not in ports:
            ports.append(channel.port_prefix)
            channels_per_port.append(0)
        channels_per_port[ports.index(channel.port_prefix)] += 1
    order = np.argsort([ports.index(c.port_prefix) for c in amp_channels], kind="stable")
    data = data[order]
    amp_channels = [amp_channels[i] for i in order]

    resolved_grids, resolved_muscles = _resolve_grids(
        rec, ports, channels_per_port, grid_names, muscles
    )
    coordinates, ieds, discard_vecs, emg_types = format_hdemg_signal(resolved_grids)

    high_pass = max(header.dsp_cutoff, header.lower_bandwidth) if header.dsp_enabled else (
        header.lower_bandwidth
    )
    hardware_filter = (
        f"HP: {high_pass:.2f} Hz, LP: {header.upper_bandwidth:.2f} Hz, "
        f"Notch: {'none' if header.notch_hz is None else f'{header.notch_hz:.0f} Hz'}"
    )

    n_emg = data.shape[0]
    metadata: dict[str, Any] = {
        "acquisition_date": None,
        "manufacturer": "Intan Technologies",
        "device_name": rec.settings.get("controller_type") or "Intan RHD",
        "manufacturers_model_name": rec.settings.get("controller_type") or "Intan RHD",
        "software_versions": (
            f"IntanRHX {rec.settings['software_version']}"
            if rec.settings.get("software_version")
            else f"RHD file format {header.version[0]}.{header.version[1]}"
        ),
        "ad_bits": 16,
        "coordinates": coordinates,
        "ieds": ieds,
        "discard_channels": discard_vecs,
        "emg_types": emg_types,
        "hardware_filters": [hardware_filter],
        "channel_map_filters": {},
        "gains": [_RHD_AMPLIFIER_GAIN] * n_emg,
        "emg_hpf": [high_pass] * n_emg,
        "emg_lpf": [header.upper_bandwidth] * n_emg,
        "aux_gains": [1.0] * len(aux_names),
        "aux_hpf": ["n/a"] * len(aux_names),
        "aux_lpf": ["n/a"] * len(aux_names),
        "units": "mV",
        "aux_units": "V",
        "recording_type": "continuous",
        "software_filters": "n/a",
        "powerline_freq": header.notch_hz or 50.0,
        "intan_layout": rec.layout,
        "intan_ports": ports,
        "intan_channel_names": [c.native_name for c in amp_channels],
        "intan_eval_board_mode": header.eval_board_mode,
        "intan_reference_channel": header.reference_channel,
        "intan_notes": header.notes,
        "electrode_impedances": [c.impedance_magnitude for c in amp_channels],
    }

    return {
        "data": data,
        "fsamp": float(header.fsamp),
        "gridname": resolved_grids,
        "muscle": resolved_muscles,
        "auxiliary": auxiliary if auxiliary.size else np.zeros((0, n_samples), dtype=np.float64),
        "auxiliaryname": aux_names,
        "metadata": metadata,
    }
