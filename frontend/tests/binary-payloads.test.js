// Binary payload codecs: MUB1 frames (api/binary.py), including the viewport
// series frames of /series/* (api/services/series_service.py).
import { test, describe } from "node:test";
import assert from "node:assert/strict";

import {
  decodeDecomposePreviewPayload,
  decodeEditLoadPayload,
  decodeFrame,
  decodeSeriesFrame,
  encodeFrame,
} from "../src/api/binary-payloads.js";

const encoder = new TextEncoder();

/** Concatenate byte chunks into one ArrayBuffer. */
function concat(chunks) {
  const total = chunks.reduce((n, c) => n + c.byteLength, 0);
  const out = new Uint8Array(total);
  let offset = 0;
  for (const c of chunks) {
    out.set(c, offset);
    offset += c.byteLength;
  }
  return out.buffer;
}

function u32(v) {
  const b = new Uint8Array(4);
  new DataView(b.buffer).setUint32(0, v, true);
  return b;
}

function jsonBuffer(obj) {
  return encoder.encode(JSON.stringify(obj)).buffer;
}

describe("MUB1 frames", () => {
  test("encode then decode round-trips metadata and every array", () => {
    const meta = { fsamp: 2048, names: ["a", "b"] };
    const buf = encodeFrame(meta, {
      pulse: {
        dtype: "f4",
        shape: [2, 3],
        rows: [
          [0.5, -1.25, 2],
          [3, 4.5, -0.75],
        ],
      },
      spikes: { dtype: "i4", shape: [3], data: [7, -1, 2 ** 30] },
      flags: { dtype: "u1", shape: [2], data: [0, 255] },
      grid: { dtype: "i2", shape: [1], data: [-300] },
    });
    const { meta: gotMeta, arrays } = decodeFrame(buf);
    assert.deepEqual(gotMeta, meta);
    assert.deepEqual(
      Array.from(arrays.pulse.data),
      [0.5, -1.25, 2, 3, 4.5, -0.75],
    );
    assert.deepEqual(arrays.pulse.shape, [2, 3]);
    assert.deepEqual(Array.from(arrays.spikes.data), [7, -1, 2 ** 30]);
    assert.deepEqual(Array.from(arrays.flags.data), [0, 255]);
    assert.deepEqual(Array.from(arrays.grid.data), [-300]);
  });

  test("decoded arrays are views into the buffer, 8-byte aligned", () => {
    const buf = encodeFrame(
      { name: "odd" },
      {
        a: { dtype: "u1", shape: [3], data: [1, 2, 3] },
        b: { dtype: "f4", shape: [2], data: [1, 2] },
      },
    );
    const { arrays } = decodeFrame(buf);
    for (const arr of Object.values(arrays)) {
      assert.equal(arr.data.buffer, buf);
      assert.equal(arr.data.byteOffset % 8, 0);
    }
  });

  test("empty arrays keep their shape", () => {
    const buf = encodeFrame(
      {},
      { p: { dtype: "f4", shape: [0, 5], rows: [] } },
    );
    const { arrays } = decodeFrame(buf);
    assert.deepEqual(arrays.p.shape, [0, 5]);
    assert.equal(arrays.p.data.length, 0);
  });

  test("rejects a buffer without the magic", () => {
    assert.throws(() => decodeFrame(jsonBuffer({ a: 1 })), /Invalid MUB1/);
  });

  test("rejects an array that runs past the end", () => {
    const buf = encodeFrame(
      {},
      { a: { dtype: "f4", shape: [2], data: [1, 2] } },
    );
    assert.throws(
      () => decodeFrame(buf.slice(0, buf.byteLength - 4)),
      /past the end/,
    );
  });

  test("rejects an unknown dtype", () => {
    const header = encoder.encode(
      JSON.stringify({
        meta: {},
        arrays: [{ name: "a", dtype: "f8", shape: [1], offset: 0 }],
      }),
    );
    const buf = concat([
      encoder.encode("MUB1"),
      u32(header.byteLength),
      header,
    ]);
    assert.throws(() => decodeFrame(buf), /Unsupported MUB1 dtype: f8/);
  });
});

