// Stage lifecycle, driven through the real app context with stand-in elements.
import { test, describe, beforeEach } from "node:test";
import assert from "node:assert/strict";

// config.js reads window.location at import time; layout redraws are timers
// that must not fire against the stand-in elements.
globalThis.window = {
  location: {
    port: "8080",
    protocol: "http:",
    hostname: "localhost",
    origin: "http://localhost:8080",
  },
  setTimeout: () => 0,
  requestAnimationFrame: () => 0,
};

const { state: initialState } = await import("../src/app/state.js");
const { createApp } = await import("../src/app/create-app.js");
const { renderActiveStage } = await import("../src/app/stages/lifecycle.js");

const pristine = structuredClone(initialState);

function fakeElement() {
  const classes = new Set();
  return {
    classList: {
      toggle(name, force = !classes.has(name)) {
        if (force) classes.add(name);
        else classes.delete(name);
        return force;
      },
      add: (name) => classes.add(name),
      remove: (name) => classes.delete(name),
      contains: (name) => classes.has(name),
    },
    setAttribute() {},
    dataset: {},
  };
}

function recorder() {
  const calls = [];
  const fn = (...args) => calls.push(args);
  fn.calls = calls;
  return fn;
}

let app;
let els;

beforeEach(() => {
  els = {
    stageQc: fakeElement(),
    stageRun: fakeElement(),
    stageEdit: fakeElement(),
    artifactAddBtn: fakeElement(),
  };
  app = createApp({ state: structuredClone(pristine), els, api: {} });
  app.setStatus = recorder();
  app.state.file = { name: "a.otb+", path: "/a.otb+" };
  app.state.previewSeries = [1, 2, 3];
});

const activePanels = () =>
  ["stageQc", "stageRun", "stageEdit"].filter((id) =>
    els[id].classList.contains("active"),
  );

describe("switchStage", () => {
  test("activates exactly the target panel", () => {
    app.switchStage("run");
    assert.equal(app.state.currentStage, "run");
    assert.deepEqual(activePanels(), ["stageRun"]);
    app.switchStage("qc");
    assert.deepEqual(activePanels(), ["stageQc"]);
  });

  test("without a file, QC and Run refuse silently", () => {
    app.state.file = null;
    app.switchStage("run");
    assert.equal(app.state.currentStage, "qc");
    assert.deepEqual(app.setStatus.calls, []);
  });

  test("Run without a preview is refused with a reason", () => {
    app.state.previewSeries = [];
    app.switchStage("run");
    assert.equal(app.state.currentStage, "qc");
    assert.deepEqual(app.setStatus.calls, [
      ["Run step is locked until preview is loaded", "muted"],
    ]);
  });

  test("entering Edit without data says so but still enters", () => {
    app.switchStage("edit");
    assert.equal(app.state.currentStage, "edit");
    assert.deepEqual(app.setStatus.calls, [
      ["Load a decomposition file to edit", "muted"],
    ]);
  });

  test("leaving QC disarms artifact selection", () => {
    app.state.artifactMode = true;
    els.artifactAddBtn.classList.add("armed");
    app.switchStage("run");
    assert.equal(app.state.artifactMode, false);
    assert.equal(els.artifactAddBtn.classList.contains("armed"), false);
  });

  test("leaving Edit clears the armed edit mode", () => {
    app.state.edit.distimes = [[1]];
    app.switchStage("edit");
    app.setEditMode("add", "Drag a box on pulse train to add spikes");
    app.setEditStatus = recorder();
    app.switchStage("qc");
    assert.equal(app.state.edit.mode, null);
    assert.deepEqual(app.setEditStatus.calls, [["", "muted"]]);
  });

  test("leaving Edit keeps the loaded decomposition", () => {
    app.state.edit.distimes = [[1, 2]];
    app.switchStage("edit");
    app.switchStage("qc");
    assert.deepEqual(app.state.edit.distimes, [[1, 2]]);
  });

  test("re-selecting the current stage runs no hooks", () => {
    app.switchStage("edit");
    app.setStatus.calls.length = 0;
    app.state.edit.mode = "add";
    app.switchStage("edit");
    assert.equal(app.state.edit.mode, "add");
    assert.deepEqual(app.setStatus.calls, []);
  });
});

describe("renderActiveStage", () => {
  test("draws only the visible stage", () => {
    app.renderChannelQC = recorder();
    app.refreshVisuals = recorder();
    app.renderMuExplorer = recorder();
    app.renderEditExplorer = recorder();
    app.switchStage("run");
    renderActiveStage(app);
    assert.equal(app.renderMuExplorer.calls.length, 1);
    assert.equal(app.renderChannelQC.calls.length, 0);
    assert.equal(app.renderEditExplorer.calls.length, 0);
  });

  test("Edit draws only once data is loaded", () => {
    app.renderEditExplorer = recorder();
    app.switchStage("edit");
    renderActiveStage(app);
    assert.equal(app.renderEditExplorer.calls.length, 0);
    app.state.edit.distimes = [[1]];
    renderActiveStage(app);
    assert.equal(app.renderEditExplorer.calls.length, 1);
  });
});

describe("createApp", () => {
  test("merges every service into one context", () => {
    for (const name of [
      "setStatus",
      "persistNpzBySaveTarget",
      "handleRawFilePath",
      "runDecomposition",
      "loadDecompositionForEditByPath",
    ]) {
      assert.equal(typeof app[name], "function", name);
    }
  });
});
