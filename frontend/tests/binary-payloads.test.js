// Binary payload decoders, fed frames laid out like the Python packers
// (`pack_json_f32_payload` in api/binary.py, `_encode_qc_raw_f32` in
// api/services/preview_service.py).
import { test, describe } from "node:test";
import assert from "node:assert/strict";

import {
  decodeDecomposePreviewPayload,
  decodeEditLoadPayload,
  decodeQcJsonPayload,
  decodeQcRawF32,
  isQcRawF32Payload,
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

function i32(v) {
  const b = new Uint8Array(4);
  new DataView(b.buffer).setInt32(0, v, true);
  return b;
}

function f32s(values) {
  const b = new Uint8Array(values.length * 4);
  const view = new DataView(b.buffer);
  values.forEach((v, i) => view.setFloat32(i * 4, v, true));
  return b;
}

/** Mirror of `pack_json_f32_payload`: magic | v | metaLen | shapes | meta | data. */
function packJsonF32(magic, meta, matrices, { version = 1 } = {}) {
  const metaBytes = encoder.encode(JSON.stringify(meta));
  const shapes = matrices.flatMap((m) => [
    u32(m.length),
    u32(m.length ? m[0].length : 0),
  ]);
  return concat([
    encoder.encode(magic),
    u32(version),
    u32(metaBytes.byteLength),
    ...shapes,
    metaBytes,
    ...matrices.map((m) => f32s(m.flat())),
  ]);
}

/** Mirror of the MQCR packer: fixed header, then (index, n, samples) per channel. */
function packQcRaw(header, channels, { magic = "MQCR", version = 1 } = {}) {
  return concat([
    encoder.encode(magic),
    u32(version),
    i32(header.grid_index),
    i32(header.channel_index),
    i32(header.start),
    i32(header.end),
    i32(header.total_samples),
    f32s([header.fsamp]),
    u32(channels.length),
    ...channels.flatMap((ch) => [
      i32(ch.channel_index),
      u32(ch.series.length),
      f32s(ch.series),
    ]),
  ]);
}

function jsonBuffer(obj) {
  return encoder.encode(JSON.stringify(obj)).buffer;
}

describe("decodeEditLoadPayload", () => {
  const pulse = [
    [0.5, -1.25, 2],
    [3, 4.5, -0.75],
  ];

  test("decodes a MELD frame into metadata plus the pulse matrix", () => {
    const meta = { fsamp: 2048, distimes: [[1, 2], [3]] };
    const out = decodeEditLoadPayload(packJsonF32("MELD", meta, [pulse]));
    assert.deepEqual(out, { ...meta, pulse_trains_full: pulse });
  });

  test("the format header alone selects the binary path", () => {
    const buf = packJsonF32("MELD", { a: 1 }, [pulse]);
    const out = decodeEditLoadPayload(buf, "edit-load-f32-v1");
    assert.deepEqual(out.pulse_trains_full, pulse);
  });

  test("odd-length metadata leaves the float data unaligned", () => {
    const meta = { name: "xy" };
    assert.equal(encoder.encode(JSON.stringify(meta)).byteLength % 4, 1);
    const out = decodeEditLoadPayload(packJsonF32("MELD", meta, [pulse]));
    assert.deepEqual(out.pulse_trains_full, pulse);
  });

  test("an empty matrix decodes to no rows", () => {
    const out = decodeEditLoadPayload(packJsonF32("MELD", {}, [[]]));
    assert.deepEqual(out.pulse_trains_full, []);
  });

  test("falls back to JSON without magic or header", () => {
    const payload = { pulse_trains_full: [[1, 2]], fsamp: 2048 };
    assert.deepEqual(decodeEditLoadPayload(jsonBuffer(payload)), payload);
  });

  test("rejects an unsupported version", () => {
    const buf = packJsonF32("MELD", {}, [pulse], { version: 2 });
    assert.throws(() => decodeEditLoadPayload(buf), /version: 2/);
  });

  test("rejects a binary header on a frame without the magic", () => {
    const buf = packJsonF32("XXXX", {}, [pulse]);
    assert.throws(
      () => decodeEditLoadPayload(buf, "edit-load-f32-v1"),
      /Invalid edit-load/,
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

  test("reads both matrices in order with their own shapes", () => {
    const meta = { iteration: 3, sil: [0.91, 0.95] };
    const buf = packJsonF32("MDPV", meta, [full, all]);
    assert.deepEqual(decodeDecomposePreviewPayload(buf), {
      ...meta,
      pulse_trains_full: full,
      pulse_trains_all: all,
    });
  });

  test("the format header alone selects the binary path", () => {
    const buf = packJsonF32("MDPV", { name: "x" }, [full, all]);
    const out = decodeDecomposePreviewPayload(buf, "decompose-preview-f32-v1");
    assert.deepEqual(out.pulse_trains_all, all);
  });

  test("falls back to JSON without magic or header", () => {
    const payload = { pulse_trains_full: [], pulse_trains_all: [] };
    assert.deepEqual(
      decodeDecomposePreviewPayload(jsonBuffer(payload)),
      payload,
    );
  });

  test("rejects an unsupported version", () => {
    const buf = packJsonF32("MDPV", {}, [full, all], { version: 7 });
    assert.throws(() => decodeDecomposePreviewPayload(buf), /version: 7/);
  });

  test("rejects a binary header on a frame without the magic", () => {
    const buf = packJsonF32("XXXX", {}, [full, all]);
    assert.throws(
      () => decodeDecomposePreviewPayload(buf, "decompose-preview-f32-v1"),
      /Invalid decompose-preview/,
    );
  });
});

describe("decodeQcRawF32", () => {
  const header = {
    grid_index: 1,
    channel_index: -1,
    start: 100,
    end: 612,
    total_samples: 20480,
    fsamp: 2048,
  };

  test("decodes the header and variable-length channel blocks", () => {
    const channels = [
      { channel_index: 0, series: [0.5, 1.5, -2.5] },
      { channel_index: -3, series: [] },
      { channel_index: 63, series: [4, 5] },
    ];
    assert.deepEqual(decodeQcRawF32(packQcRaw(header, channels)), {
      ...header,
      channels,
    });
  });

  test("a frame with no channels decodes to an empty list", () => {
    assert.deepEqual(decodeQcRawF32(packQcRaw(header, [])).channels, []);
  });

  test("rejects a missing magic", () => {
    const buf = packQcRaw(header, [], { magic: "XXXX" });
    assert.throws(() => decodeQcRawF32(buf), /Invalid QC raw/);
  });

  test("rejects an unsupported version", () => {
    const buf = packQcRaw(header, [], { version: 2 });
    assert.throws(() => decodeQcRawF32(buf), /version: 2/);
  });
});

describe("QC payload detection", () => {
  test("recognises the format header", () => {
    assert.equal(isQcRawF32Payload(jsonBuffer({}), "qc-raw-f32-v1"), true);
  });

  test("recognises the magic without a header", () => {
    assert.equal(isQcRawF32Payload(encoder.encode("MQCR....").buffer), true);
  });

  test("JSON and short buffers are not binary", () => {
    assert.equal(isQcRawF32Payload(jsonBuffer({ a: 1 })), false);
    assert.equal(isQcRawF32Payload(new ArrayBuffer(2)), false);
    assert.equal(isQcRawF32Payload(null), false);
  });

  test("decodeQcJsonPayload parses the JSON fallback", () => {
    const payload = { channels: [{ channel_index: 0, series: [1] }] };
    assert.deepEqual(decodeQcJsonPayload(jsonBuffer(payload)), payload);
  });
});
