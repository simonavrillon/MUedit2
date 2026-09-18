// State actions, selectors and transitions that carry logic beyond a plain set.
import { test, describe, beforeEach } from "node:test";
import assert from "node:assert/strict";

// config.js reads window.location at import time.
globalThis.window = {
  location: {
    port: "8080",
    protocol: "http:",
    hostname: "localhost",
    origin: "http://localhost:8080",
  },
};

const { state: initialState } = await import("../src/app/state.js");
const actions = await import("../src/state/actions.js");
const selectors = await import("../src/state/selectors.js");
const { beginRawPreviewTransition, rollbackRawPreviewTransition } =
  await import("../src/state/transitions.js");

const pristine = structuredClone(initialState);
let state;

beforeEach(() => {
  state = structuredClone(pristine);
});

describe("ensureDiscardMasks", () => {
  test("seeds masks from the per-grid bad channels", () => {
    state.channelMeans = [
      [1, 1, 1],
      [1, 1],
    ];
    state.metadata = {
      bad_channels_per_grid: [
        [0, 1, 0],
        [1, 1],
      ],
    };
    actions.ensureDiscardMasks(state);
    assert.deepEqual(state.discardMasks, [
      [0, 1, 0],
      [1, 1],
    ]);
  });

  test("zeros a grid whose bad-channel list has the wrong length", () => {
    state.channelMeans = [[1, 1, 1]];
    state.metadata = { bad_channels_per_grid: [[1, 1]] };
    actions.ensureDiscardMasks(state);
    assert.deepEqual(state.discardMasks, [[0, 0, 0]]);
  });

  test("keeps existing masks when the grid count matches", () => {
    state.channelMeans = [[1, 1]];
    state.discardMasks = [[1, 0]];
    state.metadata = { bad_channels_per_grid: [[0, 1]] };
    actions.ensureDiscardMasks(state);
    assert.deepEqual(state.discardMasks, [[1, 0]]);
  });

  test("does nothing without channel means", () => {
    state.discardMasks = [[1]];
    actions.ensureDiscardMasks(state);
    assert.deepEqual(state.discardMasks, [[1]]);
  });
});

describe("setGridNames", () => {
  test("clamps a current grid past the end", () => {
    state.currentGrid = 5;
    actions.setGridNames(state, ["a", "b"]);
    assert.equal(state.currentGrid, 1);
  });

  test("resets a negative or non-finite current grid", () => {
    state.currentGrid = -2;
    actions.setGridNames(state, ["a", "b"]);
    assert.equal(state.currentGrid, 0);
    state.currentGrid = NaN;
    actions.setGridNames(state, ["a", "b"]);
    assert.equal(state.currentGrid, 0);
  });

  test("a non-array clears the names and the current grid", () => {
    state.currentGrid = 3;
    actions.setGridNames(state, null);
    assert.deepEqual(state.gridNames, []);
    assert.equal(state.currentGrid, 0);
  });
});

describe("ROIs and artifact regions", () => {
  test("setRoiForIndex pads earlier slots and ignores negative indices", () => {
    actions.setRoiForIndex(state, 2, { start: 10, end: 20, extra: true });
    assert.deepEqual(state.rois, [
      { start: 0, end: 0 },
      { start: 0, end: 0 },
      { start: 10, end: 20 },
    ]);
    actions.setRoiForIndex(state, -1, { start: 1, end: 2 });
    assert.equal(state.rois.length, 3);
  });

  test("setArtifactRegions stores the spans, or none", () => {
    actions.setArtifactRegions(state, [{ start: 1, end: 2 }]);
    assert.deepEqual(state.artifactRegions, [{ start: 1, end: 2 }]);
    actions.setArtifactRegions(state, null);
    assert.deepEqual(state.artifactRegions, []);
  });

  test("addArtifactRegion skips non-finite bounds", () => {
    actions.addArtifactRegion(state, { start: 1, end: 2 });
    actions.addArtifactRegion(state, { start: 1, end: "x" });
    assert.deepEqual(state.artifactRegions, [{ start: 1, end: 2 }]);
  });

  test("removeLastArtifactRegion reports whether it removed anything", () => {
    state.artifactRegions = [{ start: 1, end: 2 }];
    assert.equal(actions.removeLastArtifactRegion(state), true);
    assert.equal(actions.removeLastArtifactRegion(state), false);
  });
});

