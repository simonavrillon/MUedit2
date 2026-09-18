// Edit-stage operations: pixel-to-value selection mapping, undo, duplicate
// and reset, driven through the real app context.
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
const { createApp } = await import("../src/app/create-app.js");
const ops = await import("../src/editing/operations.js");

const pristine = structuredClone(initialState);
const PLOT_HEIGHT = 100;

function recorder() {
  const calls = [];
  const fn = (...args) => calls.push(args);
  fn.calls = calls;
  return fn;
}

function assertClose(actual, expected, message) {
  assert.ok(
    Math.abs(actual - expected) < 1e-9,
    `${message ?? "value"}: expected ${expected}, got ${actual}`,
  );
}

/** Two MUs on one grid; pulse values 0..9 so min 0, max 9, span 9. */
function editState() {
  const state = structuredClone(pristine);
  const ramp = Array.from({ length: 10 }, (_, i) => i);
  Object.assign(state.edit, {
    pulseTrains: [[...ramp], [...ramp]],
    originalPulseTrains: [[...ramp], [...ramp]],
    distimes: [
      [2, 5, 8],
      [1, 4],
    ],
    originalDistimes: [
      [2, 5, 8],
      [1, 4],
    ],
    artifactTimes: [[], []],
    flagged: [false, false],
    muUids: ["g0_mu0", "g0_mu1"],
    muGridIndex: [0, 0],
    gridNames: ["GR08MM1305"],
    fsamp: 2000,
    totalSamples: 10,
  });
  return state;
}

function testApp(state) {
  const app = createApp({ state, els: {}, api: {} });
  Object.assign(app, {
    setEditStatus: recorder(),
    renderEditExplorer: recorder(),
    requestRoiEdit: recorder(),
    getPulsePlotHeight: () => PLOT_HEIGHT,
    getDrPlotHeight: () => PLOT_HEIGHT,
  });
  return app;
}

let state;
let app;

beforeEach(() => {
  state = editState();
  app = testApp(state);
});

describe("flags and display pulse", () => {
  test("ensureEditFlagged seeds one false per MU", () => {
    state.edit.flagged = [];
    ops.ensureEditFlagged(state);
    assert.deepEqual(state.edit.flagged, [false, false]);
  });

  test("ensureEditFlagged keeps flags when the MU count matches", () => {
    state.edit.flagged = [true, false];
    ops.ensureEditFlagged(state);
    assert.deepEqual(state.edit.flagged, [true, false]);
  });

  test("a flagged MU displays as zeros of the same length", () => {
    state.edit.flagged = [false, true];
    assert.deepEqual(ops.getDisplayPulse(state, 1), new Array(10).fill(0));
    assert.deepEqual(ops.getDisplayPulse(state, 0), state.edit.pulseTrains[0]);
  });

  test("a missing MU has an empty raw pulse", () => {
    assert.deepEqual(ops.getRawPulse(state, 7), []);
  });
});

describe("totals and dirty tracking", () => {
  test("total samples prefer the stored total, then the first pulse", () => {
    state.edit.totalSamples = 4096;
    assert.equal(ops.getEditTotalSamples(state), 4096);
    state.edit.totalSamples = 0;
    assert.equal(ops.getEditTotalSamples(state), 10);
    state.edit.pulseTrains = [];
    assert.equal(ops.getEditTotalSamples(state), 0);
  });

  test("dirty only when discharge times differ from the baseline", () => {
    ops.recomputeEditDirty(state);
    assert.equal(state.edit.dirty, false);
    state.edit.distimes[1] = [1, 4, 6];
    ops.recomputeEditDirty(state);
    assert.equal(state.edit.dirty, true);
  });
});

describe("getPulseViewMeta", () => {
  test("a null view spans the whole pulse", () => {
    const meta = ops.getPulseViewMeta(state);
    assert.equal(meta.s, 0);
    assert.equal(meta.e, 10);
    assert.equal(meta.minVal, 0);
    assert.equal(meta.maxVal, 9);
    assert.equal(meta.span, 9);
  });

  test("the view is clamped to the pulse and at least one sample wide", () => {
    state.edit.view = { start: -50, end: 500 };
    let meta = ops.getPulseViewMeta(state);
    assert.deepEqual([meta.s, meta.e], [0, 10]);

    state.edit.view = { start: 4, end: 4 };
    meta = ops.getPulseViewMeta(state);
    assert.deepEqual([meta.s, meta.e], [4, 5]);
  });

  test("a flat slice has a span of 1, not 0", () => {
    state.edit.pulseTrains[0] = new Array(10).fill(3);
    const meta = ops.getPulseViewMeta(state);
    assert.equal(meta.span, 1);
  });

  test("a ten-minute recording at 2048 Hz does not overflow the stack", () => {
    const n = 2048 * 600;
    const pulse = new Array(n).fill(0.5);
    pulse[123] = -2;
    pulse[n - 1] = 7;
    state.edit.pulseTrains[0] = pulse;
    const meta = ops.getPulseViewMeta(state);
    assert.equal(meta.minVal, -2);
    assert.equal(meta.maxVal, 7);
    assert.equal(meta.span, 9);
  });
});

