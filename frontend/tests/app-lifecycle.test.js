// Stage lifecycle: which page the user may enter, what entering and leaving
// one resets, and which page draws, through the real app context.
import { test, describe, beforeEach } from "node:test";
import assert from "node:assert/strict";

import { fakeElement, recorder } from "./_dom.js";
import { testApp } from "./_app.js";

const { renderActiveStage } = await import("../src/app/stages/lifecycle.js");
const { createRunLive } = await import("../src/decomp/live.js");

let app;
let els;

beforeEach(() => {
  els = {
    stageQc: fakeElement(),
    stageRun: fakeElement(),
    stageEdit: fakeElement(),
    artifactAddBtn: fakeElement(),
  };
  app = testApp({ els });
  app.state.file = { name: "a.otb+", path: "/a.otb+" };
  app.state.gridSeries = [new Float32Array([1, 2, 3])];
  app.state.runLive = createRunLive(0);
});

const activePanels = () =>
  ["stageQc", "stageRun", "stageEdit"].filter((id) =>
    els[id].classList.contains("active"),
  );

describe("switchStage", () => {
  test("a finished message goes with the page it was about", () => {
    els.status = { dataset: { tone: "success" } };
    app.switchStage("run");
    assert.deepEqual(app.setStatus.calls.at(-1), ["", "muted"]);

    app.setStatus.calls.length = 0;
    els.status.dataset.tone = "error";
    app.switchStage("qc");
    els.status.dataset.tone = "muted";
    app.switchStage("run");
    assert.deepEqual(app.setStatus.calls, []);
  });

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
    app.state.gridSeries = [];
    app.switchStage("run");
    assert.equal(app.state.currentStage, "qc");
    assert.deepEqual(app.setStatus.calls, [
      ["Run step is locked until preview is loaded", "muted"],
    ]);
  });

  test("Run before any run has started refuses silently", () => {
    app.state.runLive = null;
    app.switchStage("run");
    assert.equal(app.state.currentStage, "qc");
    assert.deepEqual(app.setStatus.calls, []);
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
    app.state.edit.spikeTimes = [[1]];
    app.switchStage("edit");
    app.setEditMode("add", "Drag a box on pulse train to add discharges");
    app.setStatus = recorder();
    app.switchStage("qc");
    assert.equal(app.state.edit.mode, null);
    assert.deepEqual(app.setStatus.calls, [["", "muted"]]);
  });

  test("leaving Edit keeps the loaded decomposition", () => {
    app.state.edit.spikeTimes = [[1, 2]];
    app.switchStage("edit");
    app.switchStage("qc");
    assert.deepEqual(app.state.edit.spikeTimes, [[1, 2]]);
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

describe("showWorkspace", () => {
  test("switches to the target once, and hides the landing after it draws", () => {
    const frames = [];
    globalThis.window.requestAnimationFrame = (fn) => frames.push(fn);
    try {
      els.workspace = fakeElement();
      els.workspace.hidden = true;
      els.landing = fakeElement();
      const switched = [];
      const switchStage = app.switchStage;
      app.switchStage = (target) => {
        switched.push(target);
        switchStage(target);
      };
      app.renderRunStage = recorder();
      app.showWorkspace("run");
      assert.deepEqual(switched, ["run"]);
      assert.ok(!els.workspace.hidden);
      assert.ok(!els.landing.hidden);
      // The page's redraw runs first in the frame, then the landing goes.
      frames.shift()();
      assert.equal(app.renderRunStage.calls.length, 1);
      assert.ok(!els.landing.hidden);
      frames.shift()();
      assert.ok(els.landing.hidden);
    } finally {
      globalThis.window.requestAnimationFrame = () => 0;
    }
  });
});

describe("renderActiveStage", () => {
  test("draws only the visible stage", () => {
    app.renderChannelQC = recorder();
    app.refreshVisuals = recorder();
    app.renderRunStage = recorder();
    app.renderEditExplorer = recorder();
    app.switchStage("run");
    renderActiveStage(app);
    assert.equal(app.renderRunStage.calls.length, 1);
    assert.equal(app.renderChannelQC.calls.length, 0);
    assert.equal(app.renderEditExplorer.calls.length, 0);
  });

  test("Edit draws only once data is loaded", () => {
    app.renderEditExplorer = recorder();
    app.switchStage("edit");
    renderActiveStage(app);
    assert.equal(app.renderEditExplorer.calls.length, 0);
    app.state.edit.spikeTimes = [[1]];
    renderActiveStage(app);
    assert.equal(app.renderEditExplorer.calls.length, 1);
  });
});
