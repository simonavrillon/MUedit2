// The QC page's views: ROI and artifact drags on the overview, the channel
// grid, the auxiliary channels, and the target segments drawn and picked.
import { test, describe, beforeEach } from "node:test";
import assert from "node:assert/strict";

import {
  dispatchWindow,
  fakeCanvas,
  fakeElement,
  ops,
  pathPoints,
  recorder,
  resetDom,
  texts,
} from "./_dom.js";
import { freshState, testApp } from "./_app.js";
import { seg, segmentedApp } from "./_segments.js";

const { COLORS, traceColors } = await import("../src/config.js");
const { drawOverlayRects } = await import("../src/view/plots.js");
const {
  buildOverlays,
  enableRoiSelection,
  renderArtifactControls,
  renderSegmentTypes,
} = await import("../src/view/qc-renderer.js");
const { setupQcEvents } = await import("../src/app/stages/qc-stage.js");
const { toggleSegment } = await import("../src/signal/segments.js");

const f32 = (values) => Float32Array.from(values);

let app;
let els;

beforeEach(() => {
  resetDom();
  const state = Object.assign(freshState(), {
    totalSamples: 1000,
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
  app = testApp({ state, els });
  Object.assign(app, {
    refreshVisuals: recorder(),
    scheduleRefreshVisuals: recorder(),
    ensureQcTraces: recorder(),
  });
});

/** Drag across the EMG overview from one x to another, in canvas px. */
function dragOverview(fromX, toX) {
  els.emgCanvas.dispatch("pointerdown", { clientX: fromX });
  els.emgCanvas.dispatch("pointermove", { clientX: toX });
  els.emgCanvas.dispatch("pointerup");
}

describe("ROI drag on the overview", () => {
  beforeEach(() => enableRoiSelection(app, "emgCanvas"));

  test("a drag over a drawn window adjusts that window", () => {
    dragOverview(50, 150);
    assert.deepEqual(app.state.rois, [
      { start: 0, end: 1000 },
      { start: 250, end: 750 },
    ]);
    assert.equal(app.state.roiDraft, null);
    assert.equal(app.ensureQcTraces.calls.length, 1);
    assert.deepEqual(app.setStatus.calls.at(-1), [
      "Analysis window updated (2 windows)",
    ]);
  });

  test("drawing windows one after another fills each in turn", () => {
    els.nwindows.value = "4";
    app.state.rois = [{ start: 0, end: 1000 }];
    for (const [from, to] of [
      [10, 40],
      [50, 80],
      [90, 120],
      [130, 160],
    ]) {
      dragOverview(from, to);
    }
    assert.deepEqual(app.state.rois, [
      { start: 50, end: 200 },
      { start: 250, end: 400 },
      { start: 450, end: 600 },
      { start: 650, end: 800 },
    ]);
  });

  test("once every window is drawn, a drag elsewhere moves the nearest", () => {
    app.state.rois = [
      { start: 0, end: 100 },
      { start: 900, end: 1000 },
    ];
    dragOverview(140, 160);
    assert.deepEqual(app.state.rois, [
      { start: 0, end: 100 },
      { start: 700, end: 800 },
    ]);
  });

  test("a drag over two windows adjusts the one it overlaps most", () => {
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

  test("shows a draft while dragging, redrawn at the next frame", () => {
    els.emgCanvas.dispatch("pointerdown", { clientX: 150 });
    els.emgCanvas.dispatch("pointermove", { clientX: 50 });
    assert.deepEqual(app.state.roiDraft, { start: 250, end: 750 });
    assert.equal(app.scheduleRefreshVisuals.calls.length, 1);
    assert.equal(app.refreshVisuals.calls.length, 0);
  });

  test("a cancelled drag drops the draft and keeps the windows", () => {
    els.emgCanvas.dispatch("pointerdown", { clientX: 50 });
    els.emgCanvas.dispatch("pointermove", { clientX: 150 });
    els.emgCanvas.dispatch("pointercancel");
    els.emgCanvas.dispatch("pointerup");
    assert.equal(app.state.roiDraft, null);
    assert.deepEqual(app.state.rois[0], { start: 0, end: 1000 });
    assert.equal(app.ensureQcTraces.calls.length, 0);
  });

  test("in artifact mode adds an artifact window and disarms", () => {
    app.state.artifactMode = true;
    dragOverview(50, 150);
    assert.deepEqual(app.state.artifactRegions, [{ start: 250, end: 750 }]);
    assert.equal(app.state.artifactMode, false);
    assert.deepEqual(app.state.rois[0], { start: 0, end: 1000 });
    assert.deepEqual(app.setStatus.calls.at(-1), [
      "Artifact window added (1 window)",
    ]);
  });

  test("a drag under 4 px is a click and changes nothing", () => {
    dragOverview(50, 52);
    assert.deepEqual(app.state.rois[0], { start: 0, end: 1000 });
    assert.equal(app.ensureQcTraces.calls.length, 0);
  });

  test("a drag past the canvas edge is clamped", () => {
    dragOverview(-40, 400);
    assert.deepEqual(app.state.rois[0], { start: 0, end: 1000 });
    assert.equal(app.ensureQcTraces.calls.length, 1);
  });

  test("without a loaded signal the canvas ignores drags", () => {
    app.state.totalSamples = null;
    dragOverview(50, 150);
    assert.equal(app.state.roiDraft, null);
    assert.equal(app.ensureQcTraces.calls.length, 0);
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
    assert.deepEqual(buildOverlays(app.state), [
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
      discardChannels: [[0, 1, 0, 0]],
      channelTraces: [[f32([0, 1]), f32([1, 0]), f32([0, 0]), f32([2, 2])]],
    });
  });

  const cellsOf = () => els.qcSection.children[0].children[0];

  test("lays channels out at their electrode positions", async () => {
    await app.renderChannelQC();
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
    await app.renderChannelQC();
    const [kept, discarded] = cellsOf().children;
    assert.ok(discarded.classList.contains("off"));
    assert.ok(!kept.classList.contains("off"));
    const strokeOf = (cell) =>
      ops(cell.children[1].ctx, "stroke")[0].strokeStyle;
    assert.equal(strokeOf(kept), COLORS.primary);
    assert.equal(strokeOf(discarded), COLORS.warning);
  });

  test("clicking a channel toggles it, its cell and its trace only", async () => {
    await app.renderChannelQC();
    const cells = cellsOf();
    const [first, second] = cells.children;
    first.dispatch("click");
    assert.deepEqual(app.state.discardChannels[0], [1, 1, 0, 0]);
    assert.equal(cellsOf(), cells, "the grid is not rebuilt");
    assert.ok(first.classList.contains("off"));
    assert.equal(
      ops(first.children[1].ctx, "stroke").at(-1).strokeStyle,
      COLORS.warning,
    );
    assert.equal(ops(second.children[1].ctx, "stroke").length, 1);
    first.dispatch("click");
    assert.deepEqual(app.state.discardChannels[0], [0, 1, 0, 0]);
    assert.ok(!first.classList.contains("off"));
  });

  test("a redraw keeps the cells and follows the masks", async () => {
    await app.renderChannelQC();
    const cells = cellsOf();
    const first = cells.children[0];
    app.state.discardChannels = [[1, 0, 0, 0]];
    await app.renderChannelQC();
    assert.equal(cellsOf(), cells);
    assert.equal(cellsOf().children[0], first);
    assert.ok(first.classList.contains("off"));
    assert.ok(!cellsOf().children[1].classList.contains("off"));
  });

  test("new channel data lays the grid out again", async () => {
    await app.renderChannelQC();
    const cells = cellsOf();
    app.state.channelMeans = [[1, 2]];
    app.state.discardChannels = [[0, 0]];
    await app.renderChannelQC();
    assert.notEqual(cellsOf(), cells);
    assert.equal(cellsOf().children.length, 2);
  });

  test("channels without a position fill the grid row by row", async () => {
    app.state.coordinates = [
      [
        [0, 0],
        [0, 1],
      ],
    ];
    await app.renderChannelQC();
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

  test("drawing a grid asks for its channels' traces", async () => {
    app.state.channelTraces = [];
    await app.renderChannelQC();
    assert.equal(app.ensureQcTraces.calls.length, 1);
  });

  test("switching grid redraws the channels, not the auxiliary plot", () => {
    app.state.channelMeans.push([1, 2]);
    app.setSelectedGrid(1);
    assert.equal(cellsOf().children.length, 2);
    assert.equal(els.auxCanvas.ctx.calls.length, 0);
  });
});

describe("auxiliary channels", () => {
  beforeEach(() => {
    Object.assign(app.state, {
      auxSeries: [f32([0, 10]), f32([5, 20])],
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
      ["Force", 12, traceColors()[0]],
      ["Aux 2", 24, traceColors()[1]],
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

describe("target segments", () => {
  beforeEach(() => {
    ({ app, els } = segmentedApp());
  });

  describe("the segment-type picker", () => {
    const rows = () => els.segmentTypeMenu.children;
    const box = (i) => rows()[i].children[0];

    test("lists the types, ticked when chosen, half-ticked when partly", () => {
      app.state.segments.push(seg(950, 990, "ramp", { direction: "up" }));
      toggleSegment(app.state, 1);
      toggleSegment(app.state, 0);
      renderSegmentTypes(els, app.state);
      assert.equal(els.segmentTypeBtn.disabled, false);
      assert.equal(els.segmentTypeBtn.textContent, "— plateau");
      assert.deepEqual(
        rows().map((r) => r.children[2].textContent),
        ["up ramp (2)", "down ramp (1)", "plateau (1)", "sinusoid (1)"],
      );
      assert.deepEqual(
        [0, 1, 2, 3].map((i) => [box(i).checked, box(i).indeterminate]),
        [
          [false, true],
          [false, false],
          [true, false],
          [false, false],
        ],
      );
    });

    test("keeps its rows while the types stay, and empties without segments", () => {
      renderSegmentTypes(els, app.state);
      const first = rows()[0];
      toggleSegment(app.state, 0);
      renderSegmentTypes(els, app.state);
      assert.equal(rows()[0], first);
      assert.equal(box(0).checked, true);
      els.segmentTypeMenu.hidden = false;
      app.state.segments = [];
      renderSegmentTypes(els, app.state);
      assert.equal(rows().length, 0);
      assert.equal(els.segmentTypeBtn.disabled, true);
      assert.equal(els.segmentTypeBtn.textContent, "No segments");
      assert.equal(els.segmentTypeMenu.hidden, true);
    });

    test("a ticked box chooses its type and syncs the window count", () => {
      setupQcEvents(app);
      renderSegmentTypes(els, app.state);
      els.segmentTypeBtn.dispatch("click");
      assert.equal(els.segmentTypeMenu.hidden, false);
      box(0).checked = true;
      els.segmentTypeMenu.dispatch("change", { target: box(0) });
      assert.deepEqual(app.state.rois, [{ start: 100, end: 200 }]);
      assert.equal(els.nwindows.value, "1");
      assert.deepEqual(app.setStatus.calls.at(-1), [
        "Added up ramps — 1 window",
      ]);
      dispatchWindow("keydown", { key: "Escape" });
      assert.equal(els.segmentTypeMenu.hidden, true);
    });
  });

  describe("drawing", () => {
    test("segments come first, chosen ones leave their fill to the window", () => {
      toggleSegment(app.state, 1);
      const overlays = buildOverlays(app.state);
      assert.deepEqual(
        overlays.map((s) => [s.kind, s.chosen, s.strong]),
        [
          ["ramp", false, undefined],
          ["plateau", true, undefined],
          ["ramp", false, undefined],
          ["sinusoid", false, undefined],
          [undefined, undefined, true],
        ],
      );
      const canvas = fakeCanvas({ width: 1000, height: 50 });
      drawOverlayRects(canvas.ctx, overlays, 1000, 1000, 50);
      const fills = ops(canvas.ctx, "fillRect").map((c) => c.fillStyle);
      assert.deepEqual(fills, [
        COLORS.rampFill,
        COLORS.rampFill,
        COLORS.sinusoidFill,
        COLORS.roiStrongFill,
      ]);
      assert.deepEqual(texts(canvas.ctx), ["↗", "—", "↘", "∿"]);
    });

    test("the whole-recording placeholder stays faint", () => {
      const placeholder = buildOverlays(app.state).find(
        (s) => s.kind === undefined,
      );
      assert.equal(placeholder.strong, undefined);
    });
  });

  describe("clicks on the canvas", () => {
    beforeEach(() => enableRoiSelection(app, "emgCanvas"));

    const click = (x) => {
      els.emgCanvas.dispatch("pointerdown", { clientX: x });
      els.emgCanvas.dispatch("pointerup");
    };

    test("a click toggles the segment under it and syncs the window count", () => {
      click(150);
      click(300);
      assert.deepEqual(app.state.rois, [{ start: 100, end: 400 }]);
      assert.equal(els.nwindows.value, "1");
      assert.match(
        app.setStatus.calls.at(-1)[0],
        /^Window 1: ↗ up ramp \+ — plateau \(30\.0 s\)/,
      );
      click(700);
      assert.equal(els.nwindows.value, "2");
      click(300);
      assert.equal(els.nwindows.value, "2");
      assert.match(
        app.setStatus.calls.at(-1)[0],
        /^Segment removed — 2 windows/,
      );
    });

    test("a click over rest changes nothing", () => {
      click(550);
      assert.deepEqual(app.state.rois, [{ start: 0, end: 1000 }]);
    });

    test("an armed artifact click does not toggle a segment", () => {
      app.state.artifactMode = true;
      click(150);
      assert.deepEqual(app.state.rois, [{ start: 0, end: 1000 }]);
    });
  });
});
