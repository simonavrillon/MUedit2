"""OT Bioelettronica OTB+ and OTB4 file loaders for MUedit."""

from __future__ import annotations

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

from muedit.signal.grid import format_hdemg_signal


def _ensure_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else [value]


def _safe_gain(gain_value: Any) -> float:
    try:
        g_val = float(gain_value)
    except (TypeError, ValueError):
        return 1.0
    return g_val if g_val != 0 else 1.0


def _sanitize_array(arr: Any) -> np.ndarray:
    arr64 = np.asarray(arr, dtype=np.float64)
    finfo = np.finfo(np.float64)
    arr64 = np.where(np.isnan(arr64), 0.0, arr64)
    arr64 = np.where(np.isposinf(arr64), finfo.max, arr64)
    arr64 = np.where(np.isneginf(arr64), finfo.min, arr64)
    return arr64


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


def _grid_map_array(description: dict[str, Any]) -> np.ndarray | None:
    if not description or "Map" not in description:
        return None
    map_field = description.get("Map")
    if isinstance(map_field, list):
        map_field = map_field[0]
    if not isinstance(map_field, dict):
        return None
    array_of_int = map_field.get("ArrayOfInt", {})
    raw_map = (
        array_of_int.get("int") if isinstance(array_of_int, dict) else array_of_int
    )
    flat = _ensure_list(raw_map)
    try:
        flat_int = [int(v) for v in flat]
    except (TypeError, ValueError):
        return None
    n_row = int(description.get("NRow", 1))
    n_col = int(description.get("NColumn", len(flat_int)))
    if n_row * n_col != len(flat_int):
        return None
    return np.asarray(flat_int, dtype=np.int32).reshape((n_row, n_col))


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


def _load_signal_file(
    file_path: str, blocks: list[dict[str, Any]], dtype: np.dtype
) -> list[tuple[dict[str, Any], np.ndarray]]:
    n_channels = int(blocks[0]["ChannelsInBlock"])
    with open(file_path, "rb") as handle:
        raw = np.fromfile(handle, dtype=dtype)
    if raw.size % n_channels != 0:
        raise OSError(
            f"Cannot reshape {os.path.basename(file_path)} into {n_channels} channels"
        )
    data = raw.reshape((n_channels, -1), order="F").astype(np.float32)

    block_data: list[tuple[dict[str, Any], np.ndarray]] = []
    for block in blocks:
        acq_ch = int(block["AcquisitionChannel"])
        n_block = int(block["NumberOfChannels"])
        view = data[acq_ch : acq_ch + n_block]
        gain = _safe_gain(block["Gain"])
        ad_bits = int(block["ADC_Nbits"])
        psup = float(block["ADC_Range"])
        view *= psup / (2**ad_bits) * 1000.0 / gain
        block_data.append((block, view.copy()))
    return block_data


def _concat_segments(
    segments: list[np.ndarray], fallback_len: int = 0
) -> np.ndarray:
    if not segments:
        return np.zeros((0, fallback_len))
    min_len = min(seg.shape[1] for seg in segments)
    cropped = [seg[:, :min_len] for seg in segments]
    return np.concatenate(cropped, axis=0)



