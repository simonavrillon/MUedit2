// The edit stage: a command runs the same from its button and from its key,
// and a redraw fetches the pulse window on screen and keeps the dropdowns.
import { test, describe, beforeEach } from "node:test";
import assert from "node:assert/strict";

import {
  dispatchWindow,
  fakeCanvas,
  fakeElement,
  recorder,
  resetDom,
} from "./_dom.js";
import { settle, testApp } from "./_app.js";

const { EDIT_COMMANDS, setupEditEvents } =
  await import("../src/app/stages/edit-stage.js");

let app;
let els;

/** Press `key` with focus on an element of `tagName`. */
function press(key, tagName = "BODY") {
  dispatchWindow("keydown", { key, target: { tagName } });
}

beforeEach(() => {
  resetDom();
  els = {
    editPulseCanvas: fakeCanvas(),
    editDrCanvas: fakeCanvas(),
    editTimelineCanvas: fakeCanvas(),
  };
  for (const { button } of EDIT_COMMANDS) els[button] = fakeElement("button");
  app = testApp({ els });
  app.state.currentStage = "edit";
});

describe("edit commands", () => {
  beforeEach(() => {
    app.renderEditExplorer = recorder();
    setupEditEvents(app);
  });

  test("a mode's key arms the same mode as its button", () => {
    els.editAddBtn.dispatch("click");
    assert.equal(app.state.edit.mode, "add");
    press("d");
    assert.equal(app.state.edit.mode, "delete_spikes");
    press("X");
    assert.equal(app.state.edit.mode, "add_artifact");
  });

  test("a toggle's key flips it like a click", () => {
    assert.equal(els.editPeelOffToggle.dataset.state, "off");
    press("p");
    assert.equal(els.editPeelOffToggle.dataset.state, "on");
    els.editPeelOffToggle.dispatch("click");
    assert.equal(els.editPeelOffToggle.dataset.state, "off");
  });

  test("an edit's key shows its button busy while it runs", async () => {
    app.state.edit.spikeTimes = [Int32Array.from([1, 5, 9, 13])];
    app.state.edit.token = "tok";
    /** @type {(value: unknown) => void} */
    let answer = () => {};
    app.api = {
      editOp: () => new Promise((resolve) => (answer = resolve)),
    };
    press("r");
    assert.equal(els.editOutliersBtn.dataset.busy, "1");
    answer({
      meta: { removed_count: 0, n_mu: 1 },
      spikeTimes: [],
    });
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert.equal(els.editOutliersBtn.dataset.busy, undefined);
    assert.deepEqual(app.setStatus.calls.at(-1), [
      "No outliers detected",
      "muted",
    ]);
  });

  test("a second edit waits for the first to finish", async () => {
    app.state.edit.spikeTimes = [Int32Array.from([1, 5, 9, 13])];
    app.state.edit.token = "tok";
    app.state.edit.totalSamples = 20;
    /** @type {(value: unknown) => void} */
    let answer = () => {};
    const sent = [];
    app.api = {
      editOp: (op) => {
        sent.push(op);
        return new Promise((resolve) => (answer = resolve));
      },
    };
    press("r");
    press(" ");
    assert.deepEqual(sent, ["remove-outliers"]);
    assert.deepEqual(app.setStatus.calls.at(-1), [
      "Wait for the current edit to finish",
      "muted",
    ]);
    answer({
      meta: { removed_count: 0, n_mu: 1 },
      spikeTimes: [],
    });
    await new Promise((resolve) => setTimeout(resolve, 0));
    press(" ");
    assert.deepEqual(sent, ["remove-outliers", "update-filter"]);
  });

  test("save is enabled while a session is open", () => {
    assert.equal(els.editSaveBtn.disabled, true);
    app.state.edit.token = "tok";
    app.refreshEditModeButtons();
    assert.equal(els.editSaveBtn.disabled, false);
    app.resetEditState();
    assert.equal(els.editSaveBtn.disabled, true);
  });

  test("keys are ignored on the landing page", () => {
    els.workspace = fakeElement();
    els.workspace.hidden = true;
    press("a");
    assert.equal(app.state.edit.mode, null);
    els.workspace.hidden = false;
    press("a");
    assert.equal(app.state.edit.mode, "add");
  });

  test("keys are ignored while typing and off the edit page", () => {
    press("a", "INPUT");
    assert.equal(app.state.edit.mode, null);
    app.state.currentStage = "qc";
    press("a");
    assert.equal(app.state.edit.mode, null);
  });

  test("arrow keys still zoom the view", () => {
    app.state.edit.spikeTimes = [Int32Array.from([1])];
    app.state.edit.totalSamples = 1000;
    press("ArrowUp");
    assert.deepEqual(app.state.edit.view, { start: 100, end: 900 });
    assert.equal(app.renderEditExplorer.calls.length, 1);
  });
});

describe("redrawing the edit page", () => {
  /** MU 0's window over `view`, as /series/pulse sends it. */
  const pulseWindow = ({ start, end }) => ({
    mu: 0,
    version: 1,
    start,
    end,
    bins: 254,
    flagged: false,
    row: new Float32Array(end - start),
    spikes: new Int32Array(0),
    spikeValues: new Float32Array(0),
    artifacts: new Int32Array(0),
    artifactValues: new Float32Array(0),
  });
  let asked;

  beforeEach(() => {
    asked = [];
    app.api = {
      fetchPulse: async (params) => (asked.push(params), pulseWindow(params)),
    };
    const view = { start: 0, end: 255 };
    Object.assign(app.state.edit, {
      token: "tok",
      spikeTimes: [Int32Array.from([100, 200])],
      flagged: [false],
      muUids: ["g0_mu0"],
      muGridIndex: [0],
      versions: [1],
      fsamp: 1000,
      totalSamples: 1000,
      view,
      pulseView: pulseWindow(view),
    });
  });

  test("asks for the window when the view moved, and draws it when it lands", async () => {
    app.state.edit.view = { start: 300, end: 554 };
    app.renderEditExplorer();
    assert.deepEqual(asked, [
      { token: "tok", mu: 0, start: 300, end: 554, bins: 254 },
    ]);
    await settle();
    assert.equal(app.state.edit.pulseView.start, 300);
    app.renderEditExplorer();
    assert.equal(asked.length, 1, "the window shown is not asked for again");
  });

  test("an edit of the MU asks for its window again", () => {
    app.state.edit.versions = [2];
    app.renderEditExplorer();
    assert.equal(asked.length, 1);
  });

  test("a view past the end of a shorter pulse is reset", () => {
    app.state.edit.view = { start: 0, end: 5000 };
    app.renderEditExplorer();
    assert.deepEqual(app.state.edit.view, { start: 0, end: 1000 });
  });

  test("an open MU dropdown is not rebuilt by a redraw", () => {
    app.state.gridNames = ["A"];
    Object.assign(app.state.edit, {
      spikeTimes: [Int32Array.from([100]), Int32Array.from([200])],
      muGridIndex: [0, 0],
      flagged: [false, false],
      versions: [1, 1],
    });
    els.editMuGridSelect = fakeElement("select");
    els.editMuSelect = fakeElement("select");
    app.renderEditExplorer();
    assert.equal(els.editMuSelect.children.length, 2);
    els.editMuSelect.children.push("untouched");
    app.renderEditExplorer();
    assert.equal(els.editMuSelect.children.at(-1), "untouched");
    app.state.edit.currentMu = 1;
    app.renderEditExplorer();
    assert.equal(els.editMuSelect.children.length, 2);
    assert.equal(els.editMuSelect.value, "1");
  });
});
