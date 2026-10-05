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

const { state: initialState } = await import("../src/state/state.js");
const { createApp } = await import("../src/app/create-app.js");
const { runDecomposition } = await import("../src/decomp/run.js");
const { ApiError } = await import("../src/app/http.js");

/** The error the backend answers for an upload it no longer holds. */
const missingUpload = () =>
  new ApiError("Request failed: upload_token Token expired or missing", {
    status: 400,
    code: "http_400",
    field: "upload_token",
  });

const pristine = structuredClone(initialState);

/** State as it looks after a previous file was loaded successfully. */
function loadedState() {
  const state = structuredClone(pristine);
  Object.assign(state, {
    file: { name: "old.otb+", path: "/data/old.otb+" },
    uploadToken: "old-token",
    gridSeries: [new Float32Array([1, 2, 3])],
    gridNames: ["GR08MM1305"],
    channelMeans: [[1, 1]],
    channelTraces: [[0.5]],
    seriesLength: 3,
    discardMasks: [[0, 1]],
  });
  return state;
}

function recorder() {
  const calls = [];
  const fn = (...args) => calls.push(args);
  fn.calls = calls;
  return fn;
}

/**
 * The real app context over stand-in elements, with the members these tests
 * observe (or that would schedule DOM work) replaced by recorders.
 */
function testApp(state, api) {
  const app = createApp({ state, els: {}, api });
  Object.assign(app, {
    setStatus: recorder(),
    switchStage: recorder(),
    handleStreamMessage: recorder(),
  });
  return app;
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

  test("a file that fails to open leaves the current one as it was", async () => {
    state.edit.token = "edit-1";
    const before = structuredClone(state);
    const app = testApp(state, failingApi);
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
    const app = testApp(state, api);
    Object.assign(app, {
      resetSessionForm: recorder(),
      applyPreviewMetadata: recorder(),
      populateAuxSelector: recorder(),
      populateGridTabs: recorder(),
      renderBidsAutoInfo: recorder(),
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
    const app = testApp(state, failingApi);
    const ok = await app.handleRawFilePath("/data/new.mat", "new.mat", {
      silentPreviewFailure: true,
    });

    assert.equal(ok, false);
    assert.ok(
      !app.setStatus.calls.some(([text]) => text.startsWith("Preview failed")),
      "no 'Preview failed' status for a silent attempt",
    );
    assert.equal(state.file.path, "/data/old.otb+");
  });
});

describe("decomposition upload-token recovery", () => {
  const streamBody = () => new Response('{"stage":"done","pct":100}\n').body;

  test("re-mints the token from the file path and retries once", async () => {
    const state = loadedState();
    const sentTokens = [];
    const previewPaths = [];
    const api = {
      decomposeStream: async (formData) => {
        sentTokens.push(formData.get("upload_token"));
        if (sentTokens.length === 1) throw missingUpload();
        return { body: streamBody() };
      },
      fetchPreviewByPath: async (path) => {
        previewPaths.push(path);
        return { upload_token: "fresh-token" };
      },
    };
    state.rois = [{ start: 0, end: 3 }];
    const app = testApp(state, api);

    await runDecomposition(app);

    assert.deepEqual(previewPaths, ["/data/old.otb+"]);
    assert.deepEqual(sentTokens, ["old-token", "fresh-token"]);
    assert.equal(state.uploadToken, "fresh-token");
    // User selections survive the reload.
    assert.deepEqual(state.rois, [{ start: 0, end: 3 }]);
    assert.deepEqual(state.discardMasks, [[0, 1]]);
    assert.deepEqual(app.handleStreamMessage.calls, [
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
        throw missingUpload();
      },
      fetchPreviewByPath: async () => {
        previewCalls += 1;
      },
    };
    const app = testApp(state, api);

    await runDecomposition(app);

    assert.equal(previewCalls, 0);
    assert.deepEqual(app.setStatus.calls.at(-1), [
      "Error: Request failed: upload_token Token expired or missing",
      "error",
    ]);
    assert.equal(state.isRunning, false);
  });

  test("an error that only mentions the token is not retried", async () => {
    const state = loadedState();
    let streamCalls = 0;
    const api = {
      decomposeStream: async () => {
        streamCalls += 1;
        throw new Error("HTTP 400: upload_token is fine, the params are not");
      },
      fetchPreviewByPath: async () => {
        throw new Error("should not be called");
      },
    };
    await runDecomposition(testApp(state, api));
    assert.equal(streamCalls, 1);
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
    const app = testApp(state, api);

    await runDecomposition(app);

    assert.equal(streamCalls, 1);
    assert.deepEqual(app.setStatus.calls.at(-1), [
      "Error: HTTP 500: boom",
      "error",
    ]);
  });
});
