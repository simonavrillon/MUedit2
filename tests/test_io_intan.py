"""Intan RHD loading on synthetic recordings in all three save layouts."""

from __future__ import annotations

import json
import struct
from pathlib import Path

import numpy as np
import pytest

from muedit.io import load_signal
from muedit.io._intan import load_intan

_GRID = "GR10MM0804"
_N_CH = 32
_N_PORTS = 2
_SAMPLES_PER_BLOCK = 128
_N_BLOCKS = 3
_N_SAMPLES = _SAMPLES_PER_BLOCK * _N_BLOCKS
_FSAMP = 2000.0


def _qstring(text: str) -> bytes:
    """Encode a Qt ``QString`` the way the RHD header stores one."""
    raw = text.encode("utf-16-le")
    return struct.pack("<I", len(raw)) + raw


def _channel_record(native: str, order: int, signal_type: int) -> bytes:
    """Encode one channel record of the header's signal-group table."""
    return (
        _qstring(native)
        + _qstring(native)
        + struct.pack("<hhhhhh", order, order, signal_type, 1, order, 0)
        + struct.pack("<hhhh", 0, 0, 0, 0)
        + struct.pack("<ff", 0.0, 0.0)
    )


def _write_header(path: Path) -> None:
    """Write a version 3.0 RHD header: two amplifier ports, aux, and digital in."""
    buf = struct.pack("<Ihh", 0xC6912702, 3, 0)
    buf += struct.pack("<f", _FSAMP)
    buf += struct.pack("<h", 1)
    buf += struct.pack("<ffffff", 20.0, 10.0, 500.0, 20.0, 10.0, 500.0)
    buf += struct.pack("<h", 1)
    buf += struct.pack("<ff", 1000.0, 1000.0)
    buf += _qstring("") * 3
    buf += struct.pack("<h", 0)
    buf += struct.pack("<h", 0)
    buf += _qstring("n/a")
    buf += struct.pack("<h", _N_PORTS + 1)

    for port in ("A", "B"):
        buf += _qstring(f"Port {port}") + _qstring(port)
        buf += struct.pack("<hhh", 1, _N_CH + 1, _N_CH)
        for i in range(_N_CH):
            buf += _channel_record(f"{port}-{i:03d}", i, 0)
        buf += _channel_record(f"{port}-AUX1", _N_CH, 1)

    buf += _qstring("Digital In Ports") + _qstring("DIGITAL-IN")
    buf += struct.pack("<hhh", 1, 1, 0)
    buf += _channel_record("DIGITAL-IN-01", 0, 4)

    path.write_bytes(buf)


