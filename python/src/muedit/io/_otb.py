"""OT Bioelettronica OTB+ and OTB4 file loaders for MUedit."""

from __future__ import annotations

import logging
import os
import re
import shutil
import tarfile
import tempfile
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

import numpy as np
import xmltodict

from muedit.io.store import ArrayStore, RamStore, sample_blocks
from muedit.models import SignalImport
from muedit.signal.grid import format_hdemg_signal

logger = logging.getLogger(__name__)


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


def _sample_frames(file_path: str, dtype: np.dtype, n_channels: int) -> np.ndarray:
    """A ``.sig`` file as a read-only ``(samples, channels)`` map: channels are interleaved."""
    n_values = os.path.getsize(file_path) // dtype.itemsize
    if n_values % n_channels != 0:
        raise OSError(f"Cannot reshape {os.path.basename(file_path)} into {n_channels} channels")
    if n_values == 0:
        return np.zeros((0, n_channels), dtype=dtype)
    return np.memmap(file_path, dtype=dtype, mode="r", shape=(n_values // n_channels, n_channels))


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


@dataclass
class _OTB4Channels:
    grid_data: np.ndarray
    grid_names: list
    auxiliary: np.ndarray
    auxiliary_names: list
    fs_out: int | float
    emg_gains: list
    emg_hpf: list
    emg_lpf: list
    aux_gains: list
    aux_hpf: list
    aux_lpf: list


@dataclass
class _Segment:
    """One track's channels: their columns in a ``.sig`` map and their scale factor."""

    frames: np.ndarray  # (samples, channels) map of the track's .sig file
    rows: np.ndarray
    scale: float


@dataclass
class _Trace:
    """One feedback channel (target or performed path) at its own sampling rate."""

    name: str
    values: np.ndarray
    fs: float


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
                    fs=float(track["SamplingFrequency"]),
                )
                for i in range(n_ch)
            )
    return traces


def _fit_trace(trace: _Trace, fs_out: float, n_samples: int) -> np.ndarray:
    """``trace`` on the EMG clock: linear between its samples, its last value held past its end."""
    t_in = np.arange(trace.values.size) / trace.fs
    t_out = np.arange(n_samples) / fs_out
    return np.interp(t_out, t_in, trace.values)


def _write_segments(
    store: ArrayStore,
    name: str,
    segments: list[_Segment],
    n_samples: int,
    n_copy: int,
    dtype: type[np.floating[Any]],
    traces: list[_Trace] | None = None,
    fs_out: float = 0.0,
) -> np.ndarray:
    """Stack the segments' channels (``n_copy`` samples, zeros after), then ``traces``, as float32."""
    traces = traces or []
    n_rows = sum(len(seg.rows) for seg in segments)
    out = store.allocate(name, (n_rows + len(traces), n_samples), np.float32, zero=True)
    width = min(n_copy, n_samples)
    row = 0
    for seg in segments:
        scale = np.full(len(seg.rows), seg.scale, dtype=dtype)
        _write_rows(out[row : row + len(seg.rows), :width], seg.frames, seg.rows, scale)
        row += len(seg.rows)
    for i, trace in enumerate(traces):
        out[n_rows + i] = _fit_trace(trace, fs_out, n_samples)
    return store.seal(out)