describe("discard masks", () => {
  test("setDiscardMasks coerces to 0/1 and replaces non-array grids", () => {
    actions.setDiscardMasks(state, [[true, 0, 2], "bad"]);
    assert.deepEqual(state.discardMasks, [[1, 0, 1], []]);
  });

  test("setDiscardMasks ignores a non-array", () => {
    state.discardMasks = [[1]];
    actions.setDiscardMasks(state, null);
    assert.deepEqual(state.discardMasks, [[1]]);
  });

  test("setDiscardMaskChannel creates a missing grid row", () => {
    state.discardMasks = [];
    actions.setDiscardMaskChannel(state, 1, 2, true);
    assert.equal(state.discardMasks[1][2], 1);
  });
});

describe("edit slice", () => {
  test("appendEditMu keeps the parallel arrays aligned and copies its inputs", () => {
    const distimes = [10, 20];
    const pulseTrain = [0, 1, 0];
    actions.appendEditMu(state, {
      distimes,
      pulseTrain,
      gridIdx: 1,
      uid: "mu-a",
    });
    const e = state.edit;
    for (const key of [
      "distimes",
      "pulseTrains",
      "originalDistimes",
      "originalPulseTrains",
      "muGridIndex",
      "flagged",
      "muUids",
      "artifactTimes",
    ]) {
      assert.equal(e[key].length, 1, key);
    }
    assert.equal(e.flagged[0], false);
    assert.equal(e.muUids[0], "mu-a");
    distimes.push(30);
    e.distimes[0].push(99);
    assert.deepEqual(e.originalDistimes[0], [10, 20]);
    assert.notEqual(e.pulseTrains[0], e.originalPulseTrains[0]);
  });

  test("dropEditHistoryForMuSince keeps older entries and other MUs", () => {
    state.edit.editHistory = [
      { mu_uid: "a", op: 1 },
      { mu_uid: "b", op: 2 },
      { mu_uid: "a", op: 3 },
      { mu_uid: "b", op: 4 },
      { mu_uid: "a", op: 5 },
    ];
    actions.dropEditHistoryForMuSince(state, "a", 2);
    assert.deepEqual(
      state.edit.editHistory.map((e) => e.op),
      [1, 2, 4],
    );
  });

  test("keepEditMus reorders every per-MU array together", () => {
    Object.assign(state.edit, {
      distimes: [[0], [1], [2]],
      originalDistimes: [[10], [11], [12]],
      pulseTrains: [[100], [101], [102]],
      originalPulseTrains: [[200], [201], [202]],
      muGridIndex: [0, 1, 0],
      flagged: [false, true, false],
      muUids: ["u0", "u1", "u2"],
      artifactTimes: [[30], [31], [32]],
      currentMu: 2,
      bookmarkPosition: { muIdx: 1, position: 50 },
      backup: { muIdx: 2 },
    });
    actions.keepEditMus(state, [2, 1]);
    const e = state.edit;
    assert.deepEqual(e.distimes, [[2], [1]]);
    assert.deepEqual(e.originalDistimes, [[12], [11]]);
    assert.deepEqual(e.pulseTrains, [[102], [101]]);
    assert.deepEqual(e.originalPulseTrains, [[202], [201]]);
    assert.deepEqual(e.muGridIndex, [0, 1]);
    assert.deepEqual(e.flagged, [false, true]);
    assert.deepEqual(e.muUids, ["u2", "u1"]);
    assert.deepEqual(e.artifactTimes, [[32], [31]]);
    assert.equal(e.currentMu, 0);
    assert.deepEqual(e.bookmarkPosition, { muIdx: 1, position: 50 });
    assert.equal(e.backup, null);
  });

  test("keepEditMus falls back to MU 0 and drops a bookmark on a removed MU", () => {
    Object.assign(state.edit, {
      distimes: [[0], [1]],
      muUids: ["u0", "u1"],
      currentMu: 1,
      bookmarkPosition: { muIdx: 1, position: 5 },
    });
    actions.keepEditMus(state, [0]);
    assert.equal(state.edit.currentMu, 0);
    assert.equal(state.edit.bookmarkPosition, null);
  });

  test("per-MU spike and artifact times are coerced and filtered", () => {
    actions.setEditDistimesForMu(state, 0, ["5", 6, "x", NaN]);
    actions.setEditArtifactTimesForMu(state, 1, [1, "2", undefined]);
    assert.deepEqual(state.edit.distimes[0], [5, 6]);
    assert.deepEqual(state.edit.artifactTimes[1], [1, 2]);
  });

  test("resetEditSlice returns a fresh slice", () => {
    state.edit.dirty = true;
    state.edit.pulseTrains.push([1]);
    actions.resetEditSlice(state);
    assert.deepEqual(state.edit, pristine.edit);
  });
});

