// Backend JSON normalised at the boundary: the preview, the QC replies, the
// segments and a run's summary.
import { test, describe } from "node:test";
import assert from "node:assert/strict";

import {
  normalizePreviewPayload,
  toAutoQc,
  toRunSummary,
  toSegmentation,
  toSegments,
} from "../src/api/payloads.js";

describe("normalizePreviewPayload", () => {
  test("missing fields default to empty ones", () => {
    const out = normalizePreviewPayload({ total_samples: "n/a" });
    for (const key of [
      "grid_names",
      "channel_means",
      "coordinates",
      "muscle_names",
      "aux_names",
    ]) {
      assert.deepEqual(out[key], [], key);
    }
    assert.deepEqual(out.metadata, {});
    assert.equal(out.total_samples, 0);
  });

  test("discard channels start from the per-grid bad channels", () => {
    const out = normalizePreviewPayload({
      channel_means: [
        [1, 1, 1],
        [1, 1],
      ],
      metadata: { bad_channels_per_grid: [[0, 1, 0], [1]] },
    });
    // The second grid's list doesn't match its channels, so none are discarded.
    assert.deepEqual(out.discard_channels, [
      [0, 1, 0],
      [0, 0],
    ]);
  });
});

describe("toAutoQc", () => {
  test("keeps the bad channels per grid, or null without them", () => {
    assert.deepEqual(toAutoQc({ bad_channels_per_grid: [[0, 1]] }), {
      badChannelsPerGrid: [[0, 1]],
      artifactRegions: [],
    });
    assert.equal(toAutoQc({}).badChannelsPerGrid, null);
  });

  test("drops artifact regions that are not a finite [start, end] pair", () => {
    const regions = (artifact_regions) =>
      toAutoQc({ artifact_regions }).artifactRegions;
    assert.deepEqual(
      regions([[5], null, { start: 1, end: 2 }, [1, NaN], ["6", 7]]),
      [{ start: 6, end: 7 }],
    );
    assert.deepEqual(regions("0-10"), []);
  });
});

describe("toSegmentation", () => {
  test("reads the target, falling back when it is missing", () => {
    assert.deepEqual(
      toSegmentation({ target_index: 2, target_name: "Force" }),
      { segments: [], targetIndex: 2, targetName: "Force" },
    );
    assert.deepEqual(toSegmentation({ target_index: 1.5 }), {
      segments: [],
      targetIndex: null,
      targetName: "Target",
    });
  });

  test("reads the segments' fields and drops malformed ones", () => {
    const seg = (start, end, kind, extra = {}) => ({
      start,
      end,
      kind,
      direction: null,
      cycles: [],
      frequencyHz: null,
      ...extra,
    });
    assert.deepEqual(
      toSegments([
        {
          start: 0,
          end: 10,
          kind: "sinusoid",
          direction: null,
          cycles: [2, 7],
          frequency_hz: 0.5,
          amplitude: 2,
        },
        { start: 10, end: 20, kind: "ramp", direction: "down" },
        { start: 20, end: 20, kind: "plateau" },
        { start: 30, end: 40, kind: "rest" },
      ]),
      [
        seg(0, 10, "sinusoid", { cycles: [2, 7], frequencyHz: 0.5 }),
        seg(10, 20, "ramp", { direction: "down" }),
      ],
    );
    assert.deepEqual(toSegments(null), []);
  });
});

describe("toRunSummary", () => {
  test("counts units per grid, a grid without any at 0", () => {
    const summary = toRunSummary(
      { n_mu: 3, grid_names: ["A", "B", "C"] },
      { mu_grid_index: [0, 1, 1] },
    );
    assert.deepEqual(summary.nMuByGrid, [1, 2, 0]);
    assert.deepEqual(summary.muGridIndex, [0, 1, 1]);
    assert.deepEqual(
      toRunSummary({ n_mu: 0, grid_names: ["A"] }, null).nMuByGrid,
      [0],
    );
  });
});