def _parse_otb4_novecento(
    tmpdir: str, track_list: list[dict[str, Any]], store: ArrayStore, traces: list[_Trace]
) -> _OTB4Channels:
    """Parse channel data for the Novecento+ device (grouped int32 signal files)."""
    grouped = _group_tracks(track_list)
    emg_blocks: list[tuple[str, _Segment, int]] = []
    aux_blocks: list[tuple[str, _Segment, int]] = []
    emg_gains: list = []
    emg_hpf: list = []
    emg_lpf: list = []
    aux_gains: list = []
    aux_hpf: list = []
    aux_lpf: list = []

    for sig_path_raw, blocks in grouped.items():
        sig_path = _find_file(tmpdir, os.path.basename(sig_path_raw)) or _find_file(
            tmpdir, sig_path_raw
        )
        if not sig_path:
            continue
        frames = _sample_frames(sig_path, np.dtype(np.int32), int(blocks[0]["ChannelsInBlock"]))
        for block in blocks:
            acq_ch = int(block["AcquisitionChannel"])
            n_ch_block = int(block["NumberOfChannels"])
            gain = _safe_gain(block["Gain"])
            segment = _Segment(
                frames=frames,
                rows=np.arange(acq_ch, acq_ch + n_ch_block),
                scale=float(block["ADC_Range"]) / (2 ** int(block["ADC_Nbits"])) * 1000.0 / gain,
            )
            title = block.get("Title") or f"block_{block['AcquisitionChannel']}"
            block_gain = _safe_gain(block.get("Gain", 1))
            strings_desc = block.get("StringsDescriptions") or {}
            block_fsamp = int(block["SamplingFrequency"])
            hpf_val = _parse_filter_string(strings_desc.get("HighPassFilter", "n/a"), block_fsamp)
            lpf_val = _parse_filter_string(strings_desc.get("LowPassFilter", "n/a"), block_fsamp)
            desc = block.get("Description") or {}
            desc_name = ""
            if isinstance(desc, dict):
                desc_name = desc.get("Name") or desc.get("@Name") or ""
            if title.upper().startswith("IN"):
                emg_blocks.append((title, segment, block_fsamp))
                emg_gains.extend([block_gain] * n_ch_block)
                emg_hpf.extend([hpf_val] * n_ch_block)
                emg_lpf.extend([lpf_val] * n_ch_block)
            elif desc_name.upper().startswith("AUX"):
                aux_blocks.append((desc_name, segment, block_fsamp))
                aux_gains.extend([block_gain] * n_ch_block)
                aux_hpf.extend([hpf_val] * n_ch_block)
                aux_lpf.extend([lpf_val] * n_ch_block)

    grid_segments = [seg for _, seg, _ in emg_blocks]
    auxiliary_segments = [seg for _, seg, _ in aux_blocks]
    fs_out = emg_blocks[0][2] if emg_blocks else (aux_blocks[0][2] if aux_blocks else 0)
    grid_len = min((seg.frames.shape[0] for seg in grid_segments), default=None)
    aux_len = min((seg.frames.shape[0] for seg in auxiliary_segments), default=0)
    n_samples = grid_len if grid_len is not None else aux_len
    # The Novecento loader always scaled in float32.
    grid_data = _write_segments(store, "emg", grid_segments, n_samples, n_samples, np.float32)
    auxiliary = _write_segments(
        store, "aux", auxiliary_segments, n_samples, aux_len, np.float32, traces, fs_out
    )

    return _OTB4Channels(
        grid_data=grid_data,
        grid_names=[name for name, _, _ in emg_blocks],
        auxiliary=auxiliary,
        auxiliary_names=[name for name, _, _ in aux_blocks],
        fs_out=fs_out,
        emg_gains=emg_gains,
        emg_hpf=emg_hpf,
        emg_lpf=emg_lpf,
        aux_gains=aux_gains,
        aux_hpf=aux_hpf,
        aux_lpf=aux_lpf,
    )