def _apply_otb_plus_scaling(
    data: np.ndarray,
    ch_idx: int,
    device_name: str,
    adapter_id: str,
    gain_array: np.ndarray,
    ad_bits: int,
) -> None:
    """Apply device-specific ADC→mV scaling in-place for one channel row."""
    if device_name in {"QUATTROCENTO", "QUATTRO"}:
        if adapter_id == "Direct connection":
            data[ch_idx] *= 0.1526
        elif adapter_id == "AdapterControl":
            pass
        else:
            data[ch_idx] *= 0.00050863
    elif device_name in {"DUE+", "QUATTRO+"}:
        if adapter_id in {"AdapterControl", "AdapterQuaternions"}:
            pass
        else:
            data[ch_idx] *= 0.00024928
    elif device_name == "DUE":
        if adapter_id in {"AdapterControl", "AdapterQuaternions"}:
            pass
        else:
            data[ch_idx] *= 0.00025177
    elif device_name in {"SESSANTAQUATTRO", "SESSANTAQUATTRO+"}:
        if adapter_id in {"AdapterControl", "AdapterQuaternions"}:
            pass
        elif adapter_id == "Direct connection to Auxiliary Input":
            data[ch_idx] *= 0.00014648 if ad_bits == 16 else 0.00000057220
        else:
            gain_val = gain_array[ch_idx]
            if ad_bits == 16:
                gain_val = {256: 1, 128: 0.5, 64: 0.75}.get(gain_val, gain_val)
            elif ad_bits == 24:
                gain_val = {1: 1, 0.5: 2, 0.25: 3, 0.125: 4}.get(gain_val, gain_val)
            data[ch_idx] *= 4.8 / (2**24) * 1000 / gain_val
    elif device_name == "SYNCSTATION":
        if adapter_id in {"Due+", "Quattro+"}:
            data[ch_idx] *= 0.00024928
        elif adapter_id == "Direct connection to Syncstation Input":
            data[ch_idx] *= 0.1526
        elif adapter_id == "AdapterLoadCell":
            data[ch_idx] *= 0.00037217
        elif adapter_id in {"AdapterControl", "AdapterQuaternions"}:
            pass
        else:
            data[ch_idx] *= 0.00028610
    else:
        if adapter_id == "Direct connection to Auxiliary Input":
            data[ch_idx] *= 0.00000057220
        elif adapter_id in {"AdapterControl", "AdapterQuaternions"}:
            pass
        else:
            gain_val = gain_array[ch_idx]
            if gain_val == 0:
                gain_val = 1.0
            data[ch_idx] *= 4.8 / (2**24) * 1000 / gain_val



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
    emg_not_grid: np.ndarray


def _parse_otb4_novecento(tmpdir: str, track_list: list[dict[str, Any]]) -> _OTB4Channels:
    """Parse channel data for the Novecento+ device (grouped int32 signal files)."""
    grouped = _group_tracks(track_list)
    emg_blocks: list[tuple[str, dict[str, Any]]] = []
    aux_blocks: list[tuple[str, dict[str, Any]]] = []
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
        for block, blk_data in _load_signal_file(sig_path, blocks, np.dtype(np.int32)):
            title = block.get("Title") or f"block_{block['AcquisitionChannel']}"
            n_ch_block = blk_data.shape[0]
            block_gain = _safe_gain(block.get("Gain", 1))
            strings_desc = block.get("StringsDescriptions") or {}
            block_fsamp = int(block["SamplingFrequency"])
            hpf_val = _parse_filter_string(strings_desc.get("HighPassFilter", "n/a"), block_fsamp)
            lpf_val = _parse_filter_string(strings_desc.get("LowPassFilter", "n/a"), block_fsamp)
            desc = block.get("Description") or {}
            desc_name = ""
            if isinstance(desc, dict):
                desc_name = desc.get("Name") or desc.get("@Name") or ""
            payload = {
                "data": blk_data,
                "fs": block_fsamp,
                "map": _grid_map_array(desc),
                "gain": block_gain,
                "hpf": hpf_val,
                "lpf": lpf_val,
            }
            if title.upper().startswith("IN"):
                emg_blocks.append((title, payload))
                emg_gains.extend([block_gain] * n_ch_block)
                emg_hpf.extend([hpf_val] * n_ch_block)
                emg_lpf.extend([lpf_val] * n_ch_block)
            elif desc_name.upper().startswith("AUX"):
                aux_blocks.append((desc_name, payload))
                aux_gains.extend([block_gain] * n_ch_block)
                aux_hpf.extend([hpf_val] * n_ch_block)
                aux_lpf.extend([lpf_val] * n_ch_block)

    grid_segments = [p["data"] for _, p in emg_blocks]
    auxiliary_segments = [p["data"] for _, p in aux_blocks]
    fs_out = (
        emg_blocks[0][1]["fs"] if emg_blocks else (aux_blocks[0][1]["fs"] if aux_blocks else 0)
    )
    ref_len = min(seg.shape[1] for seg in grid_segments) if grid_segments else None
    if ref_len is None and auxiliary_segments:
        ref_len = min(seg.shape[1] for seg in auxiliary_segments)
    ref_len = ref_len or 0
    grid_data = _concat_segments(grid_segments, fallback_len=ref_len)
    auxiliary = _concat_segments(auxiliary_segments, fallback_len=grid_data.shape[1] or ref_len)

    return _OTB4Channels(
        grid_data=grid_data,
        grid_names=[name for name, _ in emg_blocks],
        auxiliary=auxiliary,
        auxiliary_names=[name for name, _ in aux_blocks],
        fs_out=fs_out,
        emg_gains=emg_gains,
        emg_hpf=emg_hpf,
        emg_lpf=emg_lpf,
        aux_gains=aux_gains,
        aux_hpf=aux_hpf,
        aux_lpf=aux_lpf,
        emg_not_grid=np.zeros((0, grid_data.shape[1] or auxiliary.shape[1])),
    )


