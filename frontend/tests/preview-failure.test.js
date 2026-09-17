// Failed raw-file preview and upload-token recovery.
// Run with `npm test` (Node's built-in test runner, no dependencies).
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

// The code under test logs expected failures; keep the test output readable.
console.error = () => {};

const { state: initialState } = await import("../src/app/state.js");
const { createQcStageService } = await import("../src/app/stages/qc-stage.js");
const { runDecomposition } = await import("../src/decomp/run.js");

const pristine = structuredClone(initialState);

/** State as it looks after a previous file was loaded successfully. */
function loadedState() {
  const state = structuredClone(pristine);
  Object.assign(state, {
    file: { name: "old.otb+", path: "/data/old.otb+" },
    uploadToken: "old-token",
    previewSeries: [1, 2, 3],
    gridSeries: [[1, 2, 3]],
    gridNames: ["GR08MM1305"],
    channelMeans: [[1, 1]],
    channelTraces: [[0.5]],
    seriesLength: 3,
    discardMasks: [[0, 1]],
    qcWindowLoading: { 0: true },
  });
  return state;
}

function recorder() {
  const calls = [];
  const fn = (...args) => calls.push(args);
  fn.calls = calls;
  return fn;
}

function qcService(state, api) {
  const deps = {
    state,
    els: {},
    api,
    setStatus: recorder(),
    updateProgress: recorder(),
    updateStartAvailability: recorder(),
    setUploadLoading: recorder(),
  };
  return { service: createQcStageService(deps), deps };
}

describe("raw preview failure", () => {
  let state;
  const failingApi = {
    fetchPreviewByPath: async () => {
      throw new Error("HTTP 400: unsupported format");
    },
  };

  beforeEach(() => {
    state = loadedState();
  });

  test("rolls back to a no-file state", async () => {
    const { service } = qcService(state, failingApi);
    const ok = await service.handleRawFilePath("/data/new.mat", "new.mat");

    assert.equal(ok, false);
    assert.equal(state.file, null);
    assert.equal(state.uploadToken, null);
    assert.deepEqual(state.previewSeries, []);
    assert.deepEqual(state.gridSeries, []);
    assert.deepEqual(state.gridNames, []);
    assert.deepEqual(state.channelMeans, []);
    assert.deepEqual(state.channelTraces, []);
    assert.equal(state.seriesLength, null);
    assert.deepEqual(state.discardMasks, []);
    assert.deepEqual(state.qcWindowLoading, {});
  });

  test("silent failure skips the status message", async () => {
    const { service, deps } = qcService(state, failingApi);
    const ok = await service.handleRawFilePath("/data/new.mat", "new.mat", {
      silentPreviewFailure: true,
    });

    assert.equal(ok, false);
    assert.ok(
      !deps.setStatus.calls.some(([text]) => text === "Preview failed"),
      "no 'Preview failed' status for a silent attempt",
    );
    assert.equal(state.file, null);
  });
});

describe("decomposition upload-token recovery", () => {
  const streamBody = () => new Response('{"stage":"done","pct":100}\n').body;

  function runDeps(state, api) {
    return {
      state,
      api,
      getBidsProject: () => "",
      collectBidsEntities: () => ({}),
      buildParams: () => ({ niter: 1 }),
      updateStartAvailability: () => {},
      switchStage: () => {},
      setStatus: recorder(),
      updateProgress: recorder(),
      handleStreamMessageFn: recorder(),
    };
  }

  test("re-mints the token from the file path and retries once", async () => {
    const state = loadedState();
    const sentTokens = [];
    const previewPaths = [];
    const api = {
      decomposeStream: async (formData) => {
        sentTokens.push(formData.get("upload_token"));
        if (sentTokens.length === 1) {
          throw new Error("HTTP 400: upload_token Token expired or missing");
        }
        return { body: streamBody() };
      },
      fetchPreviewByPath: async (path) => {
        previewPaths.push(path);
        return { upload_token: "fresh-token" };
      },
    };
    state.rois = [{ start: 0, end: 3 }];
    const deps = runDeps(state, api);

    await runDecomposition(deps);

    assert.deepEqual(previewPaths, ["/data/old.otb+"]);
    assert.deepEqual(sentTokens, ["old-token", "fresh-token"]);
    assert.equal(state.uploadToken, "fresh-token");
    // User selections survive the reload.
    assert.deepEqual(state.rois, [{ start: 0, end: 3 }]);
    assert.deepEqual(state.discardMasks, [[0, 1]]);
    assert.deepEqual(deps.handleStreamMessageFn.calls, [
      [{ stage: "done", pct: 100 }],
    ]);
    assert.equal(state.isRunning, false);
  });

  test("without a file path the original error is reported", async () => {
    const state = loadedState();
    state.file = { name: "old.otb+" };
    let previewCalls = 0;
    const api = {
      decomposeStream: async () => {
        throw new Error("HTTP 400: upload_token Token expired or missing");
      },
      fetchPreviewByPath: async () => {
        previewCalls += 1;
      },
    };
    const deps = runDeps(state, api);

    await runDecomposition(deps);

    assert.equal(previewCalls, 0);
    assert.deepEqual(deps.setStatus.calls.at(-1), [
      "Error: HTTP 400: upload_token Token expired or missing",
      "error",
    ]);
    assert.equal(state.isRunning, false);
  });

  test("other errors are not retried", async () => {
    const state = loadedState();
    let streamCalls = 0;
    const api = {
      decomposeStream: async () => {
        streamCalls += 1;
        throw new Error("HTTP 500: boom");
      },
      fetchPreviewByPath: async () => {
        throw new Error("should not be called");
      },
    };
    const deps = runDeps(state, api);

    await runDecomposition(deps);

    assert.equal(streamCalls, 1);
    assert.deepEqual(deps.setStatus.calls.at(-1), [
      "Error: HTTP 500: boom",
      "error",
    ]);
  });
});
