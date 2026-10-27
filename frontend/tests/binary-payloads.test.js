// Binary payload codecs: MUB1 frames (api/binary.py), including the viewport
// series frames of /series/* (api/services/series_service.py).
import { test, describe } from "node:test";
import assert from "node:assert/strict";

import {
  decodeEditSessionFrame,
  decodePulseFrame,
  decodeSeriesFrame,
} from "../src/api/binary-payloads.js";
import { encodeFrame } from "./frames.js";

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
  test("rejects a buffer without the magic", () => {
    assert.throws(
      () => decodeSeriesFrame(jsonBuffer({ a: 1 })),
      /Invalid MUB1/,
    );
  });

  test("rejects an array that runs past the end", () => {
    const buf = encodeFrame(
      {},
      { a: { dtype: "f4", shape: [2], data: [1, 2] } },
    );
    assert.throws(
      () => decodeSeriesFrame(buf.slice(0, buf.byteLength - 4)),
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
    assert.throws(() => decodeSeriesFrame(buf), /Unsupported MUB1 dtype: f8/);
  });
});

/** A CSR pair as the server sends discharge times: rows of int32, int64 offsets. */
function csr(rows) {
  const offsets = [0];
  for (const row of rows) offsets.push(offsets.at(-1) + row.length);
  return {
    values: { dtype: "i4", shape: [offsets.at(-1)], data: rows.flat() },
    offsets: { dtype: "i8", shape: [offsets.length], data: offsets },
  };
}

describe("decodeEditSessionFrame", () => {
  test("splits discharge times per MU, as views", () => {
    const spikes = csr([[10, 20], [30]]);
    const buf = encodeFrame(
      { token: "abc", n_mu: 2 },
      { spikes: spikes.values, spike_offsets: spikes.offsets },
    );
    const frame = decodeEditSessionFrame(buf);
    assert.deepEqual(frame.meta, { token: "abc", n_mu: 2 });
    assert.deepEqual(
      frame.spikeTimes.map((r) => Array.from(r)),
      [[10, 20], [30]],
    );
    assert.ok(frame.spikeTimes[0] instanceof Int32Array);
    assert.equal(frame.spikeTimes[0].buffer, buf, "views, not copies");
  });
});

describe("decodePulseFrame", () => {
  const markers = {
    spikes: { dtype: "i4", shape: [2], data: [3, 5] },
    spike_values: { dtype: "f4", shape: [2], data: [0.5, 0.25] },
  };

  test("an envelope with its markers", () => {
    const buf = encodeFrame(
      { kind: "envelope", mu: 2, start: 0, end: 8, bins: 2, version: 7 },
      {
        min: { dtype: "f4", shape: [1, 2], data: [0, -1] },
        max: { dtype: "f4", shape: [1, 2], data: [1, 2] },
        ...markers,
      },
    );
    const view = decodePulseFrame(buf);
    assert.deepEqual(
      [view.mu, view.start, view.end, view.bins, view.version, view.flagged],
      [2, 0, 8, 2, 7, false],
    );
    assert.deepEqual(Array.from(view.row.max), [1, 2]);
    assert.deepEqual(Array.from(view.spikes), [3, 5]);
    assert.deepEqual(Array.from(view.spikeValues), [0.5, 0.25]);
    assert.equal(view.artifacts.length, 0, "a run sends no artifacts");
  });

  test("samples when zoomed in past one sample per bin", () => {
    const buf = encodeFrame(
      { kind: "samples", mu: 0, start: 4, end: 7, bins: 100, version: 1 },
      { samples: { dtype: "f4", shape: [1, 3], data: [1, 2, 3] }, ...markers },
    );
    const view = decodePulseFrame(buf);
    assert.ok(view.row instanceof Float32Array);
    assert.deepEqual(Array.from(view.row), [1, 2, 3]);
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
