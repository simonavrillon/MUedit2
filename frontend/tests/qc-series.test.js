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

const { state: initialState } = await import("../src/app/state.js");
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
    app.state.uploadToken = "tok";
    await app.requestQcGridWindow(0, 100, 900);
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
    assert.equal(api.requests[2][0], "emg");
  });
});
