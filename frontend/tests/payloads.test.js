// Normalisation of decoded API payloads before they reach state.
import { test, describe } from "node:test";
import assert from "node:assert/strict";

import {
  normalizePreviewPayload,
  toSpikeArray,
  toSpans,
  totalSamplesFromDistimes,
} from "../src/api/payloads.js";

describe("toSpikeArray", () => {
  test("a typed row is kept as it is", () => {
    const row = Int32Array.from([1, 2]);
    assert.equal(toSpikeArray(row), row);
  });

  test("a JSON row becomes int32, dropping unreadable and negative times", () => {
    const out = toSpikeArray([120, "x", undefined, Infinity, NaN, -3, "480"]);
    assert.ok(out instanceof Int32Array);
    assert.deepEqual(Array.from(out), [120, 480]);
  });

  test("anything but a row is empty", () => {
    assert.equal(toSpikeArray("nope").length, 0);
  });
});

describe("normalizePreviewPayload", () => {
  test("every array field defaults to an empty array", () => {
    const out = normalizePreviewPayload({});
    for (const key of [
      "grid_names",
      "rois",
      "channel_means",
      "coordinates",
      "muscle",
      "distime_all",
      "mu_grid_index",
    ]) {
      assert.deepEqual(out[key], [], key);
    }
    assert.deepEqual(out.metadata, {});
    assert.equal(out.total_samples, 0);
  });

  test("JSON discharge times become one Int32Array per MU", () => {
    const out = normalizePreviewPayload({ distime_all: [[1, 2], "x"] });
    assert.deepEqual(
      out.distime_all.map((r) => Array.from(r)),
      [[1, 2], []],
    );
  });

  test("valid fields are kept by reference", () => {
    const metadata = { device_name: "Quattrocento" };
    const out = normalizePreviewPayload({ metadata, total_samples: 20 });
    assert.equal(out.metadata, metadata);
    assert.equal(out.total_samples, 20);
  });

  test("ROIs sent as [start, end] pairs become spans", () => {
    const out = normalizePreviewPayload({
      rois: [
        [0, 500],
        [500, 1000],
      ],
    });
    assert.deepEqual(out.rois, [
      { start: 0, end: 500 },
      { start: 500, end: 1000 },
    ]);
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

describe("totalSamplesFromDistimes", () => {
  test("last spike + 1 across MUs, skipping empty and non-numeric entries", () => {
    assert.equal(totalSamplesFromDistimes([[3, 9], null, [], [7, NaN]]), 10);
  });

  test("no spikes gives 1", () => {
    assert.equal(totalSamplesFromDistimes([]), 1);
    assert.equal(totalSamplesFromDistimes([[], null]), 1);
  });

  test("20 min x 100 MUs of spikes does not overflow the argument limit", () => {
    const mu = Array.from({ length: 24_000 }, (_, i) => i * 100);
    const distimes = Array.from({ length: 100 }, () => mu);
    assert.equal(totalSamplesFromDistimes(distimes), 2_399_901);
  });
});