def _parse_otb4_generic(
    tmpdir: str, track_list: list[dict[str, Any]], store: ArrayStore, traces: list[_Trace]
) -> _OTB4Channels:
    """Parse channel data for generic OTB4 devices (flat int16 signal file)."""
    sig_paths = sorted(
        [
            os.path.join(root, f)
            for root, _, files in os.walk(tmpdir)
            for f in files
            if f.endswith(".sig")
        ]
    )
    if not sig_paths:
        raise FileNotFoundError("No .sig files found in OTB4 archive.")
    # Feedback tracks bring their own .sig: pick the one Tracks_000.xml names.
    stream = os.path.basename(track_list[0].get("SignalStreamPath") or "")
    sig_path = (_find_file(tmpdir, stream) if stream else None) or sig_paths[0]

    total_channels = sum(int(t["NumberOfChannels"]) for t in track_list)
    try:
        frames = _sample_frames(sig_path, np.dtype(np.int16), total_channels)
    except OSError as exc:
        raise ValueError("Cannot reshape .sig into channels x samples") from exc

    emg_blocks: list[tuple[str, _Segment, int]] = []
    aux_blocks: list[tuple[str, _Segment, int, float]] = []
    all_segments: list[_Segment] = []
    emg_gains: list = []
    emg_hpf: list = []
    emg_lpf: list = []

    offset = 0
    for block in track_list:
        n_block = int(block["NumberOfChannels"])
        gain = _safe_gain(block["Gain"])
        ad_bits = int(block["ADC_Nbits"])
        psup = float(block["ADC_Range"])
        segment = _Segment(
            frames=frames,
            rows=np.arange(offset, offset + n_block),
            scale=psup / (2**ad_bits) * 1000.0 / gain,
        )
        all_segments.append(segment)
        title = block.get("Title") or f"block_{block['AcquisitionChannel']}"
        grid_name = title
        desc = block.get("Description")
        if isinstance(desc, dict):
            desc_name_local: Any = desc.get("Name") or desc.get("@Name")
            if desc_name_local:
                grid_name = str(desc_name_local)
        strings_desc = block.get("StringsDescriptions") or {}
        block_fsamp = int(block["SamplingFrequency"])
        hpf_val = _parse_filter_string(strings_desc.get("HighPassFilter", "n/a"), block_fsamp)
        lpf_val = _parse_filter_string(strings_desc.get("LowPassFilter", "n/a"), block_fsamp)
        if title.upper().startswith("IN") or grid_name.upper().startswith(("GR", "HD")):
            emg_blocks.append((grid_name, segment, block_fsamp))
            emg_gains.extend([gain] * n_block)
            emg_hpf.extend([hpf_val] * n_block)
            emg_lpf.extend([lpf_val] * n_block)
        else:
            aux_blocks.append((title, segment, block_fsamp, gain))
        offset += n_block

    grid_segments = [seg for _, seg, _ in emg_blocks] or all_segments
    filtered_aux = [
        (name, seg, gain)
        for name, seg, _, gain in aux_blocks
        if "AdapterControl" not in name and "AdapterQuaternions" not in name
    ]
    aux_gains: list = []
    aux_hpf: list = []
    aux_lpf: list = []
    for _, seg, gain in filtered_aux:
        n_ch_block = len(seg.rows)
        aux_gains.extend([gain] * n_ch_block)
        aux_hpf.extend(["n/a"] * n_ch_block)
        aux_lpf.extend(["n/a"] * n_ch_block)

    fs_out = (
        emg_blocks[0][2]
        if emg_blocks
        else (aux_blocks[0][2] if aux_blocks else int(track_list[0]["SamplingFrequency"]))
    )
    # Every track lives in the same file, so they all have its length.
    n_samples = frames.shape[0]
    grid_data = _write_segments(store, "emg", grid_segments, n_samples, n_samples, np.float64)
    auxiliary = _write_segments(
        store,
        "aux",
        [seg for _, seg, _ in filtered_aux],
        n_samples,
        n_samples,
        np.float64,
        traces,
        fs_out,
    )

    return _OTB4Channels(
        grid_data=grid_data,
        grid_names=[name for name, _, _ in emg_blocks],
        auxiliary=auxiliary,
        auxiliary_names=[name for name, _, _ in filtered_aux],
        fs_out=fs_out,
        emg_gains=emg_gains,
        emg_hpf=emg_hpf,
        emg_lpf=emg_lpf,
        aux_gains=aux_gains,
        aux_hpf=aux_hpf,
        aux_lpf=aux_lpf,
    )


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


