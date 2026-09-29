// Run-stage MU explorer: dropdown and plot models.
import { test, describe } from "node:test";
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

const { buildRunMuDropdownModel, buildRunMuExplorerModel } =
  await import("../src/decomp/explorer.js");
const { UNIFORM_PULSE_COLOR } = await import("../src/config.js");

const byGrid = (lists) => (g) => lists[g] || [];

describe("buildRunMuDropdownModel", () => {
  test("labels grids and MUs one-based", () => {
    const model = buildRunMuDropdownModel({
      state: { gridNames: ["GR08MM1305", ""], currentMuGrid: 0, currentMu: 1 },
      getMuIndicesForGrid: byGrid([[0, 1]]),
    });
    assert.deepEqual(model.gridOptions, [
      { value: 0, label: "Grid 1 • GR08MM1305" },
      { value: 1, label: "Grid 2" },
    ]);
    assert.deepEqual(model.muOptions, [
      { value: 0, label: "MU 1" },
      { value: 1, label: "MU 2" },
    ]);
    assert.equal(model.selectedMu, 1);
  });

  test("an MU from another grid falls back to the first on this grid", () => {
    const model = buildRunMuDropdownModel({
      state: { gridNames: ["A", "B"], currentMuGrid: 1, currentMu: 0 },
      getMuIndicesForGrid: byGrid([[0], [3, 4]]),
    });
    assert.equal(model.selectedGrid, 1);
    assert.equal(model.selectedMu, 3);
  });

  test("an empty grid moves the selection to the first grid with MUs", () => {
    const model = buildRunMuDropdownModel({
      state: { gridNames: ["A", "B"], currentMuGrid: 0, currentMu: 0 },
      getMuIndicesForGrid: byGrid([[], [2]]),
    });
    assert.equal(model.selectedGrid, 1);
    assert.equal(model.selectedMu, 2);
  });

  test("no MUs leaves nothing selected", () => {
    const model = buildRunMuDropdownModel({
      state: { gridNames: [], currentMuGrid: 0, currentMu: 0 },
      getMuIndicesForGrid: byGrid([]),
    });
    assert.deepEqual(model.muOptions, []);
    assert.equal(model.selectedMu, undefined);
  });
});

describe("buildRunMuExplorerModel", () => {
  const ints = (...v) => Int32Array.from(v);
  const pulseView = (mu) => ({ mu, start: 1, end: 5, bins: 254 });

  test("counts the discharges and draws the fetched window of this MU", () => {
    const shown = pulseView(0);
    const model = buildRunMuExplorerModel({
      state: {
        muDistimes: [ints(2, 4)],
        seriesLength: 6,
        currentMu: 0,
        runView: { start: 1, end: 5 },
        runPulseView: shown,
      },
      fsamp: 2048,
    });
    assert.equal(model.muIdx, 0);
    assert.equal(model.trace, shown);
    assert.equal(model.metaText, "2 discharge times");
    assert.deepEqual(model.view, { start: 1, end: 5 });
    assert.equal(model.nextView, null);
    assert.equal(model.color, UNIFORM_PULSE_COLOR);
    assert.equal(model.fsamp, 2048);
  });

  test("a window of another MU is not drawn", () => {
    const model = buildRunMuExplorerModel({
      state: {
        muDistimes: [ints(), ints(3)],
        seriesLength: 6,
        currentMu: 1,
        runView: { start: 0, end: 6 },
        runPulseView: pulseView(0),
      },
    });
    assert.equal(model.trace, null);
    assert.deepEqual(Array.from(model.spikes), [3]);
  });

  test("with no view yet, proposes one spanning the recording", () => {
    const model = buildRunMuExplorerModel({
      state: { muDistimes: [ints()], seriesLength: 6, currentMu: 0 },
    });
    assert.deepEqual(model.nextView, { start: 0, end: 6 });
    assert.deepEqual(model.view, model.nextView);
    assert.equal(model.fsamp, null);
  });

  test("resets a view that runs past the recording", () => {
    const model = buildRunMuExplorerModel({
      state: {
        muDistimes: [ints()],
        seriesLength: 6,
        currentMu: 0,
        runView: { start: 0, end: 100 },
      },
    });
    assert.deepEqual(model.nextView, { start: 0, end: 6 });
  });

  test("a bad current MU or sampling rate is sanitised", () => {
    for (const currentMu of [-3, 7]) {
      const model = buildRunMuExplorerModel({
        state: {
          muDistimes: [ints(1)],
          seriesLength: 6,
          currentMu,
          runView: { start: 0, end: 6 },
        },
        fsamp: 0,
      });
      assert.equal(model.muIdx, 0);
      assert.equal(model.fsamp, null);
    }
  });

  test("no MUs gives an empty model", () => {
    const model = buildRunMuExplorerModel({ state: {} });
    assert.equal(model.trace, null);
    assert.equal(model.spikes.length, 0);
    assert.equal(model.metaText, "");
  });
});