describe("buildEditDropdownModel", () => {
  const byGrid = (lists) => (g) => lists[g] || [];

  test("keeps the current grid and MU when both are valid", () => {
    state.edit.currentMu = 1;
    const model = ops.buildEditDropdownModel(state, byGrid([[0, 1]]));
    assert.equal(model.targetGrid, 0);
    assert.equal(model.currentMu, 1);
    assert.equal(model.needsGridSwitch, false);
    assert.ok(!model.needsMuSwitch);
  });

  test("switches to the grid's first MU when the current one is elsewhere", () => {
    state.edit.currentMu = 5;
    const model = ops.buildEditDropdownModel(state, byGrid([[2, 3]]));
    assert.equal(model.currentMu, 2);
    assert.ok(model.needsMuSwitch);
  });

  test("falls through to the first grid that has MUs", () => {
    state.edit.gridNames = ["A", "B", "C"];
    state.edit.currentMuGrid = 0;
    const model = ops.buildEditDropdownModel(state, byGrid([[], [], [4]]));
    assert.equal(model.targetGrid, 2);
    assert.deepEqual(model.muOptions, [4]);
    assert.equal(model.needsGridSwitch, true);
    assert.equal(model.currentMu, 4);
  });

  test("no MUs anywhere asks for no switch", () => {
    state.edit.gridNames = ["A", "B"];
    const model = ops.buildEditDropdownModel(state, byGrid([]));
    assert.equal(model.targetGrid, 0);
    assert.deepEqual(model.muOptions, []);
    assert.ok(!model.needsMuSwitch);
  });
});

describe("computeInstantaneousDr", () => {
  test("places each rate at the midpoint of its interval", () => {
    const { series, markers, markerVals } = ops.computeInstantaneousDr(
      [100, 300, 700],
      2000,
      1000,
    );
    assert.deepEqual(markers, [200, 500]);
    assert.deepEqual(markerVals, [10, 5]);
    assert.equal(series.length, 1000);
    assert.equal(series[200], 10);
    assert.equal(series[500], 5);
    assert.equal(
      series.filter((v) => v !== 0).length,
      2,
      "only the midpoints are set",
    );
  });

  test("skips repeated discharge times", () => {
    const { markers } = ops.computeInstantaneousDr([100, 100, 300], 2000, 1000);
    assert.deepEqual(markers, [200]);
  });

  test("clamps a midpoint past the end to the last sample", () => {
    const { markers } = ops.computeInstantaneousDr([990, 1010], 2000, 1000);
    assert.deepEqual(markers, [999]);
  });

  test("an unknown sampling rate gives zero rates", () => {
    const { markerVals } = ops.computeInstantaneousDr([0, 200], 0, 1000);
    assert.deepEqual(markerVals, [0]);
  });
});

