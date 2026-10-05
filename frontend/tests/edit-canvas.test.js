// Edit-stage canvases: drag gestures to selections, the navigation timeline,
// and the bookmark, driven through the real app context.
import { test, describe, beforeEach } from "node:test";
import assert from "node:assert/strict";

import {
  fakeCanvas,
  fakeElement,
  installDom,
  ops,
  recorder,
  resetDom,
  texts,
} from "./fake-dom.js";

installDom();
const { COLORS } = await import("../src/config.js");
const { state: initialState } = await import("../src/state/state.js");
const { createApp } = await import("../src/app/create-app.js");
const {
  bindEditCanvas,
  bindEditTimeline,
  drawEditPulse,
  drawEditRates,
  drawEditTimeline,
} = await import("../src/view/edit-canvas.js");

const pristine = structuredClone(initialState);

// With axes the pulse plot starts 38 px in and is 254 px wide, from the view's
// first sample to its last, so a view of 255 samples maps one sample to one
// pixel: sample = px - 38.
const PLOT_LEFT = 38;
const PLOT_TOP = 8;

let app;
let els;

/** The fetched window of MU 0 over `view`: samples `i % 7`, spikes 100 and 200. */
function pulseWindow(view, version = 1) {
  const n = view.end - view.start;
  const spikes = Int32Array.from(
    [100, 200].filter((t) => t >= view.start && t < view.end),
  );
  return {
    mu: 0,
    version,
    start: view.start,
    end: view.end,
    bins: 254,
    flagged: false,
    row: Float32Array.from({ length: n }, (_, i) => (view.start + i) % 7),
    spikes,
    spikeValues: Float32Array.from(spikes, (t) => t % 7),
    artifacts: new Int32Array(0),
    artifactValues: new Float32Array(0),
  };
}

/**
 * The app over an edit session of one MU. With `stage`, `renderEditExplorer`
 * is the edit stage's own; otherwise a recorder.
 */
function editApp({
  samples = 1000,
  view = { start: 0, end: 255 },
  api = {},
  stage = false,
} = {}) {
  const state = structuredClone(pristine);
  Object.assign(state.edit, {
    token: "tok",
    distimes: [Int32Array.from([100, 200])],
    artifactTimes: [new Int32Array(0)],
    flagged: [false],
    muUids: ["g0_mu0"],
    muGridIndex: [0],
    versions: [1],
    fsamp: 1000,
    totalSamples: samples,
    view,
    pulseView: pulseWindow(view),
  });
  els = {
    editPulseCanvas: fakeCanvas({ left: 10, top: 20 }),
    editDrCanvas: fakeCanvas(),
    editTimelineCanvas: fakeCanvas({ width: 346, height: 20 }),
  };
  const built = createApp({ state, els, api });
  Object.assign(built, {
    setEditStatus: recorder(),
    addSpikesInSelection: recorder(),
    addArtifactInSelection: recorder(),
    deleteSpikesInSelection: recorder(),
    scheduleEditRender: recorder(),
  });
  if (!stage) built.renderEditExplorer = recorder();
  return built;
}

