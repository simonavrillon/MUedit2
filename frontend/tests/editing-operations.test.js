// Edit-stage logic without the DOM: the grid/MU dropdown model, discharge
// rates, and a box drawn on the pulse plot turned into an edit request.
import { test, describe, beforeEach } from "node:test";
import assert from "node:assert/strict";

import { recorder } from "./_dom.js";
import { freshState, ints, testApp } from "./_app.js";

const ops = await import("../src/editing/operations.js");

const PLOT_HEIGHT = 100;

function assertClose(actual, expected, message) {
  assert.ok(
    Math.abs(actual - expected) < 1e-9,
    `${message ?? "value"}: expected ${expected}, got ${actual}`,
  );
}

/** The fetched window of MU 0: samples 0..9, so min 0, max 9, span 9. */
function rampView() {
  return {
    mu: 0,
    version: 1,
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

let state;
let app;

beforeEach(() => {
  state = freshState();
  state.gridNames = ["GR08MM1305"];
  Object.assign(state.edit, {
    token: "tok",
    spikeTimes: [ints(2, 5, 8), ints(1, 4)],
    flagged: [false, false],
    muUids: ["g0_mu0", "g0_mu1"],
    muGridIndex: [0, 0],
    versions: [1, 2],
    fsamp: 2000,
    totalSamples: 10,
    pulseView: rampView(),
  });
  app = testApp({ state });
  Object.assign(app, {
    requestRoiEdit: recorder(),
    getPulsePlotHeight: () => PLOT_HEIGHT,
  });
});

describe("buildEditDropdownModel", () => {
  /** MU i on grid `muGridIndex[i]`. */
  function onGrids(muGridIndex) {
    state.edit.muGridIndex = muGridIndex;
    state.edit.spikeTimes = muGridIndex.map(() => ints());
  }

  test("keeps the current grid and MU when both are valid", () => {
    state.edit.currentMu = 1;
    const model = ops.buildEditDropdownModel(state);
    assert.equal(model.currentGrid, 0);
    assert.equal(model.currentMu, 1);
    assert.equal(model.needsGridSwitch, false);
    assert.ok(!model.needsMuSwitch);
  });

  test("switches to the grid's first MU when the current one is elsewhere", () => {
    state.gridNames = ["A", "B"];
    state.edit.currentMu = 5;
    onGrids([1, 1, 0, 0, 1, 1]);
    const model = ops.buildEditDropdownModel(state);
    assert.equal(model.currentMu, 2);
    assert.ok(model.needsMuSwitch);
  });

  test("falls through to the first grid that has MUs", () => {
    state.gridNames = ["A", "B", "C"];
    state.edit.currentGrid = 0;
    state.edit.currentMu = 1;
    onGrids([2]);
    const model = ops.buildEditDropdownModel(state);
    assert.equal(model.currentGrid, 2);
    assert.deepEqual(model.muOptions, [0]);
    assert.equal(model.needsGridSwitch, true);
    assert.equal(model.currentMu, 0);
  });

  test("no MUs anywhere asks for no switch", () => {
    state.gridNames = ["A", "B"];
    onGrids([]);
    const model = ops.buildEditDropdownModel(state);
    assert.equal(model.currentGrid, 0);
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
    assert.equal(payload.mu, 0);
    assert.equal(payload.xStart, 2);
    assert.equal(payload.xEnd, 6);
    // 60 px down a 100 px plot is 40% up a 0..9 range.
    assertClose(payload.yMin, 3.6, "yMin");
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

  test("a box over an envelope is ranged on its bins' extremes", () => {
    state.edit.pulseView.row = {
      min: Float32Array.from([-2, 0.5]),
      max: Float32Array.from([1, 7]),
    };
    app.addSpikesInSelection({ start: 2, end: 6, yMin: 20, yMax: 60 });
    // 40% up a -2..7 range.
    assertClose(app.requestRoiEdit.calls[0][1].yMin, 1.6, "yMin");
  });

  test("a flat window still maps the box to values around it", () => {
    state.edit.pulseView.row = new Float32Array(10).fill(3);
    app.addSpikesInSelection({ start: 2, end: 6, yMin: 20, yMax: 60 });
    assertClose(app.requestRoiEdit.calls[0][1].yMin, 3.4, "yMin");
  });

  test("nothing is sent before the window of the MU as it is now has arrived", () => {
    const box = { start: 2, end: 6, yMin: 20, yMax: 60 };
    state.edit.versions[0] = 3;
    app.addSpikesInSelection(box);
    state.edit.pulseView = null;
    app.deleteSpikesInSelection(box);
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
});
