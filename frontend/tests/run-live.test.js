// The decomposition page's live model: phases, dots, counts, estimate and summary.
// Run with `npm test` (Node's built-in test runner, no dependencies).
import { test, describe } from "node:test";
import assert from "node:assert/strict";

import { installDom } from "./fake-dom.js";

installDom();

const live = await import("../src/decomp/live.js");
const { DOT, applyRunEvent, createRunLive } = live;
const { state: initialState } = await import("../src/state/state.js");
const { createApp } = await import("../src/app/create-app.js");

const shape = { phase: "decompose", ngrid: 2, nwindows: 1, niter: 6 };

function recorder() {
  const calls = [];
  const fn = (...args) => calls.push(args);
  fn.calls = calls;
  return fn;
}

describe("applyRunEvent", () => {
  test("maps the backend's phases onto the track", () => {
    const run = createRunLive(0);
    applyRunEvent(run, { phase: "preprocess" }, 1);
    assert.equal(run.phase, "preprocess");
    applyRunEvent(run, { phase: "export" }, 2);
    assert.equal(run.phase, "save");
    applyRunEvent(run, { stage: "progress", message: "no phase" }, 3);
    assert.equal(run.phase, "save");
  });

  test("lays out a row per grid × window and fills its dots", () => {
    const run = createRunLive(0);
    applyRunEvent(
      run,
      { ...shape, grid: 0, window: 0, iter: 0, outcomes: "" },
      5,
    );
    assert.equal(run.rows.length, 2);
    assert.equal(run.searchStartedAt, 5);
    const change = applyRunEvent(
      run,
      { ...shape, grid: 1, window: 0, iter: 5, outcomes: "krfkr" },
      6,
    );
    assert.deepEqual(change, { row: 1, from: 0, to: 5 });
    assert.deepEqual(Array.from(run.rows[1].dots), [
      DOT.kept,
      DOT.rejected,
      DOT.fewSpikes,
      DOT.kept,
      DOT.rejected,
      DOT.pending,
    ]);
    assert.deepEqual(run.keptByGrid, [0, 2]);
    assert.equal(live.keptTotal(run), 2);
  });

  test("a window that stops early marks its remaining dots skipped", () => {
    const run = createRunLive(0);
    applyRunEvent(
      run,
      {
        ...shape,
        grid: 0,
        window: 0,
        iter: 2,
        outcomes: "kk",
        window_done: true,
      },
      1,
    );
    assert.deepEqual(Array.from(run.rows[0].dots.slice(2)), [
      DOT.skipped,
      DOT.skipped,
      DOT.skipped,
      DOT.skipped,
    ]);
    assert.equal(run.rows[0].done, 6);
  });
});

describe("searchSecondsLeft", () => {
  test("projects the pace of the iterations so far", () => {
    const run = createRunLive(0);
    applyRunEvent(
      run,
      { ...shape, grid: 0, window: 0, iter: 0, outcomes: "" },
      0,
    );
    run.rows[0].done = 6;
    run.rows[1].done = 0;
    assert.equal(live.searchSecondsLeft(run, 1000), null, "too early");
    assert.equal(
      live.searchSecondsLeft(run, 12_000),
      null,
      "too few iterations",
    );
    run.rows[1].done = 6;
    run.rows[0].done = 6;
    run.niter = 12;
    assert.equal(live.searchSecondsLeft(run, 12_000), 12);
  });

  test("formats clocks and estimates", () => {
    assert.equal(live.formatClock(65), "1:05");
    assert.equal(live.formatClock(3723), "1:02:03");
    assert.equal(live.formatRemaining(30), "under a minute");
    assert.equal(live.formatRemaining(150), "about 3 min");
  });
});

describe("buildRunSummary", () => {
  test("counts units per grid", () => {
    const summary = live.buildRunSummary(
      { mu_count: 3, grid_names: ["A", "B"] },
      { mu_grid_index: [0, 1, 1] },
    );
    assert.deepEqual(summary.perGrid, [1, 2]);
    assert.deepEqual(summary.muGridIndex, [0, 1, 1]);
  });

  test("no units", () => {
    const summary = live.buildRunSummary(
      { mu_count: 0, grid_names: ["A"] },
      null,
    );
    assert.deepEqual(summary.perGrid, [0]);
  });
});

