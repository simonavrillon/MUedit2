// QC stage: ROI and artifact drags, the channel grid, the auxiliary
// channels and automatic QC, driven through the real app context.
import { test, describe, beforeEach } from "node:test";
import assert from "node:assert/strict";

import {
  dispatchWindow,
  fakeCanvas,
  fakeElement,
  installDom,
  ops,
  pathPoints,
  recorder,
  resetDom,
  texts,
} from "./fake-dom.js";

installDom();
const { COLORS, GRID_COLORS } = await import("../src/config.js");
const { state: initialState } = await import("../src/app/state.js");
const { createApp } = await import("../src/app/create-app.js");
const { buildSelections, renderArtifactControls } =
  await import("../src/view/qc-renderer.js");

const pristine = structuredClone(initialState);

let app;
let els;

beforeEach(() => {
  resetDom();
  const state = structuredClone(pristine);
  Object.assign(state, {
    seriesLength: 1000,
    rois: [
      { start: 0, end: 1000 },
      { start: 600, end: 1000 },
    ],
  });
  els = {
    emgCanvas: fakeCanvas({ width: 200, height: 80 }),
    auxCanvas: fakeCanvas({ width: 100, height: 50 }),
    auxSelector: Object.assign(fakeElement("select"), { value: "-1" }),
    qcSection: fakeElement(),
    nwindows: Object.assign(fakeElement("input"), { value: "2" }),
    artifactCount: fakeElement("span"),
    artifactAddBtn: fakeElement("button"),
    artifactRemoveBtn: fakeElement("button"),
  };
  app = createApp({ state, els, api: {} });
  Object.assign(app, {
    refreshVisuals: recorder(),
    requestQcGridWindow: recorder(),
    updateProgress: recorder(),
  });
});

/** Drag across the EMG overview from one x to another, in canvas px. */
function dragOverview(fromX, toX) {
  els.emgCanvas.dispatch("mousedown", { clientX: fromX });
  els.emgCanvas.dispatch("mousemove", { clientX: toX });
  dispatchWindow("mouseup");
}

describe("ROI drag on the overview", () => {
  beforeEach(() => app.enableRoiSelection("emgCanvas"));

  test("moves the nearest analysis window to the dragged span", () => {
    dragOverview(50, 150);
    assert.deepEqual(app.state.rois, [
      { start: 250, end: 750 },
      { start: 600, end: 1000 },
    ]);
    assert.equal(app.state.roiDraft, null);
    assert.deepEqual(app.requestQcGridWindow.calls, [[0, 250, 750]]);
    assert.deepEqual(app.updateProgress.calls.at(-1), [
      undefined,
      "ROI updated (2 windows)",
    ]);
  });

  test("picks the window whose start is nearest the drag", () => {
    app.state.rois = [
      { start: 0, end: 260 },
      { start: 300, end: 1000 },
    ];
    dragOverview(50, 150);
    assert.deepEqual(app.state.rois, [
      { start: 0, end: 260 },
      { start: 250, end: 750 },
    ]);
  });

  test("shows a draft while dragging", () => {
    els.emgCanvas.dispatch("mousedown", { clientX: 150 });
    els.emgCanvas.dispatch("mousemove", { clientX: 50 });
    assert.deepEqual(app.state.roiDraft, { start: 250, end: 750 });
    assert.equal(app.refreshVisuals.calls.length, 1);
  });

  test("in artifact mode adds an artifact window and disarms", () => {
    app.state.artifactMode = true;
    dragOverview(50, 150);
    assert.deepEqual(app.state.artifactRegions, [{ start: 250, end: 750 }]);
    assert.equal(app.state.artifactMode, false);
    assert.deepEqual(app.state.rois[0], { start: 0, end: 1000 });
    assert.deepEqual(app.updateProgress.calls.at(-1), [
      undefined,
      "Artifact window added (1 window)",
    ]);
  });

  test("a drag under 4 px is a click and changes nothing", () => {
    dragOverview(50, 52);
    assert.deepEqual(app.state.rois[0], { start: 0, end: 1000 });
    assert.equal(app.requestQcGridWindow.calls.length, 0);
  });

  test("a drag past the canvas edge is clamped", () => {
    dragOverview(-40, 400);
    assert.deepEqual(app.state.rois[0], { start: 0, end: 1000 });
    assert.deepEqual(app.requestQcGridWindow.calls, [[0, 0, 1000]]);
  });

  test("binding twice does not double the handlers", () => {
    app.enableRoiSelection("emgCanvas");
    dragOverview(50, 150);
    assert.equal(app.requestQcGridWindow.calls.length, 1);
  });

  test("without a loaded signal the canvas ignores drags", () => {
    app.state.seriesLength = null;
    dragOverview(50, 150);
    assert.equal(app.state.roiDraft, null);
    assert.equal(app.requestQcGridWindow.calls.length, 0);
  });
});