def _parse_otb4_generic(tmpdir: str, track_list: list[dict[str, Any]]) -> _OTB4Channels:
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

    total_channels = sum(int(t["NumberOfChannels"]) for t in track_list)
    with open(sig_paths[0], "rb") as fd:
        raw_data = np.fromfile(fd, dtype=np.int16)
    if raw_data.size % total_channels != 0:
        raise ValueError("Cannot reshape .sig into channels x samples")
    data = raw_data.reshape((total_channels, -1), order="F").astype(np.float64)

    emg_blocks: list[tuple[str, dict[str, Any]]] = []
    aux_blocks: list[tuple[str, dict[str, Any]]] = []
    emg_gains: list = []
    emg_hpf: list = []
    emg_lpf: list = []

    offset = 0
    for block in track_list:
        n_block = int(block["NumberOfChannels"])
        view = data[offset : offset + n_block]
        gain = _safe_gain(block["Gain"])
        ad_bits = int(block["ADC_Nbits"])
        psup = float(block["ADC_Range"])
        view *= psup / (2**ad_bits) * 1000.0 / gain
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
        payload = {
            "data": view.copy(),
            "fs": block_fsamp,
            "map": _grid_map_array(block.get("Description") or {}),
            "gain_val": gain,
        }
        if title.upper().startswith("IN") or grid_name.upper().startswith(("GR", "HD")):
            emg_blocks.append((grid_name, payload))
            emg_gains.extend([gain] * n_block)
            emg_hpf.extend([hpf_val] * n_block)
            emg_lpf.extend([lpf_val] * n_block)
        else:
            aux_blocks.append((title, payload))
        offset += n_block

    grid_segments = [p["data"] for _, p in emg_blocks] or [data]
    filtered_aux = [
        (name, p)
        for name, p in aux_blocks
        if "AdapterControl" not in name and "AdapterQuaternions" not in name
    ]
    aux_gains: list = []
    aux_hpf: list = []
    aux_lpf: list = []
    for _, p in filtered_aux:
        n_ch_block = p["data"].shape[0]
        aux_gains.extend([p.get("gain_val", 1.0)] * n_ch_block)
        aux_hpf.extend(["n/a"] * n_ch_block)
        aux_lpf.extend(["n/a"] * n_ch_block)

    auxiliary_segments = [p["data"] for _, p in filtered_aux]
    fs_out = (
        emg_blocks[0][1]["fs"]
        if emg_blocks
        else (
            aux_blocks[0][1]["fs"]
            if aux_blocks
            else int(track_list[0]["SamplingFrequency"])
        )
    )
    ref_len = min(seg.shape[1] for seg in grid_segments) if grid_segments else 0
    if auxiliary_segments:
        ref_len = (
            min(ref_len, min(seg.shape[1] for seg in auxiliary_segments))
            if ref_len
            else min(seg.shape[1] for seg in auxiliary_segments)
        )
    grid_data = _concat_segments(grid_segments, fallback_len=ref_len)
    auxiliary = _concat_segments(auxiliary_segments, fallback_len=grid_data.shape[1] or ref_len)

    return _OTB4Channels(
        grid_data=grid_data,
        grid_names=[name for name, _ in emg_blocks],
        auxiliary=auxiliary,
        auxiliary_names=[name for name, _ in filtered_aux],
        fs_out=fs_out,
        emg_gains=emg_gains,
        emg_hpf=emg_hpf,
        emg_lpf=emg_lpf,
        aux_gains=aux_gains,
        aux_hpf=aux_hpf,
        aux_lpf=aux_lpf,
        emg_not_grid=np.zeros((0, grid_data.shape[1] or auxiliary.shape[1])),
    )