describe("decodeEditLoadPayload", () => {
  const pulse = [
    [0.5, -1.25, 2],
    [3, 4.5, -0.75],
  ];
  const frame = (meta) =>
    encodeFrame(meta, {
      pulse_trains_full: { dtype: "f4", shape: [2, 3], rows: pulse },
    });

  test("decodes a frame into metadata plus the pulse matrix", () => {
    const meta = { fsamp: 2048, distime_all: [[1, 2], [3]] };
    assert.deepEqual(decodeEditLoadPayload(frame(meta)), {
      ...meta,
      pulse_trains_full: pulse,
    });
  });

  test("the format header alone selects the frame path", () => {
    const out = decodeEditLoadPayload(frame({ a: 1 }), "mub1");
    assert.deepEqual(out.pulse_trains_full, pulse);
  });

  test("an empty matrix decodes to no rows", () => {
    const buf = encodeFrame(
      {},
      {
        pulse_trains_full: { dtype: "f4", shape: [0, 0], rows: [] },
      },
    );
    assert.deepEqual(decodeEditLoadPayload(buf).pulse_trains_full, []);
  });

  test("falls back to JSON without magic or header", () => {
    const payload = { pulse_trains_full: [[1, 2]], fsamp: 2048 };
    assert.deepEqual(decodeEditLoadPayload(jsonBuffer(payload)), payload);
  });

  test("rejects a frame header on a body without the magic", () => {
    assert.throws(
      () => decodeEditLoadPayload(jsonBuffer({}), "mub1"),
      /Invalid MUB1/,
    );
  });
});

describe("decodeDecomposePreviewPayload", () => {
  const full = [
    [1, 2, 3, 4],
    [5, 6, 7, 8],
  ];
  const all = [
    [-1, -2],
    [-3, -4],
    [-5, -6],
  ];

  test("reads both matrices with their own shapes", () => {
    const meta = { iteration: 3, sil: [0.91, 0.95] };
    const buf = encodeFrame(meta, {
      pulse_trains_full: { dtype: "f4", shape: [2, 4], rows: full },
      pulse_trains_all: { dtype: "f4", shape: [3, 2], rows: all },
    });
    assert.deepEqual(decodeDecomposePreviewPayload(buf), {
      ...meta,
      pulse_trains_full: full,
      pulse_trains_all: all,
    });
  });

  test("falls back to JSON without magic or header", () => {
    const payload = { pulse_trains_full: [], pulse_trains_all: [] };
    assert.deepEqual(
      decodeDecomposePreviewPayload(jsonBuffer(payload)),
      payload,
    );
  });
});

describe("decodeSeriesFrame", () => {
  const rows = [
    [1, 2, 3],
    [4, 5, 6],
  ];

  test("an envelope gives one min/max pair of views per row", () => {
    const buffer = encodeFrame(
      { kind: "envelope", start: 0, end: 300, bins: 3, names: ["a", "b"] },
      {
        min: { dtype: "f4", shape: [2, 3], rows },
        max: {
          dtype: "f4",
          shape: [2, 3],
          rows: rows.map((r) => r.map((v) => v * 10)),
        },
      },
    );
    const { meta, rows: decoded } = decodeSeriesFrame(buffer);
    assert.deepEqual(meta.names, ["a", "b"]);
    assert.equal(decoded.length, 2);
    const [first] = decoded;
    assert.ok(!(first instanceof Float32Array));
    assert.deepEqual(Array.from(first.min), [1, 2, 3]);
    assert.deepEqual(Array.from(first.max), [10, 20, 30]);
    assert.equal(first.min.buffer, buffer); // views, not copies
  });

  test("samples give one Float32Array view per row", () => {
    const buffer = encodeFrame(
      { kind: "samples", start: 10, end: 13 },
      { samples: { dtype: "f4", shape: [2, 3], rows } },
    );
    const { rows: decoded } = decodeSeriesFrame(buffer);
    assert.ok(decoded[1] instanceof Float32Array);
    assert.deepEqual(Array.from(decoded[1]), [4, 5, 6]);
    assert.equal(decoded[1].buffer, buffer);
  });

  test("a series without rows decodes to none", () => {
    const empty = { dtype: "f4", shape: [0, 64] };
    const buffer = encodeFrame(
      { kind: "envelope" },
      { min: empty, max: empty },
    );
    assert.deepEqual(decodeSeriesFrame(buffer).rows, []);
  });
});
