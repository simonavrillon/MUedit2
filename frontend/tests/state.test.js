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

const ints = (...values) => Int32Array.from(values);
const rows = (list) => list.map((r) => Array.from(r));

describe("edit slice", () => {
  /** A session frame as `decodeEditSessionFrame` returns it. */
  function sessionFrame() {
    return {
      meta: {
        token: "tok",
        n_mu: 2,
        flagged: [false, true],
        mu_uids: ["g0_mu0", "g0_mu1"],
        mu_grid_index: [0, 1],
        versions: [3, 4],
        has_pulse: [true, true],
        dirty: false,
        can_undo: false,
        edit_history: [{ type: "flag_mu", mu_uid: "g0_mu1" }],
        fsamp: 2048,
        total_samples: 5000,
        parameters: { duplicatesthresh: 0.3 },
      },
      spikes: [ints(10, 20), ints(30)],
      artifacts: [ints(), ints(31)],
    };
  }

  test("setEditSession takes over the whole session", () => {
    state.edit.pulseView = { mu: 0 };
    actions.setEditSession(state, sessionFrame());
    const e = state.edit;
    assert.equal(e.token, "tok");
    assert.deepEqual(rows(e.distimes), [[10, 20], [30]]);
    assert.deepEqual(rows(e.artifactTimes), [[], [31]]);
    assert.deepEqual(e.flagged, [false, true]);
    assert.deepEqual(e.versions, [3, 4]);
    assert.equal(e.totalSamples, 5000);
    assert.equal(e.fsamp, 2048);
    assert.equal(e.editHistory.length, 1);
    assert.equal(e.pulseView, null);
  });

  test("applyEditChange replaces the changed MU and the new log tail", () => {
    actions.setEditSession(state, sessionFrame());
    actions.applyEditChange(state, {
      meta: {
        n_mu: 2,
        changed: [0],
        flagged: [false, true],
        mu_uids: ["g0_mu0", "g0_mu1"],
        mu_grid_index: [0, 1],
        versions: [9, 4],
        has_pulse: [true, true],
        dirty: true,
        can_undo: true,
        history_start: 1,
        history: [{ type: "delete_spikes", mu_uid: "g0_mu0" }],
      },
      spikes: [ints(10)],
      artifacts: [ints()],
    });
    const e = state.edit;
    assert.deepEqual(rows(e.distimes), [[10], [30]]);
    assert.deepEqual(e.versions, [9, 4]);
    assert.equal(e.dirty, true);
    assert.equal(e.canUndo, true);
    assert.deepEqual(
      e.editHistory.map((h) => h.type),
      ["flag_mu", "delete_spikes"],
    );
  });

  test("applyEditChange appends a duplicate and cuts the log on undo", () => {
    actions.setEditSession(state, sessionFrame());
    const perMu = (n) => ({
      n_mu: n,
      flagged: new Array(n).fill(false),
      mu_uids: ["a", "b", "c"].slice(0, n),
      mu_grid_index: new Array(n).fill(0),
      versions: new Array(n).fill(1),
      has_pulse: new Array(n).fill(true),
    });
    actions.applyEditChange(state, {
      meta: {
        ...perMu(3),
        changed: [2],
        history_start: 1,
        history: [{ type: "duplicate_mu" }],
      },
      spikes: [ints(30)],
      artifacts: [ints()],
    });
    assert.deepEqual(rows(state.edit.distimes), [[10, 20], [30], [30]]);
    actions.applyEditChange(state, {
      meta: {
        ...perMu(2),
        changed: [],
        kept_indices: [0, 1],
        history_start: 1,
        history: [],
      },
      spikes: [],
      artifacts: [],
    });
    assert.deepEqual(rows(state.edit.distimes), [[10, 20], [30]]);
    assert.equal(state.edit.editHistory.length, 1);
  });

  test("keepEditMus reorders every per-MU array together", () => {
    Object.assign(state.edit, {
      distimes: [ints(0), ints(1), ints(2)],
      muGridIndex: [0, 1, 0],
      flagged: [false, true, false],
      muUids: ["u0", "u1", "u2"],
      artifactTimes: [ints(30), ints(31), ints(32)],
      versions: [5, 6, 7],
      hasPulse: [true, false, true],
      currentMu: 2,
      bookmarkPosition: { muIdx: 1, position: 50 },
    });
    actions.keepEditMus(state, [2, 1]);
    const e = state.edit;
    assert.deepEqual(rows(e.distimes), [[2], [1]]);
    assert.deepEqual(e.muGridIndex, [0, 1]);
    assert.deepEqual(e.flagged, [false, true]);
    assert.deepEqual(e.muUids, ["u2", "u1"]);
    assert.deepEqual(rows(e.artifactTimes), [[32], [31]]);
    assert.deepEqual(e.versions, [7, 6]);
    assert.deepEqual(e.hasPulse, [true, false]);
    assert.equal(e.currentMu, 0);
    assert.deepEqual(e.bookmarkPosition, { muIdx: 1, position: 50 });
  });

  test("keepEditMus falls back to MU 0 and drops a bookmark on a removed MU", () => {
    Object.assign(state.edit, {
      distimes: [ints(0), ints(1)],
      muUids: ["u0", "u1"],
      currentMu: 1,
      bookmarkPosition: { muIdx: 1, position: 5 },
    });
    actions.keepEditMus(state, [0]);
    assert.equal(state.edit.currentMu, 0);
    assert.equal(state.edit.bookmarkPosition, null);
  });

  test("applyEditSave mirrors the MUs the file kept", () => {
    actions.setEditSession(state, sessionFrame());
    actions.applyEditSave(state, {
      kept_indices: [0],
      n_mu: 1,
      flagged: [false],
      mu_uids: ["g0_mu0"],
      mu_grid_index: [0],
      versions: [3],
      has_pulse: [true],
      dirty: false,
      can_undo: false,
      edit_history: [{ type: "remove_flagged", on_save: true }],
    });
    assert.deepEqual(rows(state.edit.distimes), [[10, 20]]);
    assert.deepEqual(state.edit.muUids, ["g0_mu0"]);
    assert.equal(state.edit.editHistory[0].type, "remove_flagged");
  });

  test("resetEditSlice returns a fresh slice", () => {
    state.edit.dirty = true;
    state.edit.distimes.push(ints(1));
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
    state.muDistimes = [ints(1), ints(2), ints(3)];
    state.muGridIndex = [];
    assert.deepEqual(selectors.getRunMuIndicesForGrid(state, 4), [0, 1, 2]);
  });

  test("MU indices filter by grid, matching numeric strings", () => {
    state.edit.distimes = [ints(1), ints(2), ints(3), ints(4)];
    state.edit.muGridIndex = [0, "1", 1, 0];
    assert.deepEqual(selectors.getEditMuIndicesForGrid(state, 1), [1, 2]);
    assert.deepEqual(selectors.getEditMuIndicesForGrid(state, "0"), [0, 3]);
  });

  test("no MUs means no MU indices", () => {
    state.muDistimes = [];
    state.muGridIndex = [0, 1];
    assert.deepEqual(selectors.getRunMuIndicesForGrid(state, 0), []);
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
    state.gridSeries = [new Float32Array([1])];
    state.gridNames = ["g"];
    state.seriesLength = 10;
    rollbackRawPreviewTransition(state);
    assert.equal(state.file, null);
    assert.equal(state.uploadToken, null);
    assert.deepEqual(state.gridSeries, []);
    assert.deepEqual(state.gridNames, []);
    assert.equal(state.seriesLength, null);
  });
});