describe("selection to ROI request", () => {
  test("add-spikes maps the lower pixel edge to a pulse threshold", () => {
    app.addSpikesInSelection({ start: 2, end: 6, yMin: 20, yMax: 60 });
    const [[action, payload]] = app.requestRoiEdit.calls;
    assert.equal(action, "add-spikes");
    assert.equal(payload.muIdx, 0);
    assert.equal(payload.xStart, 2);
    assert.equal(payload.xEnd, 6);
    assert.equal(payload.fs, 2000);
    // 60 px down a 100 px plot is 40% up a 0..9 range.
    assertClose(payload.yMin, 3.6, "yMin");
  });

  test("a selection without a height spans the full plot", () => {
    app.addSpikesInSelection({ start: 3, end: 7 });
    const [[, payload]] = app.requestRoiEdit.calls;
    assert.deepEqual([payload.xStart, payload.xEnd], [3, 7]);
    assert.equal(payload.yMin, 0);
  });

  test("the selection is clamped to the visible window and plot", () => {
    state.edit.view = { start: 3, end: 8 };
    app.addSpikesInSelection({ start: 0, end: 20, yMin: -10, yMax: 150 });
    const [[, payload]] = app.requestRoiEdit.calls;
    assert.deepEqual([payload.xStart, payload.xEnd], [3, 8]);
    assert.equal(payload.yMin, 3, "the bottom of the 3..7 window");
  });

  test("add-spikes takes a backup before requesting", () => {
    state.edit.currentMu = 1;
    app.addSpikesInSelection({ start: 2, end: 6 });
    assert.equal(state.edit.backup.muIdx, 1);
    assert.deepEqual(state.edit.backup.distimes, [1, 4]);
  });

  test("an MU with no pulse sends nothing and takes no backup", () => {
    state.edit.pulseTrains[0] = [];
    app.addSpikesInSelection({ start: 2, end: 6 });
    assert.equal(app.requestRoiEdit.calls.length, 0);
    assert.equal(state.edit.backup, null);
  });

  test("add-artifact uses the same mapping under its own action", () => {
    app.addArtifactInSelection({ start: 2, end: 6, yMin: 20, yMax: 60 });
    const [[action, payload]] = app.requestRoiEdit.calls;
    assert.equal(action, "add-artifact");
    assertClose(payload.yMin, 3.6, "yMin");
  });

  test("delete-spikes sends a value band whichever way the box was drawn", () => {
    state.edit.artifactTimes[0] = [4];
    for (const sel of [
      { start: 2, end: 6, yMin: 20, yMax: 60 },
      { start: 2, end: 6, yMin: 60, yMax: 20 },
    ]) {
      app.requestRoiEdit.calls.length = 0;
      app.deleteSpikesInSelection(sel);
      const [[action, payload]] = app.requestRoiEdit.calls;
      assert.equal(action, "delete-spikes");
      assertClose(payload.yMin, 3.6, "yMin");
      assertClose(payload.yMax, 7.2, "yMax");
      assert.deepEqual(payload.artifact_times, [4]);
    }
  });

  test("delete-dr scales the plot to the fastest discharge rate", () => {
    state.edit.distimes[0] = [0, 200, 400, 1000];
    app.deleteDrInSelection({ start: 900, end: 100, yMin: 80, yMax: 30 });
    const [[action, payload]] = app.requestRoiEdit.calls;
    assert.equal(action, "delete-dr");
    assert.deepEqual([payload.xStart, payload.xEnd], [100, 900]);
    // Peak rate 2000/200 = 10 Hz; 80 px down a 100 px plot is 2 Hz.
    assertClose(payload.yMin, 2, "yMin");
    assert.equal(payload.fs, 2000);
  });

  test("delete-dr assumes 2000 Hz when the rate is unknown", () => {
    state.edit.fsamp = null;
    state.edit.distimes[0] = [0, 100];
    app.deleteDrInSelection({ start: 0, end: 100, yMin: 0, yMax: 50 });
    const [[, payload]] = app.requestRoiEdit.calls;
    assert.equal(payload.fs, 2000);
    assertClose(payload.yMin, 10, "half of 20 Hz");
  });

  test("delete-dr needs two discharges", () => {
    state.edit.distimes[0] = [5];
    app.deleteDrInSelection({ start: 0, end: 9 });
    assert.equal(app.requestRoiEdit.calls.length, 0);
  });
});

describe("undo", () => {
  test("restores the backed-up MU and drops only its entries since the backup", () => {
    state.edit.currentMu = 1;
    state.edit.editHistory = [
      { mu_uid: "g0_mu1", type: "first" },
      { mu_uid: "g0_mu0", type: "other" },
    ];
    app.backupEditMu();
    state.edit.editHistory.push(
      { mu_uid: "g0_mu1", type: "delete_spikes" },
      { mu_uid: "g0_mu2", type: "duplicate_mu" },
      { mu_uid: "g0_mu1", type: "delete_artifact" },
    );

    state.edit.distimes[1] = [9];
    state.edit.artifactTimes[1] = [3];
    state.edit.flagged[1] = true;
    state.edit.pulseTrains[1] = new Array(10).fill(0);
    state.edit.selectionPulse = { start: 1, end: 2 };

    app.restoreEditBackup();

    assert.deepEqual(state.edit.distimes[1], [1, 4]);
    assert.deepEqual(state.edit.artifactTimes[1], []);
    assert.equal(state.edit.flagged[1], false);
    assert.deepEqual(
      state.edit.pulseTrains[1],
      state.edit.originalPulseTrains[1],
    );
    assert.deepEqual(
      state.edit.editHistory.map((e) => e.type),
      ["first", "other", "duplicate_mu"],
    );
    assert.equal(state.edit.backup, null);
    assert.equal(state.edit.selectionPulse, null);
    assert.equal(state.edit.dirty, false);
    assert.equal(app.renderEditExplorer.calls.length, 1);
    assert.deepEqual(app.setEditStatus.calls.at(-1), [
      "Undo applied",
      "success",
    ]);
  });

  test("undoing an edit that logged nothing keeps earlier entries", () => {
    state.edit.editHistory = [{ mu_uid: "g0_mu0", type: "remove_outliers" }];
    app.backupEditMu();
    app.restoreEditBackup();
    assert.deepEqual(
      state.edit.editHistory.map((e) => e.type),
      ["remove_outliers"],
    );
  });

  test("the backup is a copy, not a view of the live arrays", () => {
    app.backupEditMu();
    state.edit.distimes[0].push(9);
    assert.deepEqual(state.edit.backup.distimes, [2, 5, 8]);
  });

  test("nothing to undo says so and changes nothing", () => {
    app.restoreEditBackup();
    assert.deepEqual(app.setEditStatus.calls, [["Nothing to undo", "muted"]]);
    assert.equal(app.renderEditExplorer.calls.length, 0);
  });
});