describe("setFsamp", () => {
  test("keeps positive numbers and nulls everything else", () => {
    actions.setFsamp(state, "2048");
    assert.equal(state.fsamp, 2048);
    for (const bad of [0, -1, "x", undefined]) {
      actions.setFsamp(state, bad);
      assert.equal(state.fsamp, null, String(bad));
    }
  });
});

describe("selectors", () => {
  test("MU indices without a grid mapping belong to every grid", () => {
    state.muPulseTrains = [[1], [2], [3]];
    state.muGridIndex = [];
    assert.deepEqual(selectors.getRunMuIndicesForGrid(state, 4), [0, 1, 2]);
  });

  test("MU indices filter by grid, matching numeric strings", () => {
    state.edit.pulseTrains = [[1], [2], [3], [4]];
    state.edit.muGridIndex = [0, "1", 1, 0];
    assert.deepEqual(selectors.getEditMuIndicesForGrid(state, 1), [1, 2]);
    assert.deepEqual(selectors.getEditMuIndicesForGrid(state, "0"), [0, 3]);
  });

  test("no pulse trains means no MU indices", () => {
    state.muPulseTrains = [];
    state.muGridIndex = [0, 1];
    assert.deepEqual(selectors.getRunMuIndicesForGrid(state, 0), []);
  });

  test("muUidFor falls back to a positional id", () => {
    state.edit.muUids = ["x"];
    assert.equal(selectors.muUidFor(state, 0), "x");
    assert.equal(selectors.muUidFor(state, 3), "mu3");
  });

  test("ROI bounds use the fallback when not finite", () => {
    assert.equal(selectors.roiStart({ start: 5 }), 5);
    assert.equal(selectors.roiStart({ start: NaN }, 7), 7);
    assert.equal(selectors.roiEnd(null, 9), 9);
    assert.equal(selectors.roiEnd({ end: 0 }, 9), 0);
  });

  test("getCurrentGrid defaults to 0", () => {
    state.currentGrid = undefined;
    assert.equal(selectors.getCurrentGrid(state), 0);
  });
});

describe("raw preview transitions", () => {
  test("begin resets the edit slice but keeps the BIDS root", () => {
    state.edit.bidsRoot = "/data/bids";
    state.edit.dirty = true;
    state.uploadToken = "old";
    state.channelTraces = [[1]];
    state.qcWindowLoading = { 0: true };
    state.discardMasks = [[1]];
    const file = { name: "new.otb+" };
    beginRawPreviewTransition(state, file);
    assert.deepEqual(state.edit, { ...pristine.edit, bidsRoot: "/data/bids" });
    assert.equal(state.file, file);
    assert.equal(state.uploadToken, null);
    assert.deepEqual(state.channelTraces, []);
    assert.deepEqual(state.qcWindowLoading, {});
    assert.deepEqual(state.discardMasks, []);
  });

  test("rollback clears the file, token and preview", () => {
    state.file = { name: "x" };
    state.uploadToken = "t";
    state.previewSeries = [1];
    state.gridNames = ["g"];
    state.seriesLength = 10;
    rollbackRawPreviewTransition(state);
    assert.equal(state.file, null);
    assert.equal(state.uploadToken, null);
    assert.deepEqual(state.previewSeries, []);
    assert.deepEqual(state.gridNames, []);
    assert.equal(state.seriesLength, null);
  });
});
