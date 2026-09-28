"""MUB1 frame packing and unpacking (``muedit.api.binary``)."""

from __future__ import annotations

import json
import struct

import numpy as np
import pytest

from muedit.api.binary import FRAME_MAGIC, pack_frame, unpack_frame


def _header(frame: memoryview | bytes) -> dict:
    (header_len,) = struct.unpack_from("<I", frame, 4)
    return json.loads(bytes(frame[8 : 8 + header_len]))


def _frame(header: dict, data: bytes = b"") -> bytes:
    raw = json.dumps(header).encode()
    head = FRAME_MAGIC + struct.pack("<I", len(raw)) + raw
    return head + b"\0" * (-len(head) % 8) + data


class TestRoundTrip:
    def test_every_wire_dtype_and_the_metadata(self) -> None:
        arrays = {
            "f4": (np.array([[0.5, -1.25, 3.0]]), "f4"),
            "i4": (np.array([3, -7, 11]), "i4"),
            "i8": (np.array([2**40, -1]), "i8"),
            "u1": (np.array([0, 255], dtype=np.uint8), "u1"),
            "i2": (np.array([[-300], [300]], dtype=np.int16), "i2"),
        }
        meta = {"fsamp": 2048.0, "names": ["a", "b"], "counts": np.array([1, 2])}
        got_meta, got = unpack_frame(pack_frame(meta, arrays))
        assert got_meta == {"fsamp": 2048.0, "names": ["a", "b"], "counts": [1, 2]}
        for name, (value, code) in arrays.items():
            assert got[name].dtype == np.dtype("<" + code)
            np.testing.assert_array_equal(got[name], value)

    def test_every_array_starts_8_byte_aligned(self) -> None:
        frame = pack_frame(
            {"x": "odd"},
            {
                "a": (np.ones(3, dtype=np.uint8), "u1"),
                "b": (np.ones(5, dtype=np.int16), "i2"),
                "c": (np.ones((2, 3)), "f4"),
            },
        )
        (header_len,) = struct.unpack_from("<I", frame, 4)
        data_start = 8 + header_len + (-(8 + header_len) % 8)
        assert data_start % 8 == 0
        for spec in _header(frame)["arrays"]:
            assert (data_start + spec["offset"]) % 8 == 0
        assert len(frame) % 8 == 0

    def test_float64_is_cast_to_float32(self) -> None:
        value = np.array([[1 / 3, 2 / 3]])
        _, arrays = unpack_frame(pack_frame({}, {"p": (value, "f4")}))
        np.testing.assert_array_equal(arrays["p"], value.astype(np.float32))

    def test_empty_arrays_keep_their_shape(self) -> None:
        _, arrays = unpack_frame(
            pack_frame({}, {"a": (np.zeros((0, 7)), "f4"), "b": (np.zeros((0, 0)), "f4")})
        )
        assert arrays["a"].shape == (0, 7)
        assert arrays["b"].shape == (0, 0)

    def test_unpacked_arrays_are_views_into_the_body(self) -> None:
        body = bytes(pack_frame({}, {"a": (np.arange(4), "i4")}))
        _, arrays = unpack_frame(body)
        assert not arrays["a"].flags.owndata
        assert not arrays["a"].flags.writeable


class TestPackRejects:
    def test_unsupported_dtype(self) -> None:
        with pytest.raises(ValueError, match="wire dtype"):
            pack_frame({}, {"a": (np.ones(2), "f8")})

    def test_lossy_cast(self) -> None:
        with pytest.raises(TypeError):
            pack_frame({}, {"a": (np.ones(2), "i4")})


class TestUnpackRejects:
    @pytest.mark.parametrize(
        ("body", "match"),
        [
            (b"MUB", "shorter"),
            (b"XXXX" + struct.pack("<I", 0), "not a MUB1"),
            (FRAME_MAGIC + struct.pack("<I", 99) + b"{}", "past the end"),
            (FRAME_MAGIC + struct.pack("<I", 3) + b"{x}", "not a JSON object"),
            (FRAME_MAGIC + struct.pack("<I", 2) + b"[]", "not a JSON object"),
        ],
    )
    def test_malformed_prefix_or_header(self, body: bytes, match: str) -> None:
        with pytest.raises(ValueError, match=match):
            unpack_frame(body)

    @pytest.mark.parametrize(
        ("spec", "match"),
        [
            ({"name": "a", "dtype": "f8", "shape": [1], "offset": 0}, "wire dtype"),
            ({"name": "a", "dtype": "f4", "shape": [1], "offset": 4}, "offset or shape"),
            ({"name": "a", "dtype": "f4", "shape": [-1], "offset": 0}, "offset or shape"),
            ({"name": "a", "dtype": "f4", "shape": [3], "offset": 0}, "past the end"),
            ({"name": "a", "dtype": "f4"}, "malformed"),
        ],
    )
    def test_bad_array_entry(self, spec: dict, match: str) -> None:
        with pytest.raises(ValueError, match=match):
            unpack_frame(_frame({"meta": {}, "arrays": [spec]}, b"\0" * 8))

    def test_meta_must_be_an_object(self) -> None:
        with pytest.raises(ValueError, match="meta object"):
            unpack_frame(_frame({"meta": [1], "arrays": []}))