def _reference_signals() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Build amplifier / aux / digital sample blocks with per-channel signatures."""
    rng = np.random.default_rng(7)
    amp = rng.integers(-2000, 2000, size=(_N_PORTS * _N_CH, _N_SAMPLES)).astype(np.int16)
    amp += (np.arange(_N_PORTS * _N_CH, dtype=np.int16) * 10)[:, None]
    aux = rng.integers(0, 3000, size=(_N_PORTS, _N_SAMPLES)).astype(np.uint16)
    digital = rng.integers(0, 2, size=(1, _N_SAMPLES)).astype(np.uint16)
    return amp, aux, digital


def _write_per_channel(
    directory: Path, amp: np.ndarray, aux: np.ndarray, digital: np.ndarray
) -> None:
    """Write the "one file per channel" layout: one ``.dat`` per saved channel."""
    _write_header(directory / "info.rhd")
    np.arange(_N_SAMPLES, dtype=np.int32).tofile(directory / "time.dat")
    for port_i, port in enumerate(("A", "B")):
        for i in range(_N_CH):
            amp[port_i * _N_CH + i].tofile(directory / f"amp-{port}-{i:03d}.dat")
        aux[port_i].tofile(directory / f"aux-{port}-AUX1.dat")
    digital[0].tofile(directory / "board-DIGITAL-IN-01.dat")


def _write_per_signal_type(
    directory: Path, amp: np.ndarray, aux: np.ndarray, digital: np.ndarray
) -> None:
    """Write the "one file per signal type" layout: channels interleaved per file."""
    _write_header(directory / "info.rhd")
    np.arange(_N_SAMPLES, dtype=np.int32).tofile(directory / "time.dat")
    amp.T.astype(np.int16).tofile(directory / "amplifier.dat")
    aux[:, ::4].T.astype(np.uint16).tofile(directory / "auxiliary.dat")
    digital[0].astype(np.uint16).tofile(directory / "digitalin.dat")


def _write_traditional(path: Path, amp: np.ndarray, aux: np.ndarray, digital: np.ndarray) -> None:
    """Write a monolithic ``.rhd``: fixed-size data blocks following the header."""
    _write_header(path)
    with open(path, "ab") as handle:
        for b in range(_N_BLOCKS):
            lo, hi = b * _SAMPLES_PER_BLOCK, (b + 1) * _SAMPLES_PER_BLOCK
            handle.write(np.arange(lo, hi, dtype=np.int32).tobytes())
            handle.write((amp[:, lo:hi].astype(np.int32) + 32768).astype(np.uint16).tobytes())
            handle.write(aux[:, lo:hi:4].astype(np.uint16).tobytes())
            handle.write(digital[0, lo:hi].astype(np.uint16).tobytes())


@pytest.fixture
def synthetic_layouts(tmp_path: Path) -> dict[str, Path]:
    """Write the same recording in all three Intan save layouts."""
    amp, aux, digital = _reference_signals()
    per_channel = tmp_path / "per_channel"
    per_signal_type = tmp_path / "per_signal_type"
    traditional = tmp_path / "traditional"
    for d in (per_channel, per_signal_type, traditional):
        d.mkdir()
    _write_per_channel(per_channel, amp, aux, digital)
    _write_per_signal_type(per_signal_type, amp, aux, digital)
    _write_traditional(traditional / "recording.rhd", amp, aux, digital)
    return {
        "per_channel": per_channel,
        "per_signal_type": per_signal_type,
        "traditional": traditional / "recording.rhd",
    }


def test_all_three_layouts_recover_the_same_channels(synthetic_layouts: dict[str, Path]) -> None:
    amp, _, _ = _reference_signals()
    expected = amp.astype(np.float64) * 0.195e-3

    loaded = {
        name: load_intan(str(path), grid_names=_GRID) for name, path in synthetic_layouts.items()
    }
    for name, sig in loaded.items():
        assert sig.metadata["intan_layout"] == name
        assert sig.data.shape == (_N_PORTS * _N_CH, _N_SAMPLES), name
        assert sig.fsamp == _FSAMP, name
        assert sig.gridname == [_GRID] * _N_PORTS, name
        np.testing.assert_allclose(sig.data, expected, rtol=0, atol=1e-12, err_msg=name)


def test_layouts_agree_on_auxiliary_and_digital(synthetic_layouts: dict[str, Path]) -> None:
    _, aux, digital = _reference_signals()

    for name, path in synthetic_layouts.items():
        sig = load_intan(str(path), grid_names=_GRID)
        assert sig.auxiliaryname == ["A-AUX1", "B-AUX1", "DIGITAL-IN-01"], name
        np.testing.assert_allclose(sig.auxiliary[2], digital[0], atol=1e-12, err_msg=name)
        if name == "per_channel":
            np.testing.assert_allclose(sig.auxiliary[0], aux[0] * 37.4e-6, atol=1e-12, err_msg=name)
        else:
            np.testing.assert_allclose(
                sig.auxiliary[0, ::4], aux[0, ::4] * 37.4e-6, atol=1e-12, err_msg=name
            )


def test_grid_names_resolve_from_sidecar(synthetic_layouts: dict[str, Path]) -> None:
    """``load_signal`` cannot pass arguments, so a sidecar must name the grids."""
    directory = synthetic_layouts["per_channel"]
    (directory / "muedit_grids.json").write_text(
        json.dumps({"gridname": [_GRID, _GRID], "muscle": ["ta", "gm"]})
    )
    sig = load_signal(str(directory))
    assert sig.gridname == [_GRID, _GRID]
    assert sig.muscle == ["ta", "gm"]


def test_unknown_grid_raises_actionable_error(synthetic_layouts: dict[str, Path]) -> None:
    with pytest.raises(ValueError, match="does not record one"):
        load_intan(str(synthetic_layouts["per_channel"]))


def test_intan_adapter_channel_map_is_a_gr08mm1305_permutation() -> None:
    """INTAN64-1305 re-orders the GR08MM1305 electrodes onto Intan channels 1-64."""
    from muedit.signal.grid import _GRID_CATALOG

    spec = _GRID_CATALOG["INTAN64-1305"]
    assert spec.nbelectrodes == 64
    assert spec.ied == 8.0
    assert sorted(int(v) for v in spec.channel_map.flat if v) == list(range(1, 65))
    otb = _GRID_CATALOG["GR08MM1305"].channel_map
    np.testing.assert_array_equal(spec.channel_map.astype(bool), otb.astype(bool))


def test_rejects_non_intan_file(tmp_path: Path) -> None:
    bogus = tmp_path / "bogus.rhd"
    bogus.write_bytes(b"\x00" * 4096)
    with pytest.raises(OSError, match="magic number"):
        load_intan(str(bogus))
