// Canvas drawing primitives: data-to-pixel mapping, markers, selections, axes.
import { test, describe, beforeEach } from "node:test";
import assert from "node:assert/strict";

import {
  fakeCanvas,
  installDom,
  ops,
  pathPoints,
  registerElement,
  resetDom,
  texts,
} from "./fake-dom.js";

installDom();
const { COLORS } = await import("../src/config.js");
const plots = await import("../src/view/plots.js");

const ramp = (n) => Array.from({ length: n }, (_, i) => i);

beforeEach(() => resetDom());

describe("getCanvasPlotMetrics", () => {
  test("axes reserve a left gutter for labels and a bottom one for time", () => {
    const m = plots.getCanvasPlotMetrics(fakeCanvas(), true);
    assert.deepEqual(m.padding, { left: 38, right: 8, top: 8, bottom: 20 });
    assert.equal(m.plotWidth, 254);
    assert.equal(m.plotHeight, 72);
  });

  test("without axes the plot fills the canvas", () => {
    const m = plots.getCanvasPlotMetrics(fakeCanvas(), false);
    assert.equal(m.plotWidth, 300);
    assert.equal(m.plotHeight, 100);
  });

  test("hiding the y axis gives its gutter back", () => {
    const m = plots.getCanvasPlotMetrics(fakeCanvas(), true, {
      hideYAxis: true,
    });
    assert.equal(m.padding.left, 8);
    assert.equal(m.plotWidth, 284);
  });

  test("a canvas with no size still has a 1 px plot", () => {
    const m = plots.getCanvasPlotMetrics(
      fakeCanvas({ width: 0, height: 0 }),
      true,
    );
    assert.equal(m.plotWidth, 1);
    assert.equal(m.plotHeight, 1);
  });
});

