// Edit-stage canvases: drag gestures to selections, the navigation timeline,
// and the bookmark, driven through the real app context.
import { test, describe, beforeEach } from "node:test";
import assert from "node:assert/strict";

import {
  dispatchWindow,
  fakeCanvas,
  installDom,
  ops,
  recorder,
  resetDom,
  texts,
} from "./fake-dom.js";

installDom();
const { state: initialState } = await import("../src/app/state.js");
const { createApp } = await import("../src/app/create-app.js");
const {
  bindEditCanvas,
  bindEditDrCanvas,
  bindEditTimeline,
  renderEditExplorer,
  renderEditTimeline,
  renderInstantaneousDr,
} = await import("../src/view/edit-canvas.js");

const pristine = structuredClone(initialState);

// With axes the pulse plot starts 38 px in and is 254 px wide, so a view of
// 254 samples maps one sample to one pixel: sample = px - 38.
const PLOT_LEFT = 38;
const PLOT_TOP = 8;

let app;
let els;

function editApp({ samples = 1000, view = { start: 0, end: 254 } } = {}) {
  const state = structuredClone(pristine);
  Object.assign(state.edit, {
    pulseTrains: [new Array(samples).fill(0).map((_, i) => i % 7)],
    distimes: [[100, 200]],
    originalDistimes: [[100, 200]],
    artifactTimes: [[]],
    flagged: [false],
    muUids: ["g0_mu0"],
    muGridIndex: [0],
    fsamp: 1000,
    totalSamples: samples,
    view,
  });
  els = {
    editPulseCanvas: fakeCanvas({ left: 10, top: 20 }),
    editDrCanvas: fakeCanvas(),
    editTimelineCanvas: fakeCanvas({ width: 346, height: 20 }),
  };
  const built = createApp({ state, els, api: {} });
  Object.assign(built, {
    setEditStatus: recorder(),
    addSpikesInSelection: recorder(),
    addArtifactInSelection: recorder(),
    deleteSpikesInSelection: recorder(),
    deleteDrInSelection: recorder(),
    renderEditExplorer: recorder(),
  });
  return built;
}

/** Drag on the pulse canvas between two points given in plot pixels. */
function dragPulse(from, to) {
  const canvas = els.editPulseCanvas;
  const at = ([x, y]) => ({
    clientX: 10 + PLOT_LEFT + x,
    clientY: 20 + PLOT_TOP + y,
  });
  canvas.dispatch("mousedown", at(from));
  canvas.dispatch("mousemove", at(to));
  dispatchWindow("mouseup");
}

beforeEach(() => {
  resetDom();
  app = editApp();
});

