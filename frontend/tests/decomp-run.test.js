// Running a decomposition: the run stream's events, the save that follows a
// finished run, cancelling, and a run whose upload the server has dropped.
import { test, describe } from "node:test";
import assert from "node:assert/strict";

import { recorder } from "./_dom.js";
import { loadedState, missingUpload, testApp } from "./_app.js";

const { createRunLive } = await import("../src/decomp/live.js");
const { autoSaveRunDecomposition, cancelDecomposition, runDecomposition } =
  await import("../src/decomp/run.js");

console.error = () => {};

describe("run stream handling", () => {
  function streamApp() {
    const app = testApp();
    Object.assign(app, {
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
    app.state.runLive = createRunLive(0);
    return app;
  }

  test("the done event keeps the token and summary, then saves", () => {
    const app = streamApp();
    app.handleStreamMessage({
      stage: "done",
      summary: { n_mu: 2, grid_names: ["A"], sil: [0.9, 0.92] },
      preview: {
        run_result_token: "run-1",
        mu_grid_index: [0, 0],
        total_samples: 10,
        rois: [],
        muscle_names: [],
        channel_means: [[1, 2]],
      },
    });
    const run = app.state.runLive;
    assert.equal(run.status, "done");
    assert.equal(run.phase, "save");
    assert.equal(run.summary.nMu, 2);
    assert.equal(app.state.runResultToken, "run-1");
    assert.equal(app.autoSaveRunDecomposition.calls.length, 1);
  });

  test("the done event leaves the recording and the session form as they are", () => {
    const app = streamApp();
    Object.assign(app.state, {
      muscleNames: ["tibialis anterior"],
      channelMeans: [[3, 4]],
      rois: [{ start: 2, end: 8 }],
    });
    const channelMeans = app.state.channelMeans;
    app.handleStreamMessage({
      stage: "done",
      summary: { n_mu: 1, grid_names: ["A"], sil: [0.9] },
      preview: {
        run_result_token: "run-1",
        mu_grid_index: [0],
        muscle_names: [],
        channel_means: [[1, 2]],
        rois: [[0, 10]],
      },
    });
    assert.deepEqual(app.state.muscleNames, ["tibialis anterior"]);
    assert.equal(app.state.channelMeans, channelMeans);
    assert.deepEqual(app.state.rois, [{ start: 2, end: 8 }]);
    assert.equal(app.renderBidsMuscleFields.calls.length, 0);
    assert.equal(app.ensureQcTraces.calls.length, 0);
  });

  test("an error marks the run failed with its detail", () => {
    const app = streamApp();
    app.handleStreamMessage({
      stage: "error",
      message: "Decomposition failed",
      detail: "boom",
    });
    assert.equal(app.state.runLive.status, "failed");
    assert.equal(app.state.runLive.error, "Decomposition failed: boom");
  });

  test("the save uses the run token and the units' grids", async () => {
    const app = streamApp();
    const payloads = [];
    Object.assign(app, {
      getBidsMuscleNames: () => ["ta"],
      saveRun: async (payload) => {
        payloads.push(payload);
        return { path: "/out/x.npz" };
      },
      loadDecompositionForEditByPath: recorder(),
    });
    Object.assign(app.state, {
      runResultToken: "run-1",
      totalSamples: 10,
      fsamp: 2000,
      gridNames: ["A"],
    });
    app.state.runLive.summary = {
      nMu: 2,
      nMuByGrid: [2],
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

describe("cancelDecomposition", () => {
  function cancelApp(api, els = {}) {
    const app = testApp({ api, els });
    app.switchStage = recorder();
    return app;
  }

  test("asks the server to stop this tab's run", async () => {
    let cancels = 0;
    const els = { cancelRun: { disabled: false, hidden: false } };
    const app = cancelApp(
      {
        cancelDecomposition: async () => {
          cancels += 1;
          return { cancelled: true };
        },
      },
      els,
    );
    app.state.isRunning = true;
    await cancelDecomposition(app);
    assert.equal(cancels, 1);
    assert.equal(els.cancelRun.disabled, true);
  });

  test("a failed request re-enables the button", async () => {
    const els = { cancelRun: { disabled: false, hidden: false } };
    const app = cancelApp(
      {
        cancelDecomposition: async () => {
          throw new Error("HTTP 500");
        },
      },
      els,
    );
    app.state.isRunning = true;
    await cancelDecomposition(app);
    assert.equal(els.cancelRun.disabled, false);
    assert.equal(app.setStatus.calls.at(-1)[1], "error");
  });

  test("the cancelled event clears the run from the page", () => {
    const app = cancelApp({});
    app.state.runLive = createRunLive(0);
    app.handleStreamMessage({
      stage: "cancelled",
      message: "Decomposition cancelled",
    });
    assert.deepEqual(app.setStatus.calls, [
      ["Decomposition cancelled", "muted"],
    ]);
    assert.equal(app.state.runLive, null);
  });

  test("a cancel seen from the run page goes back to QC", () => {
    const app = cancelApp({});
    app.state.runLive = createRunLive(0);
    app.state.currentStage = "run";
    app.handleStreamMessage({ stage: "cancelled" });
    assert.deepEqual(app.switchStage.calls, [["qc"]]);
  });

  test("a cancel seen from another page leaves the user there", () => {
    const app = cancelApp({});
    app.state.runLive = createRunLive(0);
    app.state.currentStage = "edit";
    app.updateStepAvailability = recorder();
    app.handleStreamMessage({ stage: "cancelled" });
    assert.deepEqual(app.switchStage.calls, []);
    assert.equal(app.updateStepAvailability.calls.length, 1);
  });

  test("the button shows only while a run is going", () => {
    const els = { cancelRun: { disabled: true, hidden: true } };
    const app = cancelApp({}, els);
    app.state.isRunning = true;
    app.updateStartAvailability();
    assert.equal(els.cancelRun.hidden, false);
    assert.equal(els.cancelRun.disabled, false);
    app.state.isRunning = false;
    app.updateStartAvailability();
    assert.equal(els.cancelRun.hidden, true);
  });
});

describe("upload-token recovery", () => {
  function recoveryApp(state, api) {
    const app = testApp({ state, api });
    Object.assign(app, {
      switchStage: recorder(),
      handleStreamMessage: recorder(),
    });
    return app;
  }

  const streamBody = () => new Response('{"stage":"done"}\n').body;

  test("re-mints the token from the file path and retries once", async () => {
    const state = loadedState();
    const sentTokens = [];
    const previewPaths = [];
    const api = {
      decomposeStream: async (payload) => {
        sentTokens.push(payload.upload_token);
        if (sentTokens.length === 1) throw missingUpload();
        return { body: streamBody() };
      },
      fetchPreviewByPath: async (path) => {
        previewPaths.push(path);
        return { upload_token: "fresh-token" };
      },
    };
    state.rois = [{ start: 0, end: 3 }];
    const app = recoveryApp(state, api);

    await runDecomposition(app);

    assert.deepEqual(previewPaths, ["/data/old.otb+"]);
    assert.deepEqual(sentTokens, ["old-token", "fresh-token"]);
    assert.equal(state.uploadToken, "fresh-token");
    // User selections survive the reload.
    assert.deepEqual(state.rois, [{ start: 0, end: 3 }]);
    assert.deepEqual(state.discardChannels, [[0, 1]]);
    assert.deepEqual(app.handleStreamMessage.calls, [[{ stage: "done" }]]);
    assert.equal(state.isRunning, false);
  });

  test("without a file path the original error is reported", async () => {
    const state = loadedState();
    state.file = { name: "old.otb+" };
    let previewCalls = 0;
    const api = {
      decomposeStream: async () => {
        throw missingUpload();
      },
      fetchPreviewByPath: async () => {
        previewCalls += 1;
      },
    };
    const app = recoveryApp(state, api);

    await runDecomposition(app);

    assert.equal(previewCalls, 0);
    assert.deepEqual(app.setStatus.calls.at(-1), [
      "Error: Missing or expired upload; request /api/v1/preview-by-path first",
      "error",
    ]);
    assert.equal(state.isRunning, false);
  });

  test("other errors are not retried", async () => {
    for (const message of [
      "HTTP 400: upload_token is fine, the params are not",
      "HTTP 500: boom",
    ]) {
      let streamCalls = 0;
      const api = {
        decomposeStream: async () => {
          streamCalls += 1;
          throw new Error(message);
        },
        fetchPreviewByPath: async () => {
          throw new Error("should not be called");
        },
      };
      const app = recoveryApp(loadedState(), api);
      await runDecomposition(app);
      assert.equal(streamCalls, 1, message);
      assert.deepEqual(app.setStatus.calls.at(-1), [
        `Error: ${message}`,
        "error",
      ]);
    }
  });
});