describe("drawSeries", () => {
  test("maps the series min to the bottom and max to the top", () => {
    const canvas = fakeCanvas();
    plots.drawSeries(canvas, [0, 10, 5]);
    assert.deepEqual(pathPoints(canvas.ctx), [
      ["moveTo", 0, 100],
      ["lineTo", 150, 0],
      ["lineTo", 300, 50],
    ]);
  });

  test("scales to the visible window only", () => {
    const canvas = fakeCanvas();
    plots.drawSeries(canvas, ramp(10), "#fff", [], [], null, {
      start: 2,
      end: 5,
    });
    assert.deepEqual(pathPoints(canvas.ctx), [
      ["moveTo", 0, 100],
      ["lineTo", 150, 50],
      ["lineTo", 300, 0],
    ]);
  });

  test("an empty series says so, unless asked to stay blank", () => {
    const canvas = fakeCanvas();
    plots.drawSeries(canvas, []);
    assert.deepEqual(ops(canvas.ctx, "fillText")[0].args, ["No data", 12, 24]);

    const blank = fakeCanvas();
    plots.drawSeries(blank, [], "#fff", [], [], null, null, null, true, {
      noDataText: "",
    });
    assert.deepEqual(texts(blank.ctx), []);
  });

  test("the canvas backing store follows its CSS size", () => {
    const canvas = fakeCanvas({ width: 420, height: 180 });
    canvas.width = 1;
    canvas.height = 1;
    plots.drawSeries(canvas, [1, 2]);
    assert.equal(canvas.width, 420);
    assert.equal(canvas.height, 180);
  });

  test("a canvas can be given by id, and a missing one is ignored", () => {
    const canvas = fakeCanvas();
    registerElement("pulse", canvas);
    plots.drawSeries("pulse", [0, 1]);
    assert.ok(pathPoints(canvas.ctx).length);
    assert.doesNotThrow(() => plots.drawSeries("nowhere", [0, 1]));
  });

  test("markers sit on the trace and outside ones are skipped", () => {
    const canvas = fakeCanvas();
    plots.drawSeries(canvas, ramp(10), "#fff", [3, 8], [], null, {
      start: 2,
      end: 5,
    });
    const arcs = ops(canvas.ctx, "arc");
    assert.equal(arcs.length, 1);
    assert.deepEqual(arcs[0].args.slice(0, 3), [150, 50, 3]);
    assert.equal(arcs[0].fillStyle, COLORS.secondary);
  });

  test("explicit marker values override the trace height", () => {
    const canvas = fakeCanvas();
    plots.drawSeries(
      canvas,
      ramp(10),
      "#fff",
      [3],
      [],
      null,
      {
        start: 2,
        end: 5,
      },
      [4],
    );
    assert.deepEqual(ops(canvas.ctx, "arc")[0].args.slice(0, 3), [150, 0, 3]);
  });

  test("extra markers draw larger, in their own colour", () => {
    const canvas = fakeCanvas();
    plots.drawSeries(
      canvas,
      ramp(10),
      "#fff",
      [],
      [],
      null,
      {
        start: 2,
        end: 5,
      },
      null,
      true,
      {
        extraMarkers: [{ positions: [4, 9], color: "#f86" }],
      },
    );
    const arcs = ops(canvas.ctx, "arc");
    assert.equal(arcs.length, 1);
    assert.deepEqual(arcs[0].args.slice(0, 3), [300, 0, 4]);
    assert.equal(arcs[0].fillStyle, "#f86");
  });

  test("without the line only the markers are drawn", () => {
    const canvas = fakeCanvas();
    plots.drawSeries(
      canvas,
      ramp(10),
      "#fff",
      [3],
      [],
      null,
      null,
      null,
      false,
    );
    assert.deepEqual(pathPoints(canvas.ctx), []);
    assert.equal(ops(canvas.ctx, "arc").length, 1);
  });

  describe("selections", () => {
    const view = { start: 0, end: 4 };

    test("span the full height when they carry no y range", () => {
      const canvas = fakeCanvas();
      plots.drawSeries(
        canvas,
        ramp(10),
        "#fff",
        [],
        [{ start: 1, end: 3 }],
        10,
        view,
      );
      const [rect] = ops(canvas.ctx, "fillRect");
      assert.deepEqual(rect.args, [75, 0, 150, 100]);
      assert.equal(rect.fillStyle, COLORS.selectionFill);
      assert.deepEqual(
        ops(canvas.ctx, "strokeRect")[0].args,
        [75, 0, 150, 100],
      );
    });

    test("keep the dragged y range, clamped to the plot", () => {
      const canvas = fakeCanvas();
      plots.drawSeries(
        canvas,
        ramp(10),
        "#fff",
        [],
        [
          { start: 1, end: 3, yMin: 20, yMax: 60 },
          { start: 1, end: 3, yMin: -30, yMax: 500 },
        ],
        10,
        view,
      );
      const rects = ops(canvas.ctx, "fillRect").map((r) => r.args);
      assert.deepEqual(rects, [
        [75, 20, 150, 40],
        [75, 0, 150, 100],
      ]);
    });

    test("are clipped to the visible window", () => {
      const canvas = fakeCanvas();
      plots.drawSeries(
        canvas,
        ramp(10),
        "#fff",
        [],
        [{ start: -5, end: 2 }],
        10,
        view,
      );
      assert.deepEqual(ops(canvas.ctx, "fillRect")[0].args, [0, 0, 150, 100]);
    });

    test("with non-finite bounds are skipped", () => {
      const canvas = fakeCanvas();
      plots.drawSeries(
        canvas,
        ramp(10),
        "#fff",
        [],
        [{ start: NaN, end: 3 }],
        10,
        view,
      );
      assert.equal(ops(canvas.ctx, "fillRect").length, 0);
    });
  });

  describe("axes", () => {
    test("label four y ticks from min to max", () => {
      const canvas = fakeCanvas();
      plots.drawSeries(
        canvas,
        [0, 30],
        "#fff",
        [],
        [],
        null,
        null,
        null,
        true,
        {
          showAxes: true,
        },
      );
      assert.deepEqual(texts(canvas.ctx), ["0.0", "10.0", "20.0", "30.0"]);
    });

    test("label time in seconds at a round step", () => {
      const canvas = fakeCanvas();
      plots.drawSeries(
        canvas,
        new Array(2000).fill(1),
        "#fff",
        [],
        [],
        null,
        null,
        null,
        true,
        {
          showAxes: true,
          hideYAxis: true,
          fsamp: 1000,
        },
      );
      assert.deepEqual(texts(canvas.ctx), [
        "0.0s",
        "0.5s",
        "1.0s",
        "1.5s",
        "2.0s",
      ]);
    });

    test("time labels follow the visible window", () => {
      const canvas = fakeCanvas();
      plots.drawSeries(
        canvas,
        new Array(20000).fill(1),
        "#fff",
        [],
        [],
        null,
        {
          start: 5000,
          end: 7000,
        },
        null,
        true,
        { showAxes: true, hideYAxis: true, fsamp: 1000 },
      );
      assert.deepEqual(texts(canvas.ctx), [
        "5.0s",
        "5.5s",
        "6.0s",
        "6.5s",
        "7.0s",
      ]);
    });
  });
});

