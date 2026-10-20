"""OT Bioelettronica OTB+ and OTB4 file loaders for MUedit."""

from __future__ import annotations

import datetime as dt
import logging
import os
import re
import tarfile
import tempfile
from collections import defaultdict
from dataclasses import dataclass, replace
from typing import Any

import numpy as np
import xmltodict

from muedit.errors import SignalLoadError
from muedit.models import SignalImport
from muedit.storage import ArrayStore, RamStore, sample_blocks

logger = logging.getLogger(__name__)

#: Adapters that carry control or orientation signals, not recorded channels.
_CONTROL_ADAPTERS = ("AdapterControl", "AdapterQuaternions")


def _ensure_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else [value]


def _safe_gain(gain_value: Any) -> float:
    try:
        g_val = float(gain_value)
    except (TypeError, ValueError):
        return 1.0
    return g_val if g_val != 0 else 1.0


def _parse_filter_string(filter_str: str, fsamp: float | None = None) -> str | float:
    if not filter_str or filter_str == "n/a":
        return "n/a"
    filter_str = str(filter_str).strip()
    hz_match = re.search(r"([\d.]+)\s*Hz", filter_str, re.IGNORECASE)
    if hz_match:
        try:
            return float(hz_match.group(1))
        except ValueError:
            pass
    fsamp_match = re.search(r"Fsamp\s*/\s*([\d.]+)", filter_str, re.IGNORECASE)
    if fsamp_match and fsamp:
        try:
            divisor = float(fsamp_match.group(1))
            if divisor != 0:
                return fsamp / divisor
        except ValueError:
            pass
    num_match = re.search(r"^([\d.]+)$", filter_str)
    if num_match:
        try:
            return float(num_match.group(1))
        except ValueError:
            pass
    return "n/a"


def _find_file(tmp_dir: str, name: str) -> str | None:
    for root, _, files in os.walk(tmp_dir):
        if name in files:
            return os.path.join(root, name)
    return None