def load_otb_plus(filepath: str) -> dict[str, Any]:
    """Load OTB+ archive (.otb+/.zip) and normalize channels/metadata."""
    with tempfile.TemporaryDirectory() as tmpdir:
        if filepath.endswith(".zip"):
            shutil.unpack_archive(filepath, tmpdir)
        else:
            try:
                with tarfile.open(filepath, "r") as tar:
                    tar.extractall(path=tmpdir)
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
        gains: list[float] = []
        adapter_filters = {}

        for adapter in adapters:
            gain = float(adapter.get("@Gain", 1))
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
                if len(gains) <= pos:
                    gains.extend([0.0] * (pos - len(gains) + 1))
                gains[pos] = gain
                if filt_str:
                    adapter_filters[pos] = filt_str
                n_channels += 1

        sig_path = os.path.join(tmpdir, sig_file)
        dtype = np.int16 if ad_bits == 16 else np.int32
        with open(sig_path, "rb") as f:
            raw_data = np.fromfile(f, dtype=dtype)

        data = raw_data.reshape((n_channels, -1), order="F").astype(np.float64)

        channel_cursor = 0
        total_channels = n_channels
        gain_array = np.zeros(total_channels)
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
            hpf = float(adapter.get("@HighPassFilter", "0"))
            lpf = float(adapter.get("@LowPassFilter", "0"))
            adapter_gain = float(adapter.get("@Gain", "1"))

            channels = adapter["Channel"]
            if not isinstance(channels, list):
                channels = [channels]

            for ch in channels:
                grid_names.append(ch.get("@ID", ""))

                muscle_name = ch.get("@Muscle", "")
                side_name = ch.get("@Side", "")
                muscle_str = (
                    f"{side_name} {muscle_name}".strip()
                    if side_name or muscle_name
                    else ""
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
                                if val < 5:
                                    grid_position = 1
                                else:
                                    grid_position = 2
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

                _apply_otb_plus_scaling(data, channel_cursor, device_name, adapter_id, gain_array, ad_bits)

                high_pass_array[channel_cursor] = hpf
                low_pass_array[channel_cursor] = lpf
                channel_cursor += 1

        data = data[:channel_cursor, :]
        gain_array = gain_array[:channel_cursor]
        high_pass_array = high_pass_array[:channel_cursor]
        low_pass_array = low_pass_array[:channel_cursor]

        adapter_types_arr = np.array(adapter_types, dtype=int)
        grid_ids_arr = np.array(grid_ids, dtype=int)

        grid_mask = (adapter_types_arr == 3) | (adapter_types_arr == 4)
        signal_data = data[grid_mask, :]

        grid_names_masked = [
            grid_names[i] for i in range(len(grid_names)) if grid_mask[i]
        ]
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
        auxiliary = data[aux_mask, :]
        aux_names = [grid_names[i] for i in range(len(grid_names)) if aux_mask[i]]

        emg_mask = adapter_types_arr < 3
        emg_not_grid = data[emg_mask, :]

        sip_files = sorted([f for f in os.listdir(tmpdir) if f.endswith(".sip")])
        if len(sip_files) >= 2:
            for sip in sip_files:
                try:
                    sip_path = os.path.join(tmpdir, sip)
                    with open(sip_path, "rb") as f:
                        sip_data = np.fromfile(f, dtype=np.float64)
                    if len(sip_data) > data.shape[1]:
                        sip_data = sip_data[: data.shape[1]]
                    if auxiliary.shape[0] == 0:
                        auxiliary = sip_data.reshape(1, -1)
                    else:
                        auxiliary = np.vstack([auxiliary, sip_data])
                    aux_names.append(sip.replace(".sip", ""))
                except (OSError, ValueError):
                    pass

        device_meta = parsed_xml.get("Device")
        date_node = device_meta.get("@Date", "") if isinstance(device_meta, dict) else None

        coordinates, ieds, discard_vecs, emg_types = format_hdemg_signal(unique_grids)

        valid_hardware_filters = set()
        if adapter_filters:
            for ch_idx, f_str in adapter_filters.items():
                if ch_idx < len(grid_mask) and grid_mask[ch_idx]:
                    valid_hardware_filters.add(f_str)

        emg_gains = (
            gain_array[grid_mask] if len(gain_array) == len(grid_mask) else np.array([], dtype=float)
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
            "ad_bits": ad_bits,
            "coordinates": coordinates,
            "ieds": ieds,
            "discard_channels": discard_vecs,
            "emg_types": emg_types,
            "hardware_filters": (
                list(valid_hardware_filters) if valid_hardware_filters else ["n/a"]
            ),
            "channel_map_filters": adapter_filters,
            "gains": (
                emg_gains.tolist() if isinstance(emg_gains, np.ndarray) else emg_gains
            ),
            "emg_hpf": emg_hpf.tolist() if isinstance(emg_hpf, np.ndarray) else emg_hpf,
            "emg_lpf": emg_lpf.tolist() if isinstance(emg_lpf, np.ndarray) else emg_lpf,
            "aux_gains": (
                aux_gains.tolist() if isinstance(aux_gains, np.ndarray) else aux_gains
            ),
            "aux_hpf": aux_hpf.tolist() if isinstance(aux_hpf, np.ndarray) else aux_hpf,
            "aux_lpf": aux_lpf.tolist() if isinstance(aux_lpf, np.ndarray) else aux_lpf,
        }

        return {
            "data": signal_data,
            "fsamp": sample_freq,
            "gridname": unique_grids,
            "muscle": unique_muscles,
            "device_name": device_name,
            "auxiliary": auxiliary,
            "auxiliaryname": aux_names,
            "emgnotgrid": emg_not_grid,
            "metadata": metadata,
        }


def load_otb4(filepath: str) -> dict[str, Any]:
    """Load OTB4 archives and normalize EMG/aux channels into MUedit format."""
    with tempfile.TemporaryDirectory() as tmpdir:
        if zipfile.is_zipfile(filepath):
            shutil.unpack_archive(filepath, tmpdir)
        elif tarfile.is_tarfile(filepath):
            with tarfile.open(filepath, "r") as tar:
                tar.extractall(tmpdir)
        else:
            raise OSError("Unsupported OTB4 archive format: expected tar or zip.")

        xml_files = []
        for root, _, files in os.walk(tmpdir):
            for f in files:
                if f == "Tracks_000.xml":
                    xml_files.append(os.path.join(root, f))
        if not xml_files:
            raise FileNotFoundError("No Tracks_000.xml found in OTB4 archive.")

        with open(xml_files[0], "rb") as fd:
            abs_xml = xmltodict.parse(fd.read())

        track_info = abs_xml["ArrayOfTrackInfo"]["TrackInfo"]
        track_list = _ensure_list(track_info)
        device_field = next(
            (t.get("Device") for t in track_list if "Device" in t), "Unknown"
        )
        device = device_field.split(";")[0]

        ch = (
            _parse_otb4_novecento(tmpdir, track_list)
            if device == "Novecento+"
            else _parse_otb4_generic(tmpdir, track_list)
        )

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

        metadata = {
            "acquisition_date": None,
            "manufacturer": "OT Bioelettronica",
            "device_name": device,
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
        }

        return {
            "data": _sanitize_array(ch.grid_data),
            "fsamp": float(ch.fs_out),
            "gridname": refined_grid_names,
            "muscle": [],
            "device_name": device,
            "auxiliary": _sanitize_array(ch.auxiliary),
            "auxiliaryname": ch.auxiliary_names,
            "emgnotgrid": _sanitize_array(ch.emg_not_grid),
            "metadata": metadata,
        }