def load_otb_plus(filepath: str, store: ArrayStore | None = None) -> SignalImport:
    """Load OTB+ archive (.otb+/.zip) and normalize channels/metadata, written into ``store``."""
    store = store if store is not None else RamStore()
    with tempfile.TemporaryDirectory() as tmpdir:
        if filepath.endswith(".zip"):
            shutil.unpack_archive(filepath, tmpdir)
        else:
            try:
                with tarfile.open(filepath, "r") as tar:
                    tar.extractall(path=tmpdir, filter="data")
            except (tarfile.TarError, OSError) as exc:
                raise OSError(f"Failed to extract OTB+ file: {exc}") from exc

        signals = [f for f in os.listdir(tmpdir) if f.endswith(".sig")]
        if not signals:
            raise FileNotFoundError("No .sig file found in OTB+ archive.")

        sig_file = signals[0]
        xml_filename = sig_file.replace(".sig", ".xml")
        xml_path = os.path.join(tmpdir, xml_filename)

        if not os.path.exists(xml_path):
            xmls = [f for f in os.listdir(tmpdir) if f.endswith(".xml")]
            if xmls:
                xml_path = os.path.join(tmpdir, xmls[0])
            else:
                raise FileNotFoundError(f"Could not find XML file: {xml_filename}")

        with open(xml_path, "rb") as f:
            parsed_xml = xmltodict.parse(f.read())

        device_node = parsed_xml.get("Device")
        device_name = device_node.get("@Name", "Unknown")
        sample_freq = float(device_node.get("@SampleFrequency", 2048))
        ad_bits = int(device_node.get("@ad_bits", 12))

        adapters = device_node["Channels"]["Adapter"]
        if not isinstance(adapters, list):
            adapters = [adapters]

        n_channels = 0
        adapter_filters = {}

        for adapter in adapters:
            start_index = int(adapter.get("@ChannelStartIndex", 0))
            hpf = adapter.get("@HighPassFilter", "")
            lpf = adapter.get("@LowPassFilter", "")
            filt_str = f"HP: {hpf}, LP: {lpf}".strip(", ").strip()
            channels = adapter["Channel"]
            if not isinstance(channels, list):
                channels = [channels]
            for ch in channels:
                idx = int(ch.get("@Index", 0))
                pos = start_index + idx
                if filt_str:
                    adapter_filters[pos] = filt_str
                n_channels += 1

        sig_path = os.path.join(tmpdir, sig_file)
        dtype = np.int16 if ad_bits == 16 else np.int32
        try:
            frames = _sample_frames(sig_path, np.dtype(dtype), n_channels)
        except OSError as exc:
            raise ValueError(f"cannot reshape {sig_file} into {n_channels} channels") from exc
        n_samples = frames.shape[0]

        channel_cursor = 0
        total_channels = n_channels
        gain_array = np.zeros(total_channels)
        scale_array = np.ones(total_channels)
        high_pass_array = np.zeros(total_channels)
        low_pass_array = np.zeros(total_channels)

        grid_names = []
        muscles = []
        adapter_types = []
        grid_ids = []

        for adapter in adapters:
            adapter_id = adapter.get("@ID", "")

            if adapter_id == "AdapterControl" or adapter_id == "AdapterQuaternions":
                continue

            adapter_index = adapter.get("@AdapterIndex", "0")
            hpf = _parse_filter_string(adapter.get("@HighPassFilter", "0"), sample_freq)
            lpf = _parse_filter_string(adapter.get("@LowPassFilter", "0"), sample_freq)
            hpf = 0.0 if hpf == "n/a" else float(hpf)
            lpf = 0.0 if lpf == "n/a" else float(lpf)
            adapter_gain = float(adapter.get("@Gain", "1"))

            channels = adapter["Channel"]
            if not isinstance(channels, list):
                channels = [channels]

            for ch in channels:
                grid_names.append(ch.get("@ID", ""))

                muscle_name = ch.get("@Muscle", "")
                side_name = ch.get("@Side", "")
                muscle_str = (
                    f"{side_name} {muscle_name}".strip() if side_name or muscle_name else ""
                )
                muscles.append(muscle_str)

                description = ch.get("@Description", "")
                if "General" in description or "iEMG" in description:
                    adapter_types.append(1)
                elif "16" in description:
                    adapter_types.append(2)
                elif "32" in description:
                    adapter_types.append(3)
                elif "64" in description or "Splitter" in description:
                    adapter_types.append(4)
                else:
                    adapter_types.append(5)

                grid_position = 0
                if "QUATTROCENTO" in device_name:
                    prefix = ch.get("@Prefix", "")
                    if "MULTIPLE IN" in prefix:
                        try:
                            if len(prefix) > 12:
                                grid_position = int(prefix[12]) + 2
                        except (TypeError, ValueError, IndexError):
                            grid_position = 0
                    elif "IN" in prefix:
                        try:
                            if len(prefix) > 3:
                                val = int(prefix[3])
                                grid_position = 1 if val < 5 else 2
                        except (TypeError, ValueError, IndexError):
                            grid_position = 0
                else:
                    try:
                        grid_position = int(adapter_index)
                    except (TypeError, ValueError):
                        grid_position = 0

                grid_ids.append(grid_position)

                ch_gain = float(ch.get("@Gain", "1"))
                gain_array[channel_cursor] = ch_gain * adapter_gain
                if gain_array[channel_cursor] == 0:
                    gain_array[channel_cursor] = 1.0

                scale_array[channel_cursor] = _otb_plus_scale(
                    device_name, adapter_id, float(gain_array[channel_cursor]), ad_bits
                )

                high_pass_array[channel_cursor] = hpf
                low_pass_array[channel_cursor] = lpf
                channel_cursor += 1

        gain_array = gain_array[:channel_cursor]
        high_pass_array = high_pass_array[:channel_cursor]
        low_pass_array = low_pass_array[:channel_cursor]

        adapter_types_arr = np.array(adapter_types, dtype=int)
        grid_ids_arr = np.array(grid_ids, dtype=int)

        grid_mask = (adapter_types_arr == 3) | (adapter_types_arr == 4)
        grid_rows = np.flatnonzero(grid_mask)
        signal_data = store.allocate("emg", (grid_rows.size, n_samples), np.float32)
        _write_rows(signal_data, frames, grid_rows, scale_array[grid_rows])
        signal_data = store.seal(signal_data)

        grid_names_masked = [grid_names[i] for i in range(len(grid_names)) if grid_mask[i]]
        muscles_masked = [muscles[i] for i in range(len(muscles)) if grid_mask[i]]
        grid_ids_masked = grid_ids_arr[grid_mask]

        unique_grids = []
        unique_muscles = []

        if len(grid_ids_masked) > 0:
            unique_ids = np.unique(grid_ids_masked)
            for uid in unique_ids:
                indices = np.where(grid_ids_masked == uid)[0]
                if len(indices) > 0:
                    first_idx = indices[0]
                    unique_grids.append(grid_names_masked[first_idx])
                    unique_muscles.append(muscles_masked[first_idx])

        aux_mask = adapter_types_arr == 5
        aux_rows = np.flatnonzero(aux_mask)
        aux_names = [grid_names[i] for i in range(len(grid_names)) if aux_mask[i]]

        sips: list[np.ndarray] = []
        sip_files = sorted([f for f in os.listdir(tmpdir) if f.endswith(".sip")])
        if len(sip_files) >= 2:
            for sip in sip_files:
                fitted = _read_sip(os.path.join(tmpdir, sip), n_samples)
                if fitted is not None:
                    sips.append(fitted)
                    aux_names.append(sip.replace(".sip", ""))

        auxiliary = store.allocate(
            "aux", (aux_rows.size + len(sips), n_samples), np.float32, zero=True
        )
        _write_rows(auxiliary[: aux_rows.size], frames, aux_rows, scale_array[aux_rows])
        for i, sip_data in enumerate(sips):
            auxiliary[aux_rows.size + i] = sip_data
        auxiliary = store.seal(auxiliary)

        device_meta = parsed_xml.get("Device")
        date_node = device_meta.get("@Date", "") if isinstance(device_meta, dict) else None

        coordinates, ieds, discard_vecs, emg_types = format_hdemg_signal(unique_grids)

        valid_hardware_filters = set()
        if adapter_filters:
            for ch_idx, f_str in adapter_filters.items():
                if ch_idx < len(grid_mask) and grid_mask[ch_idx]:
                    valid_hardware_filters.add(f_str)

        emg_gains = (
            gain_array[grid_mask]
            if len(gain_array) == len(grid_mask)
            else np.array([], dtype=float)
        )
        emg_hpf = (
            high_pass_array[grid_mask]
            if len(high_pass_array) == len(grid_mask)
            else np.array([], dtype=float)
        )
        emg_lpf = (
            low_pass_array[grid_mask]
            if len(low_pass_array) == len(grid_mask)
            else np.array([], dtype=float)
        )

        aux_gains = (
            gain_array[aux_mask] if len(gain_array) == len(aux_mask) else np.array([], dtype=float)
        )
        aux_hpf = (
            high_pass_array[aux_mask]
            if len(high_pass_array) == len(aux_mask)
            else np.array([], dtype=float)
        )
        aux_lpf = (
            low_pass_array[aux_mask]
            if len(low_pass_array) == len(aux_mask)
            else np.array([], dtype=float)
        )

        raw_aux_names = [grid_names[i] for i in range(len(grid_names)) if aux_mask[i]]
        allowed_indices = [
            i
            for i, name in enumerate(raw_aux_names)
            if "AdapterControl" not in name and "AdapterQuaternions" not in name
        ]
        if allowed_indices:
            allowed_indices_arr = np.array(allowed_indices, dtype=int)
            aux_gains = aux_gains[allowed_indices_arr]
            aux_hpf = aux_hpf[allowed_indices_arr]
            aux_lpf = aux_lpf[allowed_indices_arr]
        else:
            aux_gains = np.array([])
            aux_hpf = np.array([])
            aux_lpf = np.array([])

        metadata = {
            "acquisition_date": date_node,
            "manufacturer": "OT Bioelettronica",
            "device_name": device_name,
            "software_versions": "OTBioLab+",
            "ad_bits": ad_bits,
            "coordinates": coordinates,
            "ieds": ieds,
            "discard_channels": discard_vecs,
            "emg_types": emg_types,
            "hardware_filters": (
                list(valid_hardware_filters) if valid_hardware_filters else ["n/a"]
            ),
            "channel_map_filters": adapter_filters,
            "gains": (emg_gains.tolist() if isinstance(emg_gains, np.ndarray) else emg_gains),
            "emg_hpf": emg_hpf.tolist() if isinstance(emg_hpf, np.ndarray) else emg_hpf,
            "emg_lpf": emg_lpf.tolist() if isinstance(emg_lpf, np.ndarray) else emg_lpf,
            "aux_gains": (aux_gains.tolist() if isinstance(aux_gains, np.ndarray) else aux_gains),
            "aux_hpf": aux_hpf.tolist() if isinstance(aux_hpf, np.ndarray) else aux_hpf,
            "aux_lpf": aux_lpf.tolist() if isinstance(aux_lpf, np.ndarray) else aux_lpf,
            "units": "uV",
            "recording_type": "continuous",
            "software_filters": "n/a",
        }

        return SignalImport.build(
            data=signal_data,
            fsamp=sample_freq,
            gridname=unique_grids,
            muscle=unique_muscles,
            auxiliary=auxiliary,
            auxiliaryname=aux_names,
            metadata=metadata,
        )


