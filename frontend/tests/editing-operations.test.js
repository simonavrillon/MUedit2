// Edit-stage operations: pixel-to-value selection mapping, discharge rates,
// and the edit service's round trips to the server-side session, driven
// through the real app context with a fake API.
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

const { state: initialState } = await import("../src/state/state.js");
const { createApp } = await import("../src/app/create-app.js");
const {
  undoEdit,
  duplicateMu,
  removeDuplicateMus,
  flagMuForDeletion,
  resetCurrentMuEdits,
  saveEditedFile,
} = await import("../src/app/services/editing-service.js");
const ops = await import("../src/editing/operations.js");

const pristine = structuredClone(initialState);
const PLOT_HEIGHT = 100;
const ints = (...values) => Int32Array.from(values);
const rows = (list) => list.map((r) => Array.from(r));

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

/** The fetched window of MU `mu`: samples 0..9, so min 0, max 9, span 9. */
function rampView(mu = 0, version = 1) {
  return {
    mu,
    version,
    start: 0,
    end: 10,
    bins: 254,
    flagged: false,
    row: Float32Array.from({ length: 10 }, (_, i) => i),
    spikes: ints(),
    spikeValues: new Float32Array(0),
    artifacts: ints(),
    artifactValues: new Float32Array(0),
  };
}

/** Two MUs on one grid, the window of MU 0 fetched. */
function editState() {
  const state = structuredClone(pristine);
  Object.assign(state.edit, {
    token: "tok",
    distimes: [ints(2, 5, 8), ints(1, 4)],
    artifactTimes: [ints(), ints()],
    flagged: [false, false],
    muUids: ["g0_mu0", "g0_mu1"],
    muGridIndex: [0, 0],
    versions: [1, 2],
    hasPulse: [true, true],
    gridNames: ["GR08MM1305"],
    fsamp: 2000,
    totalSamples: 10,
    pulseView: rampView(),
  });
  return state;
}

/**
 * A change frame as the server sends it for `state`'s two MUs.
 *
 * @param {object} meta
 * @param {number[][]} [spikes] of the MUs in `meta.changed`
 */
function changeFrame(meta, spikes = []) {
  const n = meta.n_mu ?? 2;
  return {
    meta: {
      n_mu: n,
      flagged: new Array(n).fill(false),
      mu_uids: ["g0_mu0", "g0_mu1", "g0_mu2"].slice(0, n),
      mu_grid_index: new Array(n).fill(0),
      versions: new Array(n).fill(5),
      has_pulse: new Array(n).fill(true),
      dirty: true,
      can_undo: true,
      changed: [],
      history_start: 0,
      history: [],
      ...meta,
    },
    spikes: spikes.map((r) => ints(...r)),
    artifacts: spikes.map(() => ints()),
  };
}

function testApp(state) {
  const app = createApp({ state, els: {}, api: {} });
  Object.assign(app, {
    setEditStatus: recorder(),
    setStatus: recorder(),
    renderEditExplorer: recorder(),
    requestRoiEdit: recorder(),
    getPulsePlotHeight: () => PLOT_HEIGHT,
    getDrPlotHeight: () => PLOT_HEIGHT,
  });
  return app;
}

/** An API whose edit operations answer with `frames[op]` and record requests. */
function fakeApi(frames) {
  const calls = [];
  return {
    calls,
    editOp: async (op, payload) => {
      calls.push([op, payload]);
      const frame = frames[op];
      if (frame instanceof Error) throw frame;
      return frame;
    },
  };
}

let state;
let app;

beforeEach(() => {
  state = editState();
  app = testApp(state);
});