describe("selections and artifact controls", () => {
  test("drafts are drawn with the committed windows, artifacts tagged", () => {
    Object.assign(app.state, {
      rois: [{ start: 0, end: 10 }],
      roiDraft: { start: 20, end: 30 },
      artifactRegions: [{ start: 40, end: 50 }],
      artifactDraft: { start: 60, end: 70 },
    });
    assert.deepEqual(buildSelections(app.state), [
      { start: 0, end: 10 },
      { start: 20, end: 30 },
      { start: 40, end: 50, kind: "artifact" },
      { start: 60, end: 70, kind: "artifact" },
    ]);
  });

  test("the controls show the count, the armed state and when removal is possible", () => {
    renderArtifactControls(els, app.state);
    assert.equal(els.artifactCount.textContent, "0");
    assert.equal(els.artifactRemoveBtn.disabled, true);
    assert.equal(els.artifactAddBtn.attributes["aria-pressed"], "false");

    app.state.artifactRegions = [{ start: 1, end: 2 }];
    app.state.artifactMode = true;
    renderArtifactControls(els, app.state);
    assert.equal(els.artifactCount.textContent, "1");
    assert.equal(els.artifactRemoveBtn.disabled, false);
    assert.ok(els.artifactAddBtn.classList.contains("armed"));
    assert.equal(els.artifactAddBtn.attributes["aria-pressed"], "true");
  });
});

describe("channel grid", () => {
  beforeEach(() => {
    Object.assign(app.state, {
      channelMeans: [[1, 2, 3, 4]],
      coordinates: [
        [
          [0, 0],
          [0, 1],
          [1, 0],
          [1, 1],
        ],
      ],
      discardMasks: [[0, 1, 0, 0]],
      channelTraces: [
        [
          [0, 1],
          [1, 0],
          [0, 0],
          [2, 2],
        ],
      ],
    });
  });

  const cellsOf = () => els.qcSection.children[0].children[0];

  test("lays channels out at their electrode positions", async () => {
    await app.renderChannelQC(true);
    const cells = cellsOf();
    assert.equal(
      cells.style.gridTemplateColumns,
      "repeat(2, minmax(26px, 1fr))",
    );
    assert.equal(cells.children.length, 4);
    assert.deepEqual(
      cells.children.map((c) => [c.style.gridRow, c.style.gridColumn]),
      [
        ["1", "1"],
        ["1", "2"],
        ["2", "1"],
        ["2", "2"],
      ],
    );
    assert.equal(cells.children[0].title, "Channel 1 • mean |EMG| 1.000");
  });

  test("discarded channels are marked and drawn amber", async () => {
    await app.renderChannelQC(true);
    const [kept, discarded] = cellsOf().children;
    assert.ok(discarded.classList.contains("off"));
    assert.ok(!kept.classList.contains("off"));
    const strokeOf = (cell) =>
      ops(cell.children[1].ctx, "stroke")[0].strokeStyle;
    assert.equal(strokeOf(kept), COLORS.primary);
    assert.equal(strokeOf(discarded), COLORS.warning);
  });

  test("clicking a channel toggles it and redraws the grid", async () => {
    await app.renderChannelQC(true);
    cellsOf().children[0].dispatch("click");
    assert.deepEqual(app.state.discardMasks[0], [1, 1, 0, 0]);
    assert.ok(cellsOf().children[0].classList.contains("off"));
  });

  test("channels without a position fill the grid row by row", async () => {
    app.state.coordinates = [
      [
        [0, 0],
        [0, 1],
      ],
    ];
    await app.renderChannelQC(true);
    assert.deepEqual(
      cellsOf().children.map((c) => [c.style.gridRow, c.style.gridColumn]),
      [
        ["1", "1"],
        ["1", "2"],
        ["2", "1"],
        ["2", "2"],
      ],
    );
  });

  test("missing traces are fetched for the first analysis window", async () => {
    app.state.channelTraces = [];
    await app.renderChannelQC(true);
    assert.deepEqual(app.requestQcGridWindow.calls, [[0, 0, 1000]]);
  });

  test("a grid index past the data falls back to the first grid", async () => {
    app.state.currentGrid = 5;
    await app.renderChannelQC(true);
    assert.equal(app.state.currentGrid, 0);
    assert.equal(cellsOf().children.length, 4);
  });
});

