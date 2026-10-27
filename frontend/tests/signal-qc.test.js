// QC requests: the channel traces kept on view, automatic QC, and opening a
// raw file's preview, which replaces the file shown only once it has all arrived.
import { test, describe, beforeEach } from "node:test";
import assert from "node:assert/strict";

import { recorder } from "./_dom.js";
import { freshState, loadedState, settle, testApp } from "./_app.js";

const { OVERVIEW_BINS, QC_TRACE_BINS, requestAutoQc } =
  await import("../src/signal/qc.js");

console.error = () => {};

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
      aux_names: ["force"],
    }),
  };
}

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

/** The app over `state` and `api`, with the redraws it triggers recorded. */
function qcApp(api, state = freshState()) {
  const app = testApp({ state, api });
  Object.assign(app, {
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
    const app = qcApp(api);
    Object.assign(app.state, {
      uploadToken: "tok",
      totalSamples: 1000,
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
    const app = qcApp(api);
    Object.assign(app.state, { uploadToken: "tok", totalSamples: 1000 });
    app.ensureQcTraces();
    await settle();
    app.ensureQcTraces();
    await settle();
    assert.equal(api.requests.length, 1);
  });

  test("traces for a window the user moved off are dropped", async () => {
    const api = heldSeriesApi();
    const app = qcApp(api);
    Object.assign(app.state, {
      uploadToken: "tok",
      totalSamples: 1000,
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
    const app = qcApp(api);
    Object.assign(app.state, { uploadToken: "tok", totalSamples: 1000 });
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
    const app = qcApp(api);
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

describe("requestAutoQc", () => {
  let app;

  beforeEach(() => {
    app = qcApp({});
  });

  test("marks the bad channels and the artifact windows it found", async () => {
    app.state.uploadToken = "tok";
    app.state.channelMeans = [[1, 1]];
    app.api.runAutoQc = async () => ({
      bad_channels_per_grid: [[0, 1]],
      artifact_regions: [
        [100, 200],
        [400, 450],
      ],
    });
    assert.equal(await requestAutoQc(app), true);
    assert.deepEqual(app.state.artifactRegions, [
      { start: 100, end: 200 },
      { start: 400, end: 450 },
    ]);
    assert.deepEqual(app.state.discardChannels, [[0, 1]]);
  });

  test("keeps the traces it has and redraws them with the new marks", async () => {
    const traces = [
      [
        { min: Float32Array.of(0), max: Float32Array.of(1) },
        { min: Float32Array.of(0), max: Float32Array.of(1) },
      ],
    ];
    Object.assign(app.state, {
      uploadToken: "tok",
      channelMeans: [[1, 1]],
      channelTraces: traces,
    });
    app.api.runAutoQc = async () => ({ bad_channels_per_grid: [[0, 1]] });
    assert.equal(await requestAutoQc(app), true);
    assert.equal(app.state.channelTraces, traces);
    assert.equal(app.renderChannelQC.calls.length, 1);
  });
});

describe("opening a raw file", () => {
  let state;
  const failingApi = {
    fetchPreviewByPath: async () => {
      throw new Error("HTTP 400: unsupported format");
    },
  };

  beforeEach(() => {
    state = loadedState();
  });

  test("a file that fails to open leaves the current one as it was", async () => {
    state.edit.token = "edit-1";
    const before = structuredClone(state);
    const app = qcApp(failingApi, state);
    const ok = await app.handleRawFilePath("/data/new.mat", "new.mat");

    assert.equal(ok, false);
    assert.deepEqual(state, before);
    assert.deepEqual(app.setStatus.calls.at(-1), [
      "Preview failed: HTTP 400: unsupported format",
      "error",
    ]);
  });

  test("the file shown changes only once the new preview has arrived", async () => {
    let release;
    const api = {
      fetchPreviewByPath: () =>
        new Promise((resolve) => {
          release = resolve;
        }),
      fetchSeries: async () => ({ rows: [new Float32Array([4, 5])] }),
    };
    const app = qcApp(api, state);
    Object.assign(app, {
      resetSessionForm: recorder(),
      applyPreviewMetadata: recorder(),
      populateAuxSelector: recorder(),
      populateGridTabs: recorder(),
      prefillHardwareFields: recorder(),
      renderBidsMuscleFields: recorder(),
      showWorkspace: recorder(),
    });
    const opening = app.handleRawFilePath("/data/new.mat", "new.mat");
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert.equal(state.file.path, "/data/old.otb+");
    assert.equal(state.uploadToken, "old-token");

    release({ upload_token: "new-token", total_samples: 2 });
    assert.equal(await opening, true);
    assert.deepEqual(state.file, { name: "new.mat", path: "/data/new.mat" });
    assert.equal(state.uploadToken, "new-token");
    assert.deepEqual(app.resetSessionForm.calls, [["new.mat"]]);
  });

  test("silent failure skips the status message", async () => {
    const app = qcApp(failingApi, state);
    const ok = await app.handleRawFilePath("/data/new.mat", "new.mat", {
      silentFailure: true,
    });

    assert.equal(ok, false);
    assert.ok(
      !app.setStatus.calls.some(([text]) => text.startsWith("Preview failed")),
      "no 'Preview failed' status for a silent attempt",
    );
    assert.equal(state.file.path, "/data/old.otb+");
  });
});
