// Normalisation of decoded API payloads before they reach state.
import { test, describe } from "node:test";
import assert from "node:assert/strict";

import {
  normalizeEditLoadPayload,
  normalizePreviewPayload,
  toSpans,
} from "../src/api/payloads.js";

describe("normalizeEditLoadPayload", () => {
  test("a missing payload becomes a complete empty shape", () => {
    for (const payload of [null, undefined, "oops", 42]) {
      assert.deepEqual(normalizeEditLoadPayload(payload), {
        pulse_trains: [],
        pulse_trains_full: [],
        distime_all: [],
        grid_names: [],
        mu_grid_index: [],
        parameters: {},
        total_samples: 0,
        fsamp: null,
        file_label: "",
        edit_signal_token: "",
      });
    }
  });

  test("discharge times are coerced to numbers, one row per MU", () => {
    const out = normalizeEditLoadPayload({
      distime_all: [["1", 2], "not a row", [5]],
    });
    assert.deepEqual(out.distime_all, [[1, 2], [], [5]]);
  });

  test("unreadable discharge times are dropped, not moved to sample 0", () => {
    const out = normalizeEditLoadPayload({
      distime_all: [[120, "x", undefined, Infinity, NaN, 480]],
    });
    assert.deepEqual(out.distime_all, [[120, 480]]);
  });

  test("falls back to distime when distime_all is absent", () => {
    const out = normalizeEditLoadPayload({ distime: [[3, 4]] });
    assert.deepEqual(out.distime_all, [[3, 4]]);
  });

  test("wrong-typed fields are replaced, unknown fields pass through", () => {
    const out = normalizeEditLoadPayload({
      pulse_trains: "nope",
      parameters: "nope",
      total_samples: "4096",
      file_label: 7,
      extra: "kept",
    });
    assert.deepEqual(out.pulse_trains, []);
    assert.deepEqual(out.parameters, {});
    assert.equal(out.total_samples, 4096);
    assert.equal(out.file_label, "7");
    assert.equal(out.extra, "kept");
  });

  test("a zero fsamp is kept rather than nulled", () => {
    assert.equal(normalizeEditLoadPayload({ fsamp: 0 }).fsamp, 0);
  });
});

describe("normalizePreviewPayload", () => {
  test("every array field defaults to an empty array", () => {
    const out = normalizePreviewPayload({});
    for (const key of [
      "mean_abs",
      "grid_mean_abs",
      "grid_names",
      "rois",
      "channel_means",
      "coordinates",
      "muscle",
      "pulse_trains_full",
      "pulse_trains_all",
      "distime_all",
      "mu_grid_index",
      "auxiliary",
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