describe("drawRoiRects", () => {
  test("scales windows to the canvas and colours artifacts apart", () => {
    const canvas = fakeCanvas({ width: 200, height: 50 });
    plots.drawRoiRects(
      canvas.ctx,
      [
        { start: 10, end: 30 },
        { start: 60, end: 50, kind: "artifact" },
      ],
      100,
      200,
      50,
    );
    const rects = ops(canvas.ctx, "fillRect");
    assert.deepEqual(rects[0].args, [20, 0, 40, 50]);
    assert.equal(rects[0].fillStyle, COLORS.roiFill);
    assert.deepEqual(rects[1].args, [100, 0, 20, 50]);
    assert.equal(rects[1].fillStyle, COLORS.artifactFill);
  });

  test("draws nothing without a signal length", () => {
    const canvas = fakeCanvas();
    plots.drawRoiRects(canvas.ctx, [{ start: 0, end: 1 }], 0, 300, 100);
    assert.equal(canvas.ctx.calls.length, 0);
  });
});

describe("drawGridOverlay", () => {
  test("shares one scale across grids, one colour each", () => {
    const canvas = fakeCanvas({ width: 100, height: 50 });
    plots.drawGridOverlay(
      canvas,
      [
        [0, 10],
        [5, 20],
      ],
      ["#a", "#b"],
    );
    assert.deepEqual(pathPoints(canvas.ctx), [
      ["moveTo", 0, 50],
      ["lineTo", 100, 25],
      ["moveTo", 0, 37.5],
      ["lineTo", 100, 0],
    ]);
    const colours = ops(canvas.ctx, "lineTo").map((c) => c.strokeStyle);
    assert.deepEqual(colours, ["#a", "#b"]);
  });

  test("draws the ROI windows under the traces", () => {
    const canvas = fakeCanvas({ width: 100, height: 50 });
    plots.drawGridOverlay(canvas, [[0, 1]], ["#a"], [{ start: 0, end: 1 }], 2);
    assert.deepEqual(ops(canvas.ctx, "fillRect")[0].args, [0, 0, 50, 50]);
  });

  test("reports missing or non-numeric data", () => {
    const empty = fakeCanvas();
    plots.drawGridOverlay(empty, [[], null, "x"]);
    assert.deepEqual(texts(empty.ctx), ["No data"]);

    const nan = fakeCanvas();
    plots.drawGridOverlay(nan, [[NaN, NaN]]);
    assert.deepEqual(texts(nan.ctx), ["No numeric data"]);
  });
});

describe("drawMiniSeries", () => {
  test("a missing trace fills the cell as empty", () => {
    const canvas = fakeCanvas({ width: 60, height: 20 });
    plots.drawMiniSeries(canvas, null);
    const [rect] = ops(canvas.ctx, "fillRect");
    assert.deepEqual(rect.args, [0, 0, 60, 20]);
    assert.equal(rect.fillStyle, COLORS.gridEmpty);
  });

  test("a malformed envelope is treated as missing", () => {
    const canvas = fakeCanvas({ width: 60, height: 20 });
    plots.drawMiniSeries(canvas, /** @type {any} */ ({ min: 1, max: 2 }));
    assert.equal(ops(canvas.ctx, "fillRect").length, 1);
    assert.deepEqual(pathPoints(canvas.ctx), []);
  });

  test("a raw trace is one line, amber when the channel is discarded", () => {
    const on = fakeCanvas({ width: 60, height: 20 });
    plots.drawMiniSeries(on, [0, 2]);
    assert.deepEqual(pathPoints(on.ctx), [
      ["moveTo", 0, 20],
      ["lineTo", 60, 0],
    ]);
    assert.equal(ops(on.ctx, "stroke")[0].strokeStyle, COLORS.primary);

    const off = fakeCanvas({ width: 60, height: 20 });
    plots.drawMiniSeries(off, [0, 2], true);
    assert.equal(ops(off.ctx, "stroke")[0].strokeStyle, COLORS.warning);
  });

  test("an envelope draws one min-to-max bar per sample", () => {
    const canvas = fakeCanvas({ width: 60, height: 30 });
    plots.drawMiniSeries(canvas, { min: [0, 1], max: [2, 3] });
    assert.deepEqual(pathPoints(canvas.ctx), [
      ["moveTo", 0, 30],
      ["lineTo", 0, 10],
      ["moveTo", 60, 20],
      ["lineTo", 60, 0],
    ]);
  });

  test("an unsized cell gets a default size", () => {
    const canvas = fakeCanvas({ width: 0, height: 0 });
    plots.drawMiniSeries(canvas, [1, 2]);
    assert.equal(canvas.width, 60);
    assert.equal(canvas.height, 28);
  });
});

test("nextFrame resolves after the next animation frame", async () => {
  let ran = false;
  const original = globalThis.window.requestAnimationFrame;
  globalThis.window.requestAnimationFrame = (fn) => {
    ran = true;
    return original(fn);
  };
  await plots.nextFrame();
  globalThis.window.requestAnimationFrame = original;
  assert.ok(ran);
});