describe("pulse canvas drag", () => {
  beforeEach(() => bindEditCanvas(app));

  test("in add mode, the box becomes an add-spikes request", () => {
    app.state.edit.mode = "add";
    dragPulse([50, 10], [150, 60]);
    assert.deepEqual(app.addSpikesInSelection.calls, [
      [{ start: 50, end: 150, yMin: 10, yMax: 60 }],
    ]);
    assert.equal(app.state.edit.mode, null);
    assert.equal(app.state.edit.draftSelectionPulse, null);
  });

  test("a box dragged right-to-left and bottom-to-top is the same box", () => {
    app.state.edit.mode = "add";
    dragPulse([150, 60], [50, 10]);
    assert.deepEqual(app.addSpikesInSelection.calls[0][0], {
      start: 50,
      end: 150,
      yMin: 10,
      yMax: 60,
    });
  });

  test("the box is clamped to the plot area", () => {
    app.state.edit.mode = "add";
    dragPulse([100, -40], [500, 200]);
    assert.deepEqual(app.addSpikesInSelection.calls[0][0], {
      start: 100,
      end: 254,
      yMin: 0,
      yMax: 72,
    });
  });

  test("samples follow the visible window, not the pixel", () => {
    app.state.edit.view = { start: 500, end: 754 };
    app.state.edit.mode = "delete_spikes";
    dragPulse([50, 10], [150, 60]);
    const [[sel]] = app.deleteSpikesInSelection.calls;
    assert.deepEqual([sel.start, sel.end], [550, 650]);
  });

  test("moving the mouse updates the draft and redraws", () => {
    const canvas = els.editPulseCanvas;
    canvas.dispatch("mousedown", { clientX: 10 + PLOT_LEFT + 50, clientY: 38 });
    canvas.dispatch("mousemove", { clientX: 10 + PLOT_LEFT + 80, clientY: 58 });
    assert.deepEqual(
      [
        app.state.edit.draftSelectionPulse?.start,
        app.state.edit.draftSelectionPulse?.end,
      ],
      [50, 80],
    );
    assert.equal(app.renderEditExplorer.calls.length, 1);
  });

  test("without a mode the box is kept as the selection", () => {
    dragPulse([50, 10], [150, 60]);
    assert.deepEqual(app.state.edit.selectionPulse, {
      start: 50,
      end: 150,
      yMin: 10,
      yMax: 60,
    });
    assert.equal(app.addSpikesInSelection.calls.length, 0);
  });

  test("add-artifact mode sends the box as an artifact", () => {
    app.state.edit.mode = "add_artifact";
    dragPulse([50, 10], [150, 60]);
    assert.equal(app.addArtifactInSelection.calls.length, 1);
    assert.equal(app.state.edit.mode, null);
  });

  test("a click in delete mode removes spikes within 2 samples", () => {
    app.state.edit.mode = "delete_spikes";
    dragPulse([100, 30], [103, 30]);
    const [[sel]] = app.deleteSpikesInSelection.calls;
    assert.deepEqual([sel.start, sel.end], [98, 102]);
    assert.equal(app.state.edit.mode, "delete_spikes", "the mode stays armed");
  });

  test("a click in add mode asks for a drag instead", () => {
    app.state.edit.mode = "add";
    dragPulse([100, 30], [103, 30]);
    assert.equal(app.addSpikesInSelection.calls.length, 0);
    assert.deepEqual(app.setEditStatus.calls, [
      ["Drag a box to add spikes", "muted"],
    ]);
  });

  test("an MU without a pulse ignores the gesture", () => {
    app.state.edit.pulseTrains = [[]];
    app.state.edit.mode = "add";
    dragPulse([50, 10], [150, 60]);
    assert.equal(app.addSpikesInSelection.calls.length, 0);
  });

  test("double-click zooms out to the whole recording", () => {
    app.state.edit.selectionPulse = { start: 1, end: 2 };
    els.editPulseCanvas.dispatch("dblclick");
    assert.deepEqual(app.state.edit.view, { start: 0, end: 1000 });
    assert.equal(app.state.edit.selectionPulse, null);
    assert.equal(app.state.edit.showBookmark, true);
  });
});

describe("discharge-rate canvas drag", () => {
  beforeEach(() => bindEditDrCanvas(app));

  function dragDr() {
    const canvas = els.editDrCanvas;
    canvas.dispatch("mousedown", { clientX: PLOT_LEFT + 50, clientY: 18 });
    canvas.dispatch("mousemove", { clientX: PLOT_LEFT + 150, clientY: 68 });
    dispatchWindow("mouseup");
  }

  test("in delete-rate mode the box is sent for deletion", () => {
    app.state.edit.mode = "delete_dr";
    dragDr();
    assert.deepEqual(app.deleteDrInSelection.calls, [
      [{ start: 50, end: 150, yMin: 10, yMax: 60 }],
    ]);
    assert.deepEqual(app.state.edit.selectionDr, {
      start: 50,
      end: 150,
      yMin: 10,
      yMax: 60,
    });
  });

  test("otherwise the box is only kept as the selection", () => {
    dragDr();
    assert.equal(app.deleteDrInSelection.calls.length, 0);
    assert.equal(app.state.edit.selectionDr?.start, 50);
  });
});

