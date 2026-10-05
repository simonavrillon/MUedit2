// Normalisation of decoded API payloads before they reach state.
import { test, describe } from "node:test";
import assert from "node:assert/strict";

import { normalizePreviewPayload, toSpans } from "../src/api/payloads.js";

describe("normalizePreviewPayload", () => {
  test("every array field defaults to an empty array", () => {
    const out = normalizePreviewPayload({});
    for (const key of [
      "grid_names",
      "channel_means",
      "coordinates",
      "muscle",
      "auxiliary_names",
    ]) {
      assert.deepEqual(out[key], [], key);
    }
    assert.deepEqual(out.metadata, {});
    assert.equal(out.total_samples, 0);
  });

  test("valid fields are kept by reference", () => {
    const metadata = { device_name: "Quattrocento" };
    const out = normalizePreviewPayload({ metadata, total_samples: 20 });
    assert.equal(out.metadata, metadata);
    assert.equal(out.total_samples, 20);
  });

  test("a non-numeric total falls back to 0", () => {
    assert.equal(
      normalizePreviewPayload({ total_samples: "n/a" }).total_samples,
      0,
    );
  });
});

describe("toSpans", () => {
  test("accepts objects and [start, end] pairs", () => {
    assert.deepEqual(toSpans([{ start: "1", end: 2 }, [3, 4]]), [
      { start: 1, end: 2 },
      { start: 3, end: 4 },
    ]);
  });

  test("drops regions with a missing or non-finite bound", () => {
    assert.deepEqual(toSpans([{ start: 5 }, null, [1, NaN], [6, 7]]), [
      { start: 6, end: 7 },
    ]);
  });

  test("anything but an array gives no regions", () => {
    assert.deepEqual(toSpans(undefined), []);
    assert.deepEqual(toSpans("0-10"), []);
  });
});
