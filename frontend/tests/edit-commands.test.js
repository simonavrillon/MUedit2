// The edit toolbar: a command runs the same from its button and from its key.
import { test, describe, beforeEach } from "node:test";
import assert from "node:assert/strict";

import {
  dispatchWindow,
  fakeCanvas,
  fakeElement,
  installDom,
  recorder,
  resetDom,
} from "./fake-dom.js";

installDom();
const { state: initialState } = await import("../src/state/state.js");
const { createApp } = await import("../src/app/create-app.js");
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
  const state = structuredClone(initialState);
  state.currentStage = "edit";
  app = createApp({ state, els, api: {} });
  Object.assign(app, {
    setEditStatus: recorder(),
    renderEditExplorer: recorder(),
  });
  setupEditEvents(app);
});

describe("edit commands", () => {
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
    app.state.edit.distimes = [Int32Array.from([1, 5, 9, 13])];
    app.state.edit.token = "tok";
    /** @type {(value: unknown) => void} */
    let answer = () => {};
    app.api = {
      editOp: () => new Promise((resolve) => (answer = resolve)),
    };
    press("r");
    assert.equal(els.editOutliersBtn.dataset.busy, "1");
    answer({ meta: { removed_count: 0, n_mu: 1 }, spikes: [], artifacts: [] });
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert.equal(els.editOutliersBtn.dataset.busy, undefined);
    assert.deepEqual(app.setEditStatus.calls.at(-1), [
      "No outliers detected",
      "muted",
    ]);
  });

  test("keys are ignored while typing and off the edit page", () => {
    press("a", "INPUT");
    assert.equal(app.state.edit.mode, null);
    app.state.currentStage = "qc";
    press("a");
    assert.equal(app.state.edit.mode, null);
  });

  test("arrow keys still zoom the view", () => {
    app.state.edit.distimes = [Int32Array.from([1])];
    app.state.edit.totalSamples = 1000;
    press("ArrowUp");
    assert.deepEqual(app.state.edit.view, { start: 100, end: 900 });
    assert.equal(app.renderEditExplorer.calls.length, 1);
  });
});