describe("getPulseViewMeta", () => {
  test("a null view spans the whole recording, ranged on the window", () => {
    const meta = ops.getPulseViewMeta(state);
    assert.deepEqual(
      [meta.s, meta.e, meta.minVal, meta.maxVal, meta.span],
      [0, 10, 0, 9, 9],
    );
  });

  test("the view is clamped to the recording and at least one sample wide", () => {
    state.edit.view = { start: -50, end: 500 };
    let meta = ops.getPulseViewMeta(state);
    assert.deepEqual([meta.s, meta.e], [0, 10]);

    state.edit.view = { start: 4, end: 4 };
    meta = ops.getPulseViewMeta(state);
    assert.deepEqual([meta.s, meta.e], [4, 5]);
  });

  test("a flat window has a span of 1, not 0", () => {
    state.edit.pulseView.row = new Float32Array(10).fill(3);
    assert.equal(ops.getPulseViewMeta(state).span, 1);
  });

  test("an envelope is ranged on its bins' extremes", () => {
    state.edit.pulseView.row = {
      min: Float32Array.from([-2, 0.5]),
      max: Float32Array.from([1, 7]),
    };
    const meta = ops.getPulseViewMeta(state);
    assert.deepEqual([meta.minVal, meta.maxVal], [-2, 7]);
  });

  test("null until the window of the MU as it is now has arrived", () => {
    state.edit.versions[0] = 3;
    assert.equal(ops.getPulseViewMeta(state), null);
    state.edit.versions[0] = 1;
    state.edit.currentMu = 1;
    assert.equal(ops.getPulseViewMeta(state), null);
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

describe("dischargeRates", () => {
  test("places each rate at the midpoint of its interval", () => {
    const { positions, rates } = ops.dischargeRates(
      ints(100, 300, 700),
      2000,
      1000,
    );
    assert.deepEqual(positions, [200, 500]);
    assert.deepEqual(rates, [10, 5]);
  });

  test("skips repeated discharge times", () => {
    assert.deepEqual(
      ops.dischargeRates([100, 100, 300], 2000, 1000).positions,
      [200],
    );
  });

  test("clamps a midpoint past the end to the last sample", () => {
    assert.deepEqual(
      ops.dischargeRates([990, 1010], 2000, 1000).positions,
      [999],
    );
  });

  test("an unknown sampling rate gives zero rates", () => {
    assert.deepEqual(ops.dischargeRates([0, 200], 0, 1000).rates, [0]);
  });

  test("the plot's top is the fastest rate in view", () => {
    const dr = ops.dischargeRates([0, 100, 150, 1000], 1000, 2000);
    assert.equal(ops.fastestRateInView(dr, { start: 0, end: 2000 }), 20);
    assert.equal(
      ops.fastestRateInView(dr, { start: 500, end: 2000 }),
      1000 / 850,
    );
    assert.equal(ops.fastestRateInView(dr, { start: 1500, end: 2000 }), 0);
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
    state.edit.pulseView = {
      ...rampView(),
      start: 3,
      end: 8,
      row: Float32Array.from([3, 4, 5, 6, 7]),
    };
    app.addSpikesInSelection({ start: 0, end: 20, yMin: -10, yMax: 150 });
    const [[, payload]] = app.requestRoiEdit.calls;
    assert.deepEqual([payload.xStart, payload.xEnd], [3, 8]);
    assert.equal(payload.yMin, 3, "the bottom of the 3..7 window");
  });

  test("nothing is sent before the window on screen has arrived", () => {
    state.edit.pulseView = null;
    app.addSpikesInSelection({ start: 2, end: 6 });
    app.deleteSpikesInSelection({ start: 2, end: 6 });
    assert.equal(app.requestRoiEdit.calls.length, 0);
  });

  test("add-artifact uses the same mapping under its own action", () => {
    app.addArtifactInSelection({ start: 2, end: 6, yMin: 20, yMax: 60 });
    const [[action, payload]] = app.requestRoiEdit.calls;
    assert.equal(action, "add-artifact");
    assertClose(payload.yMin, 3.6, "yMin");
  });

  test("delete-spikes sends a value band whichever way the box was drawn", () => {
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
    }
  });

  test("delete-dr scales the plot to the fastest discharge rate in view", () => {
    state.edit.totalSamples = 2000;
    state.edit.distimes[0] = ints(0, 200, 400, 1000);
    app.deleteDrInSelection({ start: 900, end: 100, yMin: 80, yMax: 30 });
    const [[action, payload]] = app.requestRoiEdit.calls;
    assert.equal(action, "delete-dr");
    assert.deepEqual([payload.xStart, payload.xEnd], [100, 900]);
    // Peak rate 2000/200 = 10 Hz; 80 px down a 100 px plot is 2 Hz.
    assertClose(payload.yMin, 2, "yMin");
  });

  test("delete-dr needs two discharges", () => {
    state.edit.distimes[0] = ints(5);
    app.deleteDrInSelection({ start: 0, end: 9 });
    assert.equal(app.requestRoiEdit.calls.length, 0);
  });
});

describe("edits on the server", () => {
  test("an ROI edit sends the box and mirrors the changed MU", async () => {
    app.api = fakeApi({
      "delete-spikes": changeFrame(
        {
          changed: [0],
          history: [{ type: "delete_spikes", mu_uid: "g0_mu0" }],
        },
        [[2, 8]],
      ),
    });
    app.requestRoiEdit = (action, payload) => {
      return import("../src/app/services/editing-service.js").then((m) =>
        m.requestRoiEdit(app, action, payload),
      );
    };
    await app.requestRoiEdit("delete-spikes", {
      muIdx: 0,
      xStart: 4,
      xEnd: 6,
      yMin: 1,
      yMax: 7,
    });
    assert.deepEqual(app.api.calls, [
      [
        "delete-spikes",
        { token: "tok", mu: 0, x_start: 4, x_end: 6, y_min: 1, y_max: 7 },
      ],
    ]);
    assert.deepEqual(rows(state.edit.distimes), [
      [2, 8],
      [1, 4],
    ]);
    assert.deepEqual(state.edit.versions, [5, 5]);
    assert.equal(state.edit.dirty, true);
    assert.equal(state.edit.canUndo, true);
    assert.deepEqual(state.edit.bookmarkPosition, { muIdx: 0, position: 5 });
    assert.equal(state.edit.editHistory.at(-1).type, "delete_spikes");
    assert.deepEqual(app.setEditStatus.calls.at(-1), [
      "ROI applied",
      "success",
    ]);
  });

  test("undo asks the server and drops the log entries it took back", async () => {
    state.edit.canUndo = true;
    state.edit.editHistory = [{ type: "first" }, { type: "delete_spikes" }];
    state.edit.selectionPulse = { start: 1, end: 2 };
    app.api = fakeApi({
      undo: changeFrame(
        { changed: [1], history_start: 1, can_undo: false, dirty: false },
        [[1, 4, 6]],
      ),
    });
    await undoEdit(app);
    assert.deepEqual(rows(state.edit.distimes), [
      [2, 5, 8],
      [1, 4, 6],
    ]);
    assert.deepEqual(
      state.edit.editHistory.map((e) => e.type),
      ["first"],
    );
    assert.equal(state.edit.canUndo, false);
    assert.equal(state.edit.selectionPulse, null);
    assert.deepEqual(app.setEditStatus.calls.at(-1), [
      "Undo applied",
      "success",
    ]);
  });

  test("nothing to undo says so and asks nothing", async () => {
    app.api = fakeApi({});
    await undoEdit(app);
    assert.deepEqual(app.setEditStatus.calls, [["Nothing to undo", "muted"]]);
    assert.equal(app.api.calls.length, 0);
  });

  test("duplicating switches to the new MU", async () => {
    state.edit.currentMu = 1;
    app.api = fakeApi({
      duplicate: changeFrame(
        {
          n_mu: 3,
          changed: [2],
          history_start: 0,
          history: [{ type: "duplicate_mu", mu_uid: "g0_mu2" }],
        },
        [[1, 4]],
      ),
    });
    await duplicateMu(app);
    assert.deepEqual(app.api.calls[0], ["duplicate", { token: "tok", mu: 1 }]);
    assert.equal(state.edit.distimes.length, 3);
    assert.equal(state.edit.currentMu, 2);
    assert.deepEqual(app.setEditStatus.calls.at(-1), [
      "MU duplicated — now editing MU 3",
      "success",
    ]);
  });

  test("removing duplicates keeps the MUs the server kept", async () => {
    state.edit.distimes.push(ints(2, 5, 9));
    state.edit.artifactTimes.push(ints(7));
    state.edit.muUids.push("g0_mu2");
    state.edit.currentMu = 2;
    app.api = fakeApi({
      "remove-duplicates": changeFrame({
        kept_indices: [1, 2],
        removed_count: 1,
        can_undo: false,
        mu_uids: ["g0_mu1", "g0_mu2"],
      }),
    });
    await removeDuplicateMus(app);
    assert.deepEqual(rows(state.edit.distimes), [
      [1, 4],
      [2, 5, 9],
    ]);
    assert.deepEqual(rows(state.edit.artifactTimes), [[], [7]]);
    assert.deepEqual(state.edit.muUids, ["g0_mu1", "g0_mu2"]);
    assert.equal(state.edit.currentMu, 1);
    assert.deepEqual(app.setEditStatus.calls.at(-1), [
      "1 duplicate removed",
      "success",
    ]);
  });

  test("no duplicates found changes nothing", async () => {
    app.api = fakeApi({
      "remove-duplicates": changeFrame({ removed_count: 0, dirty: false }),
    });
    await removeDuplicateMus(app);
    assert.deepEqual(app.setEditStatus.calls.at(-1), [
      "No duplicates found",
      "muted",
    ]);
  });

  test("flagging toggles the current MU's flag", async () => {
    app.api = fakeApi({
      flag: changeFrame({ changed: [0], flagged: [true, false] }, [[2, 5, 8]]),
    });
    await flagMuForDeletion(app);
    assert.deepEqual(app.api.calls[0], [
      "flag",
      { token: "tok", mu: 0, flag: true },
    ]);
    assert.deepEqual(state.edit.flagged, [true, false]);
  });

  test("a failed edit reports it and leaves the state alone", async () => {
    app.api = fakeApi({ reset: new Error("HTTP 400: mu_index out of range") });
    await resetCurrentMuEdits(app);
    assert.deepEqual(rows(state.edit.distimes), [
      [2, 5, 8],
      [1, 4],
    ]);
    assert.match(app.setEditStatus.calls.at(-1)[0], /mu_index out of range/);
  });
});

describe("saveEditedFile", () => {
  function stubSave(response) {
    const payloads = [];
    Object.assign(app, {
      getBidsMuscleNames: () => ["TA"],
      withBidsSaveFields: (payload) => ({ ...payload, project: "p" }),
    });
    app.api = {
      editSessionSave: async (payload) => {
        payloads.push(payload);
        return {
          saved: true,
          path: "/out.npz",
          ...changeFrame({ dirty: false, can_undo: false }).meta,
          ...response,
        };
      },
    };
    return payloads;
  }

  test("sends only the session token and the form's fields", async () => {
    state.edit.filename = "sub-01_task-x_decomp.npz";
    const payloads = stubSave({});
    await saveEditedFile(app);
    assert.deepEqual(payloads[0], {
      token: "tok",
      muscle: ["TA"],
      entity_label: "sub-01_task-x",
      file_label: "sub-01_task-x_decomp_edited.npz",
      software_versions: null,
      project: "p",
    });
    assert.equal("remove_duplicates" in payloads[0], false);
  });

  test("mirrors the MUs and log entries the save removed", async () => {
    const saveEntry = {
      type: "remove_flagged",
      on_save: true,
      removed_count: 1,
      removed_mu_uids: ["g0_mu0"],
    };
    stubSave({
      kept_indices: [1],
      n_mu: 1,
      flagged: [false],
      mu_uids: ["g0_mu1"],
      mu_grid_index: [0],
      versions: [2],
      has_pulse: [true],
      edit_history: [saveEntry],
    });
    await saveEditedFile(app);
    assert.deepEqual(state.edit.muUids, ["g0_mu1"]);
    assert.deepEqual(rows(state.edit.distimes), [[1, 4]]);
    assert.deepEqual(state.edit.flagged, [false]);
    assert.deepEqual(state.edit.editHistory, [saveEntry]);
    assert.equal(state.edit.dirty, false);
    assert.equal(app.renderEditExplorer.calls.length, 1);
  });
});