describe("run stream handling", () => {
  function testApp() {
    const state = structuredClone(initialState);
    const app = createApp({ state, els: {}, api: {} });
    Object.assign(app, {
      setStatus: recorder(),
      renderChannelQC: recorder(),
      refreshVisuals: recorder(),
      ensureQcTraces: recorder(),
      populateAuxSelector: recorder(),
      populateGridTabs: recorder(),
      scheduleLayoutRerender: recorder(),
      prefillHardwareFields: recorder(),
      renderBidsMuscleFields: recorder(),
      autoSaveRunDecomposition: recorder(),
    });
    state.runLive = createRunLive(0);
    return app;
  }

  test("the done event keeps the token and summary, then saves", () => {
    const app = testApp();
    app.handleStreamMessage({
      stage: "done",
      pct: 100,
      summary: { mu_count: 2, grid_names: ["A"], sil: [0.9, 0.92] },
      preview: {
        run_result_token: "run-1",
        mu_grid_index: [0, 0],
        total_samples: 10,
        rois: [],
        muscle: [],
        channel_means: [[1, 2]],
      },
    });
    const run = app.state.runLive;
    assert.equal(run.status, "done");
    assert.equal(run.phase, "save");
    assert.equal(run.summary.muCount, 2);
    assert.equal(app.state.runResultToken, "run-1");
    assert.equal(app.autoSaveRunDecomposition.calls.length, 1);
  });

  test("the done event leaves the recording and the session form as they are", () => {
    const app = testApp();
    Object.assign(app.state, {
      muscle: ["tibialis anterior"],
      channelMeans: [[3, 4]],
      rois: [{ start: 2, end: 8 }],
    });
    const channelMeans = app.state.channelMeans;
    app.handleStreamMessage({
      stage: "done",
      pct: 100,
      summary: { mu_count: 1, grid_names: ["A"], sil: [0.9] },
      preview: {
        run_result_token: "run-1",
        mu_grid_index: [0],
        muscle: [],
        channel_means: [[1, 2]],
        rois: [[0, 10]],
      },
    });
    assert.deepEqual(app.state.muscle, ["tibialis anterior"]);
    assert.equal(app.state.channelMeans, channelMeans);
    assert.deepEqual(app.state.rois, [{ start: 2, end: 8 }]);
    assert.equal(app.renderBidsMuscleFields.calls.length, 0);
    assert.equal(app.ensureQcTraces.calls.length, 0);
  });

  test("an error marks the run failed with its detail", () => {
    const app = testApp();
    app.handleStreamMessage({
      stage: "error",
      message: "Decomposition failed",
      detail: "boom",
    });
    assert.equal(app.state.runLive.status, "failed");
    assert.equal(app.state.runLive.error, "Decomposition failed: boom");
  });

  test("the save uses the run token and the units' grids", async () => {
    const app = testApp();
    const payloads = [];
    Object.assign(app, {
      getBidsMuscleNames: () => ["ta"],
      persistNpzBySaveTarget: async (payload) => {
        payloads.push(payload);
        return { path: "/out/x.npz" };
      },
      loadDecompositionForEditByPath: recorder(),
    });
    const { autoSaveRunDecomposition } = await import("../src/decomp/run.js");
    Object.assign(app.state, {
      runResultToken: "run-1",
      seriesLength: 10,
      fsamp: 2000,
      gridNames: ["A"],
    });
    app.state.runLive.summary = {
      muCount: 2,
      perGrid: [2],
      muGridIndex: [0, 0],
    };
    await autoSaveRunDecomposition(app);
    assert.equal(payloads[0].run_result_token, "run-1");
    assert.deepEqual(payloads[0].mu_grid_index, [0, 0]);
    assert.equal(app.state.runLive.savedPath, "/out/x.npz");
    // Preloaded for Edit without leaving the run page.
    assert.deepEqual(app.loadDecompositionForEditByPath.calls, [
      ["/out/x.npz", { open: false }],
    ]);
  });
});
