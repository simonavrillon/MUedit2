// The QC stage asks for viewport series (/series/*), never whole-recording lists.
// Run with `npm test` (Node's built-in test runner, no dependencies).
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
  requestAnimationFrame: (fn) => setTimeout(fn, 0),
};
console.error = () => {};

const { state: initialState } = await import("../src/state/state.js");
const { createApp } = await import("../src/app/create-app.js");
const { OVERVIEW_BINS, QC_TRACE_BINS } = await import("../src/config.js");

function recorder() {
  const calls = [];
  const fn = (...args) => calls.push(args);
  fn.calls = calls;
  return fn;
}

/** An API whose series are one envelope row per requested kind. */
function seriesApi() {
  const requests = [];
  const row = { min: new Float32Array([0, 1]), max: new Float32Array([2, 3]) };
  return {
    requests,
    row,
    fetchSeries: async (kind, params) => {
      requests.push([kind, params]);
      return { meta: { kind: "envelope" }, rows: [row] };
    },
    fetchPreviewByPath: async () => ({
      upload_token: "tok",
      grid_names: ["GR08MM1305"],
      total_samples: 1000,
      fsamp: 2048,
      channel_means: [[1, 2]],
      coordinates: [
        [
          [0, 0],
          [0, 1],
        ],
      ],
      auxiliary_names: ["force"],
    }),
  };
}

/** Let pending promise callbacks run. */
const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

/**
 * An API whose EMG requests wait until released, so a test can order the
 * answers; each answer's row is tagged with the window's start.
 */
function heldSeriesApi() {
  const requests = [];
  const rows = new Map();
  const pending = [];
  const rowFor = (start) => {
    if (!rows.has(start)) rows.set(start, { min: [start], max: [start] });
    return rows.get(start);
  };
  return {
    requests,
    rowFor,
    fetchSeries: (kind, params) => {
      requests.push([kind, params]);
      return new Promise((resolve) => pending.push({ params, resolve }));
    },
    /** Answer the waiting request whose params match `match`. */
    release(match) {
      const idx = pending.findIndex(({ params }) =>
        Object.entries(match).every(([k, v]) => params[k] === v),
      );
      assert.ok(idx >= 0, `no request waiting for ${JSON.stringify(match)}`);
      const [{ params, resolve }] = pending.splice(idx, 1);
      resolve({ meta: { kind: "envelope" }, rows: [rowFor(params.start)] });
    },
  };
}

function testApp(api) {
  const app = createApp({ state: structuredClone(initialState), els: {}, api });
  Object.assign(app, {
    setStatus: recorder(),
    updateProgress: recorder(),
    switchStage: recorder(),
    renderChannelQC: recorder(),
    refreshVisuals: recorder(),
    showWorkspace: recorder(),
  });
  return app;
}

describe("QC series requests", () => {
  test("a grid's mini traces are one envelope per channel over the ROI", async () => {
    const api = seriesApi();
    const app = testApp(api);
    Object.assign(app.state, {
      uploadToken: "tok",
      seriesLength: 1000,
      rois: [{ start: 100, end: 900 }],
    });
    app.ensureQcTraces();
    await settle();
    assert.deepEqual(api.requests, [
      [
        "emg",
        {
          upload_token: "tok",
          grid: 0,
          start: 100,
          end: 900,
          bins: QC_TRACE_BINS,
        },
      ],
    ]);
    assert.deepEqual(app.state.channelTraces, [[api.row]]);
    assert.equal(app.renderChannelQC.calls.length, 1);
  });

  test("traces already on screen are not asked for again", async () => {
    const api = seriesApi();
    const app = testApp(api);
    Object.assign(app.state, { uploadToken: "tok", seriesLength: 1000 });
    app.ensureQcTraces();
    await settle();
    app.ensureQcTraces();
    await settle();
    assert.equal(api.requests.length, 1);
  });

  test("traces for a window the user moved off are dropped", async () => {
    const api = heldSeriesApi();
    const app = testApp(api);
    Object.assign(app.state, {
      uploadToken: "tok",
      seriesLength: 1000,
      rois: [{ start: 0, end: 1000 }],
    });
    app.ensureQcTraces();
    app.state.rois = [{ start: 200, end: 400 }];
    app.ensureQcTraces();
    api.release({ start: 0, end: 1000 });
    await settle();
    assert.deepEqual(app.state.channelTraces, []);
    api.release({ start: 200, end: 400 });
    await settle();
    assert.deepEqual(
      api.requests.map(([, p]) => [p.start, p.end]),
      [
        [0, 1000],
        [200, 400],
      ],
    );
    assert.deepEqual(app.state.channelTraces, [[api.rowFor(200)]]);
  });

  test("each grid keeps the traces of its own window", async () => {
    const api = heldSeriesApi();
    const app = testApp(api);
    Object.assign(app.state, { uploadToken: "tok", seriesLength: 1000 });
    app.ensureQcTraces();
    app.state.currentGrid = 1;
    app.ensureQcTraces();
    api.release({ grid: 0 });
    await settle();
    api.release({ grid: 1 });
    await settle();
    assert.deepEqual(app.state.channelTraces, [
      [api.rowFor(0)],
      [api.rowFor(0)],
    ]);
    // Only the grid on screen is redrawn.
    assert.equal(app.renderChannelQC.calls.length, 1);
  });

  test("a preview fetches the overview and aux envelopes of the whole recording", async () => {
    const api = seriesApi();
    const app = testApp(api);
    const ok = await app.handleRawFilePath("/data/rec.mat", "rec.mat");
    assert.equal(ok, true);
    const whole = { upload_token: "tok", bins: OVERVIEW_BINS };
    assert.deepEqual(api.requests.slice(0, 2), [
      ["overview", whole],
      ["aux", whole],
    ]);
    assert.deepEqual(app.state.gridSeries, [api.row]);
    assert.deepEqual(app.state.auxSeries, [api.row]);
    assert.deepEqual(app.state.auxNames, ["force"]);
  });
});