/** Drag on the pulse canvas between two points given in plot pixels. */
function dragPulse(from, to) {
  const canvas = els.editPulseCanvas;
  const at = ([x, y]) => ({
    clientX: 10 + PLOT_LEFT + x,
    clientY: 20 + PLOT_TOP + y,
  });
  canvas.dispatch("pointerdown", at(from));
  canvas.dispatch("pointermove", at(to));
  canvas.dispatch("pointerup");
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
    app.state.edit.view = { start: 500, end: 755 };
    app.state.edit.mode = "delete_spikes";
    dragPulse([50, 10], [150, 60]);
    const [[sel]] = app.deleteSpikesInSelection.calls;
    assert.deepEqual([sel.start, sel.end], [550, 650]);
  });

  test("moving the pointer updates the draft and schedules a redraw", () => {
    const canvas = els.editPulseCanvas;
    canvas.dispatch("pointerdown", {
      clientX: 10 + PLOT_LEFT + 50,
      clientY: 38,
    });
    canvas.dispatch("pointermove", {
      clientX: 10 + PLOT_LEFT + 80,
      clientY: 58,
    });
    assert.deepEqual(
      [
        app.state.edit.draftSelectionPulse?.start,
        app.state.edit.draftSelectionPulse?.end,
      ],
      [50, 80],
    );
    assert.equal(app.scheduleEditRender.calls.length, 1);
    assert.equal(app.renderEditExplorer.calls.length, 0);
  });

  test("the canvas captures the pointer for the drag", () => {
    const canvas = els.editPulseCanvas;
    canvas.dispatch("pointerdown", { pointerId: 7, clientX: 60, clientY: 38 });
    assert.equal(canvas.capturedPointer, 7);
  });

  test("a cancelled gesture drops the draft and applies nothing", () => {
    app.state.edit.mode = "add";
    const canvas = els.editPulseCanvas;
    canvas.dispatch("pointerdown", {
      clientX: 10 + PLOT_LEFT + 50,
      clientY: 38,
    });
    canvas.dispatch("pointermove", {
      clientX: 10 + PLOT_LEFT + 80,
      clientY: 58,
    });
    canvas.dispatch("pointercancel");
    canvas.dispatch("pointerup");
    assert.equal(app.state.edit.draftSelectionPulse, null);
    assert.equal(app.addSpikesInSelection.calls.length, 0);
    assert.equal(app.state.edit.mode, "add");
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

  test("a near-click without a mode leaves no box drawn", () => {
    dragPulse([100, 30], [103, 34]);
    assert.equal(app.state.edit.draftSelectionPulse, null);
    assert.equal(app.state.edit.selectionPulse, null);
    assert.equal(app.scheduleEditRender.calls.length, 2);
  });

  test("an armed box is cleared from the plot as it is sent", () => {
    app.state.edit.mode = "add";
    dragPulse([50, 10], [150, 60]);
    assert.equal(app.addSpikesInSelection.calls.length, 1);
    assert.equal(app.state.edit.draftSelectionPulse, null);
    // Redrawn after the move and after the release, without the box.
    assert.equal(app.scheduleEditRender.calls.length, 2);
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

  test("with nothing open the gesture is ignored", () => {
    app.state.edit.totalSamples = 0;
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

describe("timeline", () => {
  // 346 px wide: a 38 px left pad and an 8 px right pad leave a 300 px bar,
  // so each pixel of bar is 10 samples of a 3000-sample recording.
  beforeEach(() => {
    app = editApp({ samples: 3000, view: { start: 0, end: 300 } });
    bindEditTimeline(app);
  });

  test("a click centres the view on that point, keeping its width", () => {
    els.editTimelineCanvas.dispatch("pointerdown", { clientX: 38 + 150 });
    els.editTimelineCanvas.dispatch("pointerup", { clientX: 38 + 150 });
    assert.deepEqual(app.state.edit.view, { start: 1350, end: 1650 });
  });

  test("a click near an edge keeps the view inside the recording", () => {
    els.editTimelineCanvas.dispatch("pointerdown", { clientX: 40 });
    els.editTimelineCanvas.dispatch("pointerup", { clientX: 40 });
    assert.deepEqual(app.state.edit.view, { start: 0, end: 300 });
  });

  test("dragging pans the view by the dragged distance", () => {
    els.editTimelineCanvas.dispatch("pointerdown", { clientX: 100 });
    els.editTimelineCanvas.dispatch("pointermove", { clientX: 130 });
    els.editTimelineCanvas.dispatch("pointerup", { clientX: 130 });
    assert.deepEqual(app.state.edit.view, { start: 300, end: 600 });
  });

  test("panning stops at the end of the recording", () => {
    app.state.edit.view = { start: 2800, end: 3000 };
    els.editTimelineCanvas.dispatch("pointerdown", { clientX: 100 });
    els.editTimelineCanvas.dispatch("pointermove", { clientX: 130 });
    assert.deepEqual(app.state.edit.view, { start: 2800, end: 3000 });
  });

  test("a jitter under 4 px counts as a click, not a pan", () => {
    els.editTimelineCanvas.dispatch("pointerdown", { clientX: 38 + 150 });
    els.editTimelineCanvas.dispatch("pointermove", { clientX: 38 + 152 });
    els.editTimelineCanvas.dispatch("pointerup", { clientX: 38 + 150 });
    assert.deepEqual(app.state.edit.view, { start: 1350, end: 1650 });
  });

  test("draws the last edit, the spikes and the view window", () => {
    Object.assign(app.state.edit, {
      distimes: [Int32Array.from([0, 1500])],
      editHistory: [
        { mu_uid: "g0_mu0", spikes_added: [900] },
        { mu_uid: "g0_mu1", spikes_added: [1200] },
        { mu_uid: "g0_mu0", spikes_added: [300], spikes_removed: [600] },
      ],
    });
    drawEditTimeline(els, app.state);
    const rects = ops(els.editTimelineCanvas.ctx, "fillRect").map((r) => [
      r.fillStyle,
      ...r.args,
    ]);
    assert.deepEqual(rects, [
      [COLORS.timelineTrack, 38, 4, 300, 12],
      [COLORS.timelineAdded, 68, 4, 2, 12],
      [COLORS.timelineRemoved, 98, 4, 2, 12],
      [COLORS.timelineSpikes, 38, 4, 2, 12],
      [COLORS.timelineSpikes, 188, 4, 2, 12],
      [COLORS.timelineViewFill, 38, 2, 30, 16],
    ]);
  });
});

describe("pulse plot", () => {
  test("marks where the user stopped, midway across the view", () => {
    app.state.edit.bookmarkPosition = { muIdx: 0, position: 127 };
    app.state.edit.showBookmark = true;
    drawEditPulse(els, app.state);
    const label = ops(els.editPulseCanvas.ctx, "fillText").find(
      (c) => c.args[0] === "You stopped here",
    );
    assert.deepEqual(label?.args, ["You stopped here", 165, 23]);
  });

  test("the bookmark is hidden when dismissed or for another MU", () => {
    app.state.edit.bookmarkPosition = { muIdx: 0, position: 127 };
    app.state.edit.showBookmark = false;
    drawEditPulse(els, app.state);
    app.state.edit.bookmarkPosition = { muIdx: 3, position: 127 };
    app.state.edit.showBookmark = true;
    drawEditPulse(els, app.state);
    assert.ok(!texts(els.editPulseCanvas.ctx).includes("You stopped here"));
  });

  test("draws the fetched window with its discharges on it", () => {
    drawEditPulse(els, app.state);
    const ctx = els.editPulseCanvas.ctx;
    const arcs = ops(ctx, "arc");
    assert.equal(arcs.length, 2);
    // Sample t at x = 38 + t, value t % 7 on a 0..6 range (100 → 2, 200 → 4).
    assert.deepEqual(
      arcs.map((a) => a.args.slice(0, 2).map((v) => Math.round(v))),
      [
        [138, 56],
        [238, 32],
      ],
    );
  });

  test("a flagged MU is a flat line with its discharges at 0", () => {
    app.state.edit.flagged = [true];
    drawEditPulse(els, app.state);
    const ctx = els.editPulseCanvas.ctx;
    const bottom = PLOT_TOP + 72;
    assert.ok(ops(ctx, "arc").every((a) => a.args[1] === bottom));
  });

  test("asks for the window when the view moved, and draws it when it lands", async () => {
    const asked = [];
    app = editApp({
      api: {
        fetchPulse: async (params) => {
          asked.push(params);
          return pulseWindow({ start: params.start, end: params.end }, 1);
        },
      },
      stage: true,
    });
    app.state.currentStage = "edit";
    app.state.edit.view = { start: 300, end: 554 };
    app.renderEditExplorer();
    assert.deepEqual(asked, [
      { token: "tok", mu: 0, start: 300, end: 554, bins: 254 },
    ]);
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert.equal(app.state.edit.pulseView.start, 300);
    app.renderEditExplorer();
    assert.equal(asked.length, 1, "the window shown is not asked for again");
  });

  test("an edit of the MU asks for its window again", () => {
    const asked = [];
    app = editApp({
      api: { fetchPulse: async (p) => (asked.push(p), new Promise(() => {})) },
      stage: true,
    });
    app.state.edit.versions = [2];
    app.renderEditExplorer();
    assert.equal(asked.length, 1);
  });

  test("drawing changes nothing and asks the server for nothing", () => {
    const asked = [];
    app = editApp({ api: { fetchPulse: async (p) => asked.push(p) } });
    Object.assign(app.state.edit, { view: null, versions: [2] });
    drawEditPulse(els, app.state);
    drawEditRates(els, app.state);
    drawEditTimeline(els, app.state);
    assert.equal(app.state.edit.view, null);
    assert.deepEqual(asked, []);
  });

  test("a view past the end of a shorter pulse is reset", () => {
    app = editApp({ view: { start: 0, end: 5000 }, stage: true });
    app.renderEditExplorer();
    assert.deepEqual(app.state.edit.view, { start: 0, end: 1000 });
  });
});

describe("discharge-rate plot", () => {
  test("a flagged MU shows no rate", () => {
    app.state.edit.flagged = [true];
    drawEditRates(els, app.state);
    assert.deepEqual(texts(els.editDrCanvas.ctx), ["No data"]);
  });

  test("marks each rate and scales the axis to the fastest", () => {
    drawEditRates(els, app.state);
    const ctx = els.editDrCanvas.ctx;
    assert.equal(ops(ctx, "arc").length, 1, "one interval, one rate");
    const yLabels = texts(ctx).filter((t) => !t.endsWith("s"));
    // 100 samples between discharges at 1 kHz is 10 Hz.
    assert.deepEqual(yLabels, ["0.0", "3.3", "6.7", "10.0"]);
  });
});

describe("MU dropdowns", () => {
  beforeEach(() => {
    app = editApp({
      api: { fetchPulse: () => new Promise(() => {}) },
      stage: true,
    });
    Object.assign(app.state.edit, {
      distimes: [Int32Array.from([100]), Int32Array.from([200])],
      muGridIndex: [0, 0],
      gridNames: ["A"],
    });
    els.editMuGridSelect = fakeElement("select");
    els.editMuSelect = fakeElement("select");
  });

  test("are rebuilt only when what they show changes", () => {
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