def load_otb4(filepath: str, store: ArrayStore | None = None) -> SignalImport:
    """Load OTB4 archives and normalize EMG/aux channels into MUedit format, written into ``store``."""
    store = store if store is not None else RamStore()
    with tempfile.TemporaryDirectory() as tmpdir:
        if zipfile.is_zipfile(filepath):
            shutil.unpack_archive(filepath, tmpdir)
        elif tarfile.is_tarfile(filepath):
            with tarfile.open(filepath, "r") as tar:
                tar.extractall(tmpdir, filter="data")
        else:
            raise OSError("Unsupported OTB4 archive format: expected tar or zip.")

        xml_files = [
            os.path.join(root, f)
            for root, _, files in os.walk(tmpdir)
            for f in files
            if f == "Tracks_000.xml"
        ]
        if not xml_files:
            raise FileNotFoundError("No Tracks_000.xml found in OTB4 archive.")

        with open(xml_files[0], "rb") as fd:
            abs_xml = xmltodict.parse(fd.read())

        track_info = abs_xml["ArrayOfTrackInfo"]["TrackInfo"]
        track_list = _ensure_list(track_info)
        device_field = next((t.get("Device") for t in track_list if "Device" in t), "Unknown")
        device = device_field.split(";")[0]

        traces = _read_feedback_traces(tmpdir)
        ch = (
            _parse_otb4_novecento(tmpdir, track_list, store, traces)
            if device == "Novecento+"
            else _parse_otb4_generic(tmpdir, track_list, store, traces)
        )
        ch.auxiliary_names += [trace.name for trace in traces]
        ch.aux_gains += [1.0] * len(traces)
        ch.aux_hpf += ["n/a"] * len(traces)
        ch.aux_lpf += ["n/a"] * len(traces)

        filters_list = []
        for track in track_list:
            strings_desc = track.get("StringsDescriptions", {})
            if not isinstance(strings_desc, dict):
                continue
            hpf = strings_desc.get("HighPassFilter", "")
            lpf = strings_desc.get("LowPassFilter", "")
            f_str = f"HP: {hpf}, LP: {lpf}".strip(", ").strip()
            if f_str and f_str not in filters_list:
                filters_list.append(f_str)

        refined_grid_names = []
        for grid_name in ch.grid_names:
            if (grid_name.startswith("IN") or grid_name.startswith("Channel")) and track_list:
                strings_desc = track_list[0].get("StringsDescriptions", {})
                if isinstance(strings_desc, dict):
                    sensor = strings_desc.get("OriginalSensor")
                    refined_grid_names.append(sensor if sensor else grid_name)
                else:
                    refined_grid_names.append(grid_name)
            else:
                refined_grid_names.append(grid_name)

        coordinates, ieds, discard_vecs, emg_types = format_hdemg_signal(refined_grid_names)

        metadata: dict[str, Any] = {
            "acquisition_date": None,
            "manufacturer": "OT Bioelettronica",
            "device_name": device,
            "software_versions": "OTBIOLAB26",
            "ad_bits": None,
            "coordinates": coordinates,
            "ieds": ieds,
            "discard_channels": discard_vecs,
            "emg_types": emg_types,
            "hardware_filters": filters_list if filters_list else ["n/a"],
            "channel_map_filters": {},
            "gains": ch.emg_gains,
            "aux_gains": ch.aux_gains,
            "emg_hpf": ch.emg_hpf if ch.emg_hpf else ["n/a"] * len(ch.emg_gains),
            "emg_lpf": ch.emg_lpf if ch.emg_lpf else ["n/a"] * len(ch.emg_gains),
            "aux_hpf": ch.aux_hpf if ch.aux_hpf else ["n/a"] * len(ch.aux_gains),
            "aux_lpf": ch.aux_lpf if ch.aux_lpf else ["n/a"] * len(ch.aux_gains),
            "units": "uV",
            "recording_type": "continuous",
            "software_filters": "n/a",
        }

        return SignalImport.build(
            data=ch.grid_data,
            fsamp=float(ch.fs_out),
            gridname=refined_grid_names,
            auxiliary=ch.auxiliary,
            auxiliaryname=ch.auxiliary_names,
            metadata=metadata,
        )