describe("auxiliary channels", () => {
  beforeEach(() => {
    Object.assign(app.state, {
      auxSeries: [
        [0, 10],
        [5, 20],
      ],
      auxNames: ["Force", ""],
      rois: [{ start: 0, end: 500 }],
    });
  });

  test("the selector lists every channel, naming unnamed ones", () => {
    app.populateAuxSelector();
    assert.deepEqual(
      els.auxSelector.children.map((o) => [o.value, o.textContent]),
      [
        ["0", "Force"],
        ["1", "Aux 2"],
      ],
    );
  });

  test("all channels share one scale, each labelled in its colour", () => {
    app.renderAuxiliaryChannels();
    const ctx = els.auxCanvas.ctx;
    assert.deepEqual(pathPoints(ctx), [
      ["moveTo", 0, 50],
      ["lineTo", 100, 25],
      ["moveTo", 0, 37.5],
      ["lineTo", 100, 0],
    ]);
    const labels = ops(ctx, "fillText").map((c) => [
      c.args[0],
      c.args[2],
      c.fillStyle,
    ]);
    assert.deepEqual(labels, [
      ["Force", 12, GRID_COLORS[0]],
      ["Aux 2", 24, GRID_COLORS[1]],
    ]);
  });

  test("picking one channel rescales to it alone", () => {
    els.auxSelector.value = "1";
    app.renderAuxiliaryChannels();
    const ctx = els.auxCanvas.ctx;
    assert.deepEqual(pathPoints(ctx), [
      ["moveTo", 0, 50],
      ["lineTo", 100, 0],
    ]);
    assert.deepEqual(texts(ctx), ["Aux 2"]);
  });

  test("the analysis windows are drawn behind the channels", () => {
    app.renderAuxiliaryChannels();
    const [roi] = ops(els.auxCanvas.ctx, "fillRect");
    assert.deepEqual(roi.args, [0, 0, 50, 50]);
    assert.equal(roi.fillStyle, COLORS.roiFill);
  });

  test("no auxiliary data says so", () => {
    app.state.auxSeries = [];
    app.renderAuxiliaryChannels();
    assert.deepEqual(texts(els.auxCanvas.ctx), ["No auxiliary data"]);
  });
});

describe("automatic QC", () => {
  test("stores the backend's [start, end] artifact pairs as spans", async () => {
    app.state.uploadToken = "tok";
    app.state.channelMeans = [[1, 1]];
    app.api.runAutoQc = async () => ({
      bad_channels_per_grid: [[0, 1]],
      artifact_regions: [
        [100, 200],
        [400, 450],
      ],
    });
    app.setStatus = recorder();
    assert.equal(await app.runAutoQc(), true);
    assert.deepEqual(app.state.artifactRegions, [
      { start: 100, end: 200 },
      { start: 400, end: 450 },
    ]);
    assert.deepEqual(app.state.discardMasks, [[0, 1]]);
  });
});