def _group_tracks(
    track_info: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for track in track_info:
        grouped[track["SignalStreamPath"]].append(track)
    for path in grouped:
        grouped[path].sort(key=lambda t: int(t["AcquisitionChannel"]))
    return grouped


def _frame_count(file_path: str, dtype: np.dtype, n_channels: int) -> int:
    """The samples of a ``.sig`` file of ``n_channels`` interleaved channels."""
    n_values = os.path.getsize(file_path) // dtype.itemsize
    if n_values % n_channels != 0:
        raise SignalLoadError(
            f"Cannot reshape {os.path.basename(file_path)} into {n_channels} channels"
        )
    return n_values // n_channels


def _sample_frames(file_path: str, dtype: np.dtype, n_channels: int) -> np.ndarray:
    """A ``.sig`` file as a read-only ``(samples, channels)`` map: channels are interleaved."""
    n_frames = _frame_count(file_path, dtype, n_channels)
    if n_frames == 0:
        return np.zeros((0, n_channels), dtype=dtype)
    return np.memmap(file_path, dtype=dtype, mode="r", shape=(n_frames, n_channels))


def _write_rows(out: np.ndarray, frames: np.ndarray, rows: np.ndarray, scale: np.ndarray) -> None:
    """Write channels ``rows`` of ``frames`` times ``scale`` into ``out``, block by block.

    The product is taken in ``scale.dtype``, as the loaders always scaled.
    """
    factors = scale[:, None]
    for start, stop in sample_blocks(out.shape[1], len(rows), scale.dtype.itemsize):
        block = frames[start:stop, rows].T.astype(scale.dtype)
        block *= factors
        out[:, start:stop] = block


def _otb_plus_scale(device_name: str, adapter_id: str, gain: float, ad_bits: int) -> float:
    """Device-specific ADC→mV factor of one channel; 1.0 where the data is already in units."""
    # OTBioLab+ names the device with its serial, e.g. "QUATTROCENTO; ID=Unknown".
    device_name = device_name.split(";", 1)[0].strip().upper()
    if device_name in {"QUATTROCENTO", "QUATTRO"}:
        if adapter_id == "Direct connection":
            return 0.1526
        if adapter_id == "AdapterControl":
            return 1.0
        return 0.00050863
    if device_name in {"DUE+", "QUATTRO+"}:
        return 1.0 if adapter_id in {"AdapterControl", "AdapterQuaternions"} else 0.00024928
    if device_name == "DUE":
        return 1.0 if adapter_id in {"AdapterControl", "AdapterQuaternions"} else 0.00025177
    if device_name in {"SESSANTAQUATTRO", "SESSANTAQUATTRO+"}:
        if adapter_id in {"AdapterControl", "AdapterQuaternions"}:
            return 1.0
        if adapter_id == "Direct connection to Auxiliary Input":
            return 0.00014648 if ad_bits == 16 else 0.00000057220
        codes: dict[float, float] = {}
        if ad_bits == 16:
            codes = {256: 1, 128: 0.5, 64: 0.75}
        elif ad_bits == 24:
            codes = {1: 1, 0.5: 2, 0.25: 3, 0.125: 4}
        gain_val = codes.get(gain, gain)
        return 4.8 / (2**24) * 1000 / gain_val
    if device_name == "SYNCSTATION":
        if adapter_id in {"Due+", "Quattro+"}:
            return 0.00024928
        if adapter_id == "Direct connection to Syncstation Input":
            return 0.1526
        if adapter_id == "AdapterLoadCell":
            return 0.00037217
        if adapter_id in {"AdapterControl", "AdapterQuaternions"}:
            return 1.0
        return 0.00028610
    if adapter_id == "Direct connection to Auxiliary Input":
        return 0.00000057220
    if adapter_id in {"AdapterControl", "AdapterQuaternions"}:
        return 1.0
    gain_val = gain if gain != 0 else 1.0
    return 4.8 / (2**24) * 1000 / gain_val


@dataclass(frozen=True)
class _SigFile:
    """A ``.sig`` file of interleaved channels, mapped only while it is read."""

    path: str
    dtype: np.dtype
    n_channels: int
    n_frames: int

    @classmethod
    def open(cls, path: str, dtype: np.dtype, n_channels: int) -> _SigFile:
        """The file at ``path``, its length checked against ``n_channels``."""
        return cls(path, dtype, n_channels, _frame_count(path, dtype, n_channels))

    def frames(self) -> np.ndarray:
        """The file as a read-only ``(samples, channels)`` map, to drop once read."""
        # Windows cannot delete a mapped file, so no map may outlive the extraction folder.
        return _sample_frames(self.path, self.dtype, self.n_channels)


@dataclass
class _Segment:
    """One track's channels: their columns in a ``.sig`` file and their scale factor."""

    sig: _SigFile
    rows: np.ndarray
    scale: float


@dataclass
class _Trace:
    """One feedback channel (target or performed path) at its own sampling rate."""

    name: str
    values: np.ndarray
    fsamp: float


def _read_feedback_traces(tmpdir: str) -> list[_Trace]:
    """Feedback channels (float64, already in %MVC) of track files besides ``Tracks_000.xml``."""
    xml_paths = sorted(
        os.path.join(root, f)
        for root, _, files in os.walk(tmpdir)
        for f in files
        if f.endswith(".xml") and "Tracks_" in f and f != "Tracks_000.xml"
    )
    traces: list[_Trace] = []
    for xml_path in xml_paths:
        with open(xml_path, "rb") as fd:
            root_node = xmltodict.parse(fd.read()).get("ArrayOfTrackInfo") or {}
        for track in _ensure_list(root_node.get("TrackInfo") or []):
            sig_name = os.path.basename(track.get("SignalStreamPath", ""))
            name = " - ".join(p for p in (track.get("Title"), track.get("SubTitle")) if p)
            sig_path = _find_file(tmpdir, sig_name) if sig_name else None
            if sig_path is None:
                logger.warning("Skipping feedback track %s: %s is missing", name, sig_name)
                continue
            if int(track.get("SampleSize", 0)) != 8:
                logger.warning("Skipping feedback track %s: samples are not float64", name)
                continue
            n_total = int(track["TotalChannelsInFile"])
            values = np.fromfile(sig_path, dtype=np.float64)
            if values.size == 0 or values.size % n_total != 0:
                logger.warning(
                    "Skipping feedback track %s: cannot split %s into %d channels",
                    name,
                    sig_name,
                    n_total,
                )
                continue
            frames = values.reshape(-1, n_total)
            first = int(track["AcquisitionChannel"])
            n_ch = int(track.get("NumberOfChannels", 1))
            traces.extend(
                _Trace(
                    name=name if n_ch == 1 else f"{name} {i + 1}",
                    values=frames[:, first + i].copy(),
                    fsamp=float(track["SamplingFrequency"]),
                )
                for i in range(n_ch)
            )
    return traces


def _fit_trace(trace: _Trace, fsamp: float, n_samples: int) -> np.ndarray:
    """``trace`` on the EMG clock: linear between its samples, its last value held past its end."""
    t_in = np.arange(trace.values.size) / trace.fsamp
    t_out = np.arange(n_samples) / fsamp
    return np.interp(t_out, t_in, trace.values)


def _write_segments(
    store: ArrayStore,
    name: str,
    segments: list[_Segment],
    n_samples: int,
    n_copy: int,
    dtype: type[np.floating[Any]],
    traces: list[_Trace] | None = None,
    fsamp: float = 0.0,
) -> np.ndarray:
    """Stack the segments' channels (``n_copy`` samples, zeros after), then ``traces``, as float32."""
    traces = traces or []
    n_rows = sum(len(seg.rows) for seg in segments)
    out = store.allocate(name, (n_rows + len(traces), n_samples), np.float32, zero=True)
    width = min(n_copy, n_samples)
    row = 0
    for seg in segments:
        scale = np.full(len(seg.rows), seg.scale, dtype=dtype)
        _write_rows(out[row : row + len(seg.rows), :width], seg.sig.frames(), seg.rows, scale)
        row += len(seg.rows)
    for i, trace in enumerate(traces):
        out[n_rows + i] = _fit_trace(trace, fsamp, n_samples)
    return store.seal(out)


def _read_sip(path: str, n_samples: int) -> np.ndarray | None:
    """One ``.sip`` channel (a float64 target or path trace) fitted to the EMG length.

    A longer trace is cut; a shorter one holds its last value to the end, as a force
    target would. An unreadable or empty file is skipped.
    """
    name = os.path.basename(path)
    try:
        values = np.fromfile(path, dtype=np.float64)
    except (OSError, ValueError) as exc:
        logger.warning("Skipping %s: cannot read it (%s)", name, exc)
        return None
    if values.size == 0:
        logger.warning("Skipping %s: it holds no samples", name)
        return None
    if values.size < n_samples:
        logger.warning(
            "%s holds %d samples, the EMG %d: holding its last value to the end",
            name,
            values.size,
            n_samples,
        )
        return np.concatenate([values, np.full(n_samples - values.size, values[-1])])
    return values[:n_samples]


def _extract(filepath: str, tmpdir: str, kind: str) -> None:
    """Unpack an OTB+ or OTB4 archive (a tar file) into ``tmpdir``."""
    try:
        with tarfile.open(filepath, "r") as tar:
            tar.extractall(tmpdir, filter="data")
    except (tarfile.TarError, OSError) as exc:
        raise SignalLoadError(f"Failed to extract {kind} file: {exc}") from exc


def _iso_date(text: Any) -> str | None:
    """``text`` as an ISO 8601 date and time, or ``None`` when it is not one (OTB writes ``Unknown``)."""
    try:
        return dt.datetime.fromisoformat(str(text).strip()).isoformat()
    except ValueError:
        return None


def _sig_date(sig_file: str) -> str | None:
    """The start time OTBioLab+ puts in a signal file's name, ``YYYYMMDDhhmmss_…sig``."""
    match = re.match(r"(\d{14})", sig_file)
    if match is None:
        return None
    try:
        return dt.datetime.strptime(match[1], "%Y%m%d%H%M%S").isoformat()
    except ValueError:
        return None


def _channel_kind(description: str) -> str | None:
    """``"emg"`` for a 32/64-channel grid input, ``None`` for a bipolar or 16-channel one, else ``"aux"``."""
    if "General" in description or "iEMG" in description or "16" in description:
        return None
    if "32" in description or "64" in description or "Splitter" in description:
        return "emg"
    return "aux"


def _grid_position(device_name: str, adapter_index: str, prefix: str) -> int:
    """The grid a channel belongs to: from its input prefix on a Quattrocento, else its adapter."""
    try:
        if "QUATTROCENTO" not in device_name:
            return int(adapter_index)
        if "MULTIPLE IN" in prefix:
            return int(prefix[12]) + 2 if len(prefix) > 12 else 0
        if "IN" in prefix:
            return (1 if int(prefix[3]) < 5 else 2) if len(prefix) > 3 else 0
    except ValueError:
        pass
    return 0


def load_otb_plus(filepath: str, store: ArrayStore | None = None) -> SignalImport:
    """Load an OTB+ archive as a ``SignalImport``, written into ``store`` block by block."""
    store = store if store is not None else RamStore()
    with tempfile.TemporaryDirectory() as tmpdir:
        _extract(filepath, tmpdir, "OTB+")
        signals = sorted(f for f in os.listdir(tmpdir) if f.endswith(".sig"))
        if not signals:
            raise SignalLoadError("No .sig file found in OTB+ archive.")
        sig_file = signals[0]
        if len(signals) > 1:
            logger.warning(
                "%s holds %d .sig files; loading %s, ignoring %s",
                filepath,
                len(signals),
                sig_file,
                ", ".join(signals[1:]),
            )
        xml_filename = sig_file.removesuffix(".sig") + ".xml"
        xml_path = os.path.join(tmpdir, xml_filename)
        if not os.path.exists(xml_path):
            raise SignalLoadError(f"Could not find XML file: {xml_filename}")
        with open(xml_path, "rb") as f:
            device = xmltodict.parse(f.read()).get("Device")

        device_name = device.get("@Name", "Unknown")
        fsamp = float(device.get("@SampleFrequency", 2048))
        ad_bits = int(device.get("@ad_bits", 12))
        adapters = _ensure_list(device["Channels"]["Adapter"])
        n_channels = sum(len(_ensure_list(adapter["Channel"])) for adapter in adapters)
        dtype = np.int16 if ad_bits == 16 else np.int32
        try:
            frames = _sample_frames(os.path.join(tmpdir, sig_file), np.dtype(dtype), n_channels)
        except OSError as exc:
            raise SignalLoadError(f"cannot reshape {sig_file} into {n_channels} channels") from exc
        n_samples = frames.shape[0]

        # One entry per grid channel, then per auxiliary channel, in file order.
        emg_rows: list[int] = []
        emg_scale: list[float] = []
        gains: list[float] = []
        emg_hpf: list[float] = []
        emg_lpf: list[float] = []
        aux_rows: list[int] = []
        aux_scale: list[float] = []
        aux_names: list[str] = []
        aux_gains: list[float | str] = []
        aux_hpf: list[float | str] = []
        aux_lpf: list[float | str] = []
        hardware_filters: dict[str, None] = {}
        grids: dict[int, tuple[str, str]] = {}  # grid position -> (grid name, muscle name)
        row = 0
        for adapter in adapters:
            adapter_id = adapter.get("@ID", "")
            if adapter_id in _CONTROL_ADAPTERS:
                continue
            hpf = _parse_filter_string(adapter.get("@HighPassFilter", "0"), fsamp)
            lpf = _parse_filter_string(adapter.get("@LowPassFilter", "0"), fsamp)
            hpf = 0.0 if hpf == "n/a" else float(hpf)
            lpf = 0.0 if lpf == "n/a" else float(lpf)
            hardware_filter = (
                f"HP: {adapter.get('@HighPassFilter', '')}, LP: {adapter.get('@LowPassFilter', '')}"
            )
            adapter_gain = float(adapter.get("@Gain", "1"))
            for ch in _ensure_list(adapter["Channel"]):
                gain = float(ch.get("@Gain", "1")) * adapter_gain or 1.0
                scale = _otb_plus_scale(device_name, adapter_id, gain, ad_bits)
                kind = _channel_kind(ch.get("@Description", ""))
                if kind == "emg":
                    emg_rows.append(row)
                    emg_scale.append(scale)
                    gains.append(gain)
                    emg_hpf.append(hpf)
                    emg_lpf.append(lpf)
                    hardware_filters[hardware_filter.strip(", ").strip()] = None
                    position = _grid_position(
                        device_name, adapter.get("@AdapterIndex", "0"), ch.get("@Prefix", "")
                    )
                    muscle_name = f"{ch.get('@Side', '')} {ch.get('@Muscle', '')}".strip()
                    grids.setdefault(position, (ch.get("@ID", ""), muscle_name))
                elif kind == "aux":
                    aux_rows.append(row)
                    aux_scale.append(scale)
                    aux_names.append(ch.get("@ID", ""))
                    aux_gains.append(gain)
                    aux_hpf.append(hpf)
                    aux_lpf.append(lpf)
                row += 1

        data = store.allocate("emg", (len(emg_rows), n_samples), np.float32)
        _write_rows(data, frames, np.array(emg_rows, dtype=int), np.array(emg_scale))
        data = store.seal(data)

        sips: list[np.ndarray] = []
        sip_files = sorted(f for f in os.listdir(tmpdir) if f.endswith(".sip"))
        if len(sip_files) >= 2:
            for sip in sip_files:
                fitted = _read_sip(os.path.join(tmpdir, sip), n_samples)
                if fitted is not None:
                    sips.append(fitted)
                    aux_names.append(sip.removesuffix(".sip"))
                    aux_gains.append(1.0)
                    aux_hpf.append("n/a")
                    aux_lpf.append("n/a")
        aux_data = store.allocate(
            "aux", (len(aux_rows) + len(sips), n_samples), np.float32, zero=True
        )
        _write_rows(
            aux_data[: len(aux_rows)], frames, np.array(aux_rows, dtype=int), np.array(aux_scale)
        )
        for i, values in enumerate(sips):
            aux_data[len(aux_rows) + i] = values
        aux_data = store.seal(aux_data)
        del frames

    metadata: dict[str, Any] = {
        "acquisition_date": _iso_date(device.get("@Date")) or _sig_date(sig_file),
        "manufacturer": "OT Bioelettronica",
        "device_name": device_name,
        "software_versions": "OTBioLab+",
        "hardware_filters": list(hardware_filters) or ["n/a"],
        "powerline_freq": None,
        "units": "mV",
        "gains": gains,
        "emg_hpf": emg_hpf,
        "emg_lpf": emg_lpf,
        "aux_gains": aux_gains,
        "aux_hpf": aux_hpf,
        "aux_lpf": aux_lpf,
        "recording_type": "continuous",
        "software_filters": "n/a",
    }

    return SignalImport.build(
        data=data,
        fsamp=fsamp,
        grid_names=[grids[position][0] for position in sorted(grids)],
        muscle_names=[grids[position][1] for position in sorted(grids)],
        aux_data=aux_data,
        aux_names=aux_names,
        metadata=metadata,
    )


@dataclass
class _Track:
    """One OTB4 track: its channels in a ``.sig`` map, and what the BIDS export lists for them."""

    name: str
    is_emg: bool
    segment: _Segment
    fsamp: float
    gain: float
    hpf: float | str
    lpf: float | str


def _description_name(block: dict[str, Any]) -> str:
    """The ``Name`` of a track's ``Description``, ``""`` when it has none."""
    desc = block.get("Description")
    return str(desc.get("Name") or desc.get("@Name") or "") if isinstance(desc, dict) else ""


def _track(block: dict[str, Any], name: str, is_emg: bool, sig: _SigFile, first_row: int) -> _Track:
    """The track ``block`` describes, its channels from ``first_row`` on in ``sig``."""
    gain = _safe_gain(block["Gain"])
    fsamp = int(block["SamplingFrequency"])
    strings = block.get("StringsDescriptions") or {}
    return _Track(
        name=name,
        is_emg=is_emg,
        segment=_Segment(
            sig=sig,
            rows=np.arange(first_row, first_row + int(block["NumberOfChannels"])),
            scale=float(block["ADC_Range"]) / (2 ** int(block["ADC_Nbits"])) * 1000.0 / gain,
        ),
        fsamp=fsamp,
        gain=gain,
        hpf=_parse_filter_string(strings.get("HighPassFilter", "n/a"), fsamp),
        lpf=_parse_filter_string(strings.get("LowPassFilter", "n/a"), fsamp),
    )


def _novecento_tracks(tmpdir: str, track_list: list[dict[str, Any]]) -> list[_Track]:
    """Novecento+ tracks: int32 signal files, each shared by the tracks of one stream."""
    tracks: list[_Track] = []
    for sig_name, blocks in _group_tracks(track_list).items():
        sig_path = _find_file(tmpdir, os.path.basename(sig_name)) or _find_file(tmpdir, sig_name)
        if not sig_path:
            raise SignalLoadError(
                f"OTB4 archive is missing {os.path.basename(sig_name)}, the signal file "
                f"of {len(blocks)} track(s)"
            )
        sig = _SigFile.open(sig_path, np.dtype(np.int32), int(blocks[0]["ChannelsInBlock"]))
        for block in blocks:
            title = block.get("Title") or f"block_{block['AcquisitionChannel']}"
            desc_name = _description_name(block)
            if title.upper().startswith("IN"):
                tracks.append(_track(block, title, True, sig, int(block["AcquisitionChannel"])))
            elif desc_name.upper().startswith("AUX"):
                tracks.append(
                    _track(block, desc_name, False, sig, int(block["AcquisitionChannel"]))
                )
    return tracks


def _generic_tracks(tmpdir: str, track_list: list[dict[str, Any]]) -> list[_Track]:
    """Tracks of the other devices: int16 channels of one signal file, in track order."""
    sig_paths = sorted(
        os.path.join(root, f)
        for root, _, files in os.walk(tmpdir)
        for f in files
        if f.endswith(".sig")
    )
    if not sig_paths:
        raise SignalLoadError("No .sig files found in OTB4 archive.")
    # Feedback tracks bring their own .sig: pick the one Tracks_000.xml names.
    stream = os.path.basename(track_list[0].get("SignalStreamPath") or "")
    sig_path = (_find_file(tmpdir, stream) if stream else None) or sig_paths[0]
    n_channels = sum(int(block["NumberOfChannels"]) for block in track_list)
    try:
        sig = _SigFile.open(sig_path, np.dtype(np.int16), n_channels)
    except OSError as exc:
        raise SignalLoadError("Cannot reshape .sig into channels x samples") from exc

    tracks: list[_Track] = []
    first_row = 0
    for block in track_list:
        title = block.get("Title") or f"block_{block['AcquisitionChannel']}"
        grid_name = _description_name(block) or title
        if title.upper().startswith("IN") or grid_name.upper().startswith(("GR", "HD")):
            tracks.append(_track(block, grid_name, True, sig, first_row))
        elif not any(adapter in title for adapter in _CONTROL_ADAPTERS):
            aux_track = _track(block, title, False, sig, first_row)
            tracks.append(replace(aux_track, hpf="n/a", lpf="n/a"))
        first_row += int(block["NumberOfChannels"])
    return tracks


def load_otb4(filepath: str, store: ArrayStore | None = None) -> SignalImport:
    """Load an OTB4 archive as a ``SignalImport``, written into ``store`` block by block."""
    store = store if store is not None else RamStore()
    with tempfile.TemporaryDirectory() as tmpdir:
        _extract(filepath, tmpdir, "OTB4")
        xml_files = [
            os.path.join(root, f)
            for root, _, files in os.walk(tmpdir)
            for f in files
            if f == "Tracks_000.xml"
        ]
        if not xml_files:
            raise SignalLoadError("No Tracks_000.xml found in OTB4 archive.")
        with open(xml_files[0], "rb") as f:
            track_list = _ensure_list(xmltodict.parse(f.read())["ArrayOfTrackInfo"]["TrackInfo"])
        device_field = next((t.get("Device") for t in track_list if "Device" in t), "Unknown")
        device_name = device_field.split(";")[0]

        novecento = device_name == "Novecento+"
        tracks = (_novecento_tracks if novecento else _generic_tracks)(tmpdir, track_list)
        emg_tracks = [track for track in tracks if track.is_emg]
        aux_tracks = [track for track in tracks if not track.is_emg]
        if not emg_tracks:
            raise SignalLoadError("OTB4 archive holds no EMG track (titled IN…, GR… or HD…).")
        fsamp = emg_tracks[0].fsamp
        n_samples = min(track.segment.sig.n_frames for track in emg_tracks)
        aux_samples = min((track.segment.sig.n_frames for track in aux_tracks), default=0)
        # Each device path has always scaled in its own precision.
        dtype = np.float32 if novecento else np.float64
        traces = _read_feedback_traces(tmpdir)
        data = _write_segments(
            store, "emg", [t.segment for t in emg_tracks], n_samples, n_samples, dtype
        )
        aux_data = _write_segments(
            store,
            "aux",
            [t.segment for t in aux_tracks],
            n_samples,
            aux_samples,
            dtype,
            traces,
            fsamp,
        )

    hardware_filters: dict[str, None] = {}
    for track in track_list:
        strings = track.get("StringsDescriptions", {})
        if isinstance(strings, dict):
            hardware_filter = (
                f"HP: {strings.get('HighPassFilter', '')}, LP: {strings.get('LowPassFilter', '')}"
            )
            hardware_filters[hardware_filter.strip(", ").strip()] = None
    strings = track_list[0].get("StringsDescriptions")
    sensor = strings.get("OriginalSensor") if isinstance(strings, dict) else None
    grid_names = [
        sensor if sensor and track.name.startswith(("IN", "Channel")) else track.name
        for track in emg_tracks
    ]

    n_traces = len(traces)
    metadata: dict[str, Any] = {
        "acquisition_date": next(
            (
                date
                for t in track_list
                if isinstance(t.get("StringsDescriptions"), dict)
                and (date := _iso_date(t["StringsDescriptions"].get("StartDate")))
            ),
            None,
        ),
        "manufacturer": "OT Bioelettronica",
        "device_name": device_name,
        "software_versions": "OTBIOLAB26",
        "hardware_filters": [f for f in hardware_filters if f] or ["n/a"],
        "powerline_freq": None,
        "units": "mV",
        "gains": [t.gain for t in emg_tracks for _ in t.segment.rows],
        "emg_hpf": [t.hpf for t in emg_tracks for _ in t.segment.rows],
        "emg_lpf": [t.lpf for t in emg_tracks for _ in t.segment.rows],
        "aux_gains": [t.gain for t in aux_tracks for _ in t.segment.rows] + [1.0] * n_traces,
        "aux_hpf": [t.hpf for t in aux_tracks for _ in t.segment.rows] + ["n/a"] * n_traces,
        "aux_lpf": [t.lpf for t in aux_tracks for _ in t.segment.rows] + ["n/a"] * n_traces,
        "recording_type": "continuous",
        "software_filters": "n/a",
    }

    return SignalImport.build(
        data=data,
        fsamp=float(fsamp),
        grid_names=grid_names,
        aux_data=aux_data,
        aux_names=[t.name for t in aux_tracks] + [trace.name for trace in traces],
        metadata=metadata,
    )