describe("duplicateMu", () => {
  test("numbers the copy after the highest uid on its grid", () => {
    state.edit.muGridIndex = [0, 1];
    state.edit.muUids = ["g0_mu0", "g1_mu3"];
    state.edit.currentMu = 1;

    app.duplicateMu();

    assert.equal(state.edit.muUids[2], "g1_mu4");
    assert.equal(state.edit.muGridIndex[2], 1);
    assert.equal(state.edit.currentMuGrid, 1);
    assert.equal(state.edit.currentMu, 2);
    const entry = state.edit.editHistory.at(-1);
    assert.equal(entry.type, "duplicate_mu");
    assert.equal(entry.mu_uid, "g1_mu4");
    assert.equal(entry.source_mu_uid, "g1_mu3");
    assert.ok(entry.timestamp);
    assert.deepEqual(app.setEditStatus.calls.at(-1), [
      "MU duplicated — now editing MU 3",
      "success",
    ]);
  });

  test("uid numbering ignores other grids and unparseable suffixes", () => {
    state.edit.muUids = ["g0_mu1", "g0_muX", "g10_mu9"];
    state.edit.muGridIndex = [0, 0, 10];
    state.edit.distimes.push([3]);
    state.edit.pulseTrains.push(new Array(10).fill(1));
    app.duplicateMu();
    assert.equal(state.edit.muUids.at(-1), "g0_mu2");
  });

  test("the copy's discharge times are independent of the source", () => {
    app.duplicateMu();
    state.edit.distimes[0].push(9);
    assert.deepEqual(state.edit.distimes[2], [2, 5, 8]);
    assert.equal(state.edit.flagged.length, 3);
  });

  test("never reuses the uid of an MU the log says was removed", () => {
    state.edit.editHistory = [
      { type: "remove_duplicates", removed_mu_uids: ["g0_mu5"] },
      { type: "flag_mu", mu_uid: "g0_mu7", flagged: true },
    ];
    app.duplicateMu();
    assert.equal(state.edit.muUids.at(-1), "g0_mu8");
  });

  test("an MU with no pulse is not duplicated", () => {
    state.edit.pulseTrains[0] = [];
    app.duplicateMu();
    assert.equal(state.edit.distimes.length, 2);
    assert.deepEqual(app.setEditStatus.calls, [["No MU loaded", "muted"]]);
  });
});