describe("timeline", () => {
  // 346 px wide: a 38 px left pad and an 8 px right pad leave a 300 px bar,
  // so each pixel of bar is 10 samples of a 3000-sample recording.
  beforeEach(() => {
    app = editApp({ samples: 3000, view: { start: 0, end: 300 } });
    bindEditTimeline(app);
  });

  test("a click centres the view on that point, keeping its width", () => {
    els.editTimelineCanvas.dispatch("mousedown", { clientX: 38 + 150 });
    dispatchWindow("mouseup", { clientX: 38 + 150 });
    assert.deepEqual(app.state.edit.view, { start: 1350, end: 1650 });
  });

  test("a click near an edge keeps the view inside the recording", () => {
    els.editTimelineCanvas.dispatch("mousedown", { clientX: 40 });
    dispatchWindow("mouseup", { clientX: 40 });
    assert.deepEqual(app.state.edit.view, { start: 0, end: 300 });
  });

  test("dragging pans the view by the dragged distance", () => {
    els.editTimelineCanvas.dispatch("mousedown", { clientX: 100 });
    dispatchWindow("mousemove", { clientX: 130 });
    dispatchWindow("mouseup", { clientX: 130 });
    assert.deepEqual(app.state.edit.view, { start: 300, end: 600 });
  });

  test("panning stops at the end of the recording", () => {
    app.state.edit.view = { start: 2800, end: 3000 };
    els.editTimelineCanvas.dispatch("mousedown", { clientX: 100 });
    dispatchWindow("mousemove", { clientX: 130 });
    assert.deepEqual(app.state.edit.view, { start: 2800, end: 3000 });
  });

  test("a jitter under 4 px counts as a click, not a pan", () => {
    els.editTimelineCanvas.dispatch("mousedown", { clientX: 38 + 150 });
    dispatchWindow("mousemove", { clientX: 38 + 152 });
    dispatchWindow("mouseup", { clientX: 38 + 150 });
    assert.deepEqual(app.state.edit.view, { start: 1350, end: 1650 });
  });

  test("draws the last edit, the spikes and the view window", () => {
    Object.assign(app.state.edit, {
      distimes: [[0, 1500]],
      editHistory: [
        { mu_uid: "g0_mu0", spikes_added: [900] },
        { mu_uid: "g0_mu1", spikes_added: [1200] },
        { mu_uid: "g0_mu0", spikes_added: [300], spikes_removed: [600] },
      ],
    });
    renderEditTimeline(app);
    const rects = ops(els.editTimelineCanvas.ctx, "fillRect").map((r) => [
      r.fillStyle,
      ...r.args,
    ]);
    assert.deepEqual(rects, [
      ["rgba(255,255,255,0.07)", 38, 4, 300, 12],
      ["rgba(74,222,128,0.85)", 68, 4, 2, 12],
      ["rgba(248,113,113,0.85)", 98, 4, 2, 12],
      ["rgba(231,193,255,0.35)", 38, 4, 2, 12],
      ["rgba(231,193,255,0.35)", 188, 4, 2, 12],
      ["rgba(195,155,242,0.28)", 38, 2, 30, 16],
    ]);
  });
});

describe("pulse plot", () => {
  beforeEach(() => {
    app.renderEditExplorer = () => renderEditExplorer(app);
  });

  test("marks where the user stopped, midway across the view", () => {
    app.state.edit.bookmarkPosition = { muIdx: 0, position: 127 };
    app.state.edit.showBookmark = true;
    renderEditExplorer(app);
    const label = ops(els.editPulseCanvas.ctx, "fillText").find(
      (c) => c.args[0] === "You stopped here",
    );
    assert.deepEqual(label?.args, ["You stopped here", 165, 23]);
  });

  test("the bookmark is hidden when dismissed or for another MU", () => {
    app.state.edit.bookmarkPosition = { muIdx: 0, position: 127 };
    app.state.edit.showBookmark = false;
    renderEditExplorer(app);
    app.state.edit.bookmarkPosition = { muIdx: 3, position: 127 };
    app.state.edit.showBookmark = true;
    renderEditExplorer(app);
    assert.ok(!texts(els.editPulseCanvas.ctx).includes("You stopped here"));
  });

  test("a view past the end of a shorter pulse is reset", () => {
    app.state.edit.view = { start: 0, end: 5000 };
    renderEditExplorer(app);
    assert.deepEqual(app.state.edit.view, { start: 0, end: 1000 });
  });
});

describe("discharge-rate plot", () => {
  test("a flagged MU shows no rate", () => {
    app.state.edit.flagged = [true];
    renderInstantaneousDr(app);
    assert.deepEqual(texts(els.editDrCanvas.ctx), ["No data"]);
  });

  test("marks each rate and scales the axis to the fastest", () => {
    renderInstantaneousDr(app);
    const ctx = els.editDrCanvas.ctx;
    assert.equal(ops(ctx, "arc").length, 1, "one interval, one rate");
    const yLabels = texts(ctx).filter((t) => !t.endsWith("s"));
    // 100 samples between discharges at 1 kHz is 10 Hz.
    assert.deepEqual(yLabels, ["0.0", "3.3", "6.7", "10.0"]);
  });
});
