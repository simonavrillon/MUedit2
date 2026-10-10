// Cancelling a decomposition from the run stage.
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
};

// The code under test logs expected failures; keep the test output readable.
console.error = () => {};

const { state: initialState } = await import("../src/state/state.js");
const { createApp } = await import("../src/app/create-app.js");
const { createRunLive } = await import("../src/decomp/live.js");
const { cancelDecomposition } = await import("../src/decomp/run.js");

function recorder() {
  const calls = [];
  const fn = (...args) => calls.push(args);
  fn.calls = calls;
  return fn;
}

function testApp(api, els = {}) {
  const state = structuredClone(initialState);
  const app = createApp({ state, els, api });
  Object.assign(app, {
    setStatus: recorder(),
    switchStage: recorder(),
  });
  return app;
}

describe("decomposition cancel", () => {
  test("asks the server to stop this tab's run", async () => {
    let cancels = 0;
    const els = { cancelRun: { disabled: false, hidden: false } };
    const app = testApp(
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

  test("does nothing when no run is going", async () => {
    let cancels = 0;
    const app = testApp({
      cancelDecomposition: async () => {
        cancels += 1;
      },
    });
    await cancelDecomposition(app);
    assert.equal(cancels, 0);
  });

  test("a failed request re-enables the button", async () => {
    const els = { cancelRun: { disabled: false, hidden: false } };
    const app = testApp(
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
    const app = testApp({});
    app.state.runLive = createRunLive(0);
    app.handleStreamMessage({
      stage: "cancelled",
      pct: 0,
      message: "Decomposition cancelled",
    });
    assert.deepEqual(app.setStatus.calls, [
      ["Decomposition cancelled", "muted"],
    ]);
    assert.equal(app.state.runLive, null);
  });

  test("a cancel seen from the run page goes back to QC", () => {
    const app = testApp({});
    app.state.runLive = createRunLive(0);
    app.state.currentStage = "run";
    app.handleStreamMessage({ stage: "cancelled" });
    assert.deepEqual(app.switchStage.calls, [["qc"]]);
  });

  test("a cancel seen from another page leaves the user there", () => {
    const app = testApp({});
    app.state.runLive = createRunLive(0);
    app.state.currentStage = "edit";
    app.updateStepAvailability = recorder();
    app.handleStreamMessage({ stage: "cancelled" });
    assert.deepEqual(app.switchStage.calls, []);
    assert.equal(app.updateStepAvailability.calls.length, 1);
  });

  test("the button shows only while a run is going", () => {
    const els = { cancelRun: { disabled: true, hidden: true } };
    const app = testApp({}, els);
    app.state.isRunning = true;
    app.updateStartAvailability();
    assert.equal(els.cancelRun.hidden, false);
    assert.equal(els.cancelRun.disabled, false);
    app.state.isRunning = false;
    app.updateStartAvailability();
    assert.equal(els.cancelRun.hidden, true);
  });
});