describe("resetCurrentMuEdits", () => {
  test("returns the MU to its loaded state and logs what the reset undid", () => {
    state.edit.distimes[0] = [7];
    state.edit.artifactTimes[0] = [5];
    state.edit.flagged[0] = true;
    state.edit.pulseTrains[0] = new Array(10).fill(0);
    state.edit.backup = { muIdx: 0 };
    state.edit.editHistory = [
      { mu_uid: "g0_mu0", type: "add" },
      { mu_uid: "g0_mu1", type: "keep" },
    ];

    app.resetCurrentMuEdits();

    assert.deepEqual(state.edit.distimes[0], [2, 5, 8]);
    assert.deepEqual(state.edit.artifactTimes[0], []);
    assert.equal(state.edit.flagged[0], false);
    assert.deepEqual(
      state.edit.pulseTrains[0],
      state.edit.originalPulseTrains[0],
    );
    assert.deepEqual(
      state.edit.editHistory.map((e) => e.type),
      ["add", "keep", "reset_mu"],
    );
    const { timestamp, ...entry } = state.edit.editHistory.at(-1);
    assert.ok(timestamp);
    assert.deepEqual(entry, {
      type: "reset_mu",
      mu_uid: "g0_mu0",
      spikes_added: [2, 5, 8],
      spikes_removed: [7],
      artifacts_removed: [5],
      flagged: false,
    });
    assert.equal(state.edit.backup, null);
    assert.equal(app.renderEditExplorer.calls.length, 1);
  });

  test("resetting a duplicated MU keeps the record that it was duplicated", () => {
    app.duplicateMu();
    app.resetCurrentMuEdits();
    assert.deepEqual(
      state.edit.editHistory.map((e) => [e.type, e.mu_uid]),
      [
        ["duplicate_mu", "g0_mu2"],
        ["reset_mu", "g0_mu2"],
      ],
    );
  });

  test("an MU with no baseline is left alone", () => {
    state.edit.originalDistimes = [];
    state.edit.distimes[0] = [7];
    app.resetCurrentMuEdits();
    assert.deepEqual(state.edit.distimes[0], [7]);
    assert.equal(app.renderEditExplorer.calls.length, 0);
  });
});

describe("removeDuplicateMus", () => {
  /** Three MUs: 0 and 2 are duplicates and the backend keeps 2. */
  function threeMus() {
    state.edit.distimes.push([2, 5, 9]);
    state.edit.originalDistimes.push([2, 5, 9]);
    state.edit.pulseTrains.push(new Array(10).fill(2));
    state.edit.originalPulseTrains.push(new Array(10).fill(2));
    state.edit.artifactTimes.push([7]);
    state.edit.flagged.push(false);
    state.edit.muUids.push("g0_mu2");
    state.edit.muGridIndex.push(0);
    app.api = {
      editRemoveDuplicates: async () => ({
        kept_indices: [1, 2],
        distimes: [
          [1, 4],
          [2, 5, 9],
        ],
        removed_count: 1,
      }),
    };
  }

  test("logs the uid of the MU that was actually removed", async () => {
    threeMus();
    await app.removeDuplicateMus();
    const { timestamp, ...entry } = state.edit.editHistory.at(-1);
    assert.ok(timestamp);
    assert.deepEqual(entry, {
      type: "remove_duplicates",
      removed_count: 1,
      removed_mu_uids: ["g0_mu0"],
    });
  });

  test("keeps uids aligned with the surviving spike trains", async () => {
    threeMus();
    await app.removeDuplicateMus();
    assert.deepEqual(state.edit.muUids, ["g0_mu1", "g0_mu2"]);
    assert.deepEqual(state.edit.distimes, [
      [1, 4],
      [2, 5, 9],
    ]);
    assert.deepEqual(state.edit.originalDistimes, state.edit.distimes);
    assert.deepEqual(state.edit.artifactTimes, [[], [7]]);
  });

  test("clears the undo backup, whose MU index no longer applies", async () => {
    threeMus();
    state.edit.currentMu = 2;
    app.backupEditMu();
    await app.removeDuplicateMus();
    assert.equal(state.edit.backup, null);
    assert.equal(state.edit.currentMu, 1);
    app.restoreEditBackup();
    assert.equal(state.edit.distimes.length, 2);
  });
});

describe("saveEditedFile", () => {
  function stubSave(response) {
    const payloads = [];
    Object.assign(app, {
      getBidsMuscleNames: () => [],
      persistNpzBySaveTarget: async (payload) => {
        payloads.push(payload);
        return { mode: "saved", path: "/out.npz", ...response };
      },
    });
    return payloads;
  }

  test("leaves duplicate removal on the backend's default", async () => {
    const payloads = stubSave({});
    await app.saveEditedFile();
    assert.equal("remove_duplicates" in payloads[0], false);
  });

  test("mirrors the MUs and log entries the save removed", async () => {
    state.edit.flagged = [true, false];
    const saveEntry = {
      type: "remove_flagged",
      on_save: true,
      removed_count: 1,
      removed_mu_uids: ["g0_mu0"],
    };
    stubSave({ keptIndices: [1], editHistory: [saveEntry] });
    await app.saveEditedFile();
    assert.deepEqual(state.edit.muUids, ["g0_mu1"]);
    assert.deepEqual(state.edit.distimes, [[1, 4]]);
    assert.deepEqual(state.edit.flagged, [false]);
    assert.deepEqual(state.edit.editHistory, [saveEntry]);
  });
});
