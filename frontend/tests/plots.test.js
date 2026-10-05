// Canvas drawing primitives: data-to-pixel mapping, markers, selections, axes.
import { test, describe, beforeEach } from "node:test";
import assert from "node:assert/strict";

import {
  fakeCanvas,
  installDom,
  ops,
  pathPoints,
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

describe("drawTrace", () => {
  const samples = (values, start = 0) => ({
    row: Float32Array.from(values),
    start,
    end: start + values.length,
  });

  test("maps the window min to the bottom and max to the top", () => {
    const canvas = fakeCanvas();
    plots.drawTrace(canvas, samples([0, 10, 5]), { start: 0, end: 3 });
    assert.deepEqual(pathPoints(canvas.ctx), [
      ["moveTo", 0, 100],
      ["lineTo", 150, 0],
      ["lineTo", 300, 50],
    ]);
  });

  test("an envelope is one min-max stroke per bin", () => {
    const canvas = fakeCanvas();
    const trace = {
      row: { min: Float32Array.from([0, 2]), max: Float32Array.from([4, 8]) },
      start: 0,
      end: 6,
    };
    plots.drawTrace(canvas, trace, { start: 0, end: 6 });
    // Bin 0 covers samples 0-2 (drawn at 1), bin 1 samples 3-5 (at 4).
    assert.deepEqual(pathPoints(canvas.ctx), [
      ["moveTo", 60, 50],
      ["lineTo", 60, 100],
      ["lineTo", 240, 0],
      ["lineTo", 240, 75],
    ]);
  });

  test("a window still loading is drawn where it falls in the view", () => {
    const canvas = fakeCanvas();
    plots.drawTrace(canvas, samples(ramp(10), 2), { start: 5, end: 8 });
    assert.deepEqual(
      pathPoints(canvas.ctx).map(([, x]) => x),
      [0, 150, 300],
    );
  });

  test("no window says so, unless asked to stay blank", () => {
    const canvas = fakeCanvas();
    plots.drawTrace(canvas, null, { start: 0, end: 10 });
    assert.deepEqual(ops(canvas.ctx, "fillText")[0].args, ["No data", 12, 24]);

    const blank = fakeCanvas();
    plots.drawTrace(blank, null, { start: 0, end: 10 }, { noDataText: "" });
    assert.deepEqual(texts(blank.ctx), []);
  });

  test("the canvas backing store follows its CSS size", () => {
    const canvas = fakeCanvas({ width: 420, height: 180 });
    canvas.width = 1;
    canvas.height = 1;
    plots.drawTrace(canvas, samples([1, 2]), { start: 0, end: 2 });
    assert.equal(canvas.width, 420);
    assert.equal(canvas.height, 180);
  });

  test("on a dense screen the store is scaled and drawing stays in CSS px", () => {
    globalThis.window.devicePixelRatio = 2;
    try {
      const canvas = fakeCanvas({ width: 420, height: 180 });
      plots.drawTrace(canvas, samples([1, 2]), { start: 0, end: 2 });
      assert.deepEqual([canvas.width, canvas.height], [840, 360]);
      assert.deepEqual(canvas.ctx.transform, [2, 0, 0, 2, 0, 0]);
      assert.deepEqual(ops(canvas.ctx, "clearRect")[0].args, [0, 0, 420, 180]);
    } finally {
      delete globalThis.window.devicePixelRatio;
    }
  });

  test("a store already the right size is not reassigned", () => {
    const canvas = fakeCanvas({ width: 420, height: 180 });
    let assigned = 0;
    let width = 420;
    Object.defineProperty(canvas, "width", {
      get: () => width,
      set: (v) => {
        assigned += 1;
        width = v;
      },
    });
    plots.drawTrace(canvas, samples([1, 2]), { start: 0, end: 2 });
    assert.equal(assigned, 0);
  });

  test("a missing canvas is ignored", () => {
    assert.doesNotThrow(() =>
      plots.drawTrace(null, samples([0, 1]), { start: 0, end: 2 }),
    );
  });

  test("markers sit at their values and outside ones are skipped", () => {
    const canvas = fakeCanvas();
    plots.drawTrace(
      canvas,
      samples([2, 3, 4], 2),
      { start: 2, end: 5 },
      {
        markers: [{ positions: [3, 8], values: [3, 8], color: "#abc" }],
      },
    );
    const arcs = ops(canvas.ctx, "arc");
    assert.equal(arcs.length, 1);
    assert.deepEqual(arcs[0].args.slice(0, 3), [150, 50, 3]);
    assert.equal(arcs[0].fillStyle, "#abc");
  });

  test("markers on the same pixel are drawn once", () => {
    const canvas = fakeCanvas();
    const positions = ramp(1000);
    plots.drawTrace(
      canvas,
      samples([0, 1]),
      { start: 0, end: 1000 },
      {
        range: { min: 0, max: 1 },
        markers: [
          { positions, values: new Array(1000).fill(1), color: "#abc" },
        ],
      },
    );
    assert.equal(
      ops(canvas.ctx, "arc").length,
      301,
      "one per pixel column, 0 to 300",
    );
  });

  test("outlined markers draw larger with a dark edge", () => {
    const canvas = fakeCanvas();
    plots.drawTrace(
      canvas,
      samples([2, 3, 4], 2),
      { start: 2, end: 5 },
      {
        markers: [
          {
            positions: [4],
            values: [4],
            color: "#f86",
            radius: 4,
            outlined: true,
          },
        ],
      },
    );
    const arcs = ops(canvas.ctx, "arc");
    assert.deepEqual(arcs[0].args.slice(0, 3), [300, 0, 4]);
    assert.equal(arcs[0].fillStyle, "#f86");
    assert.equal(
      ops(canvas.ctx, "stroke").at(-1).strokeStyle,
      COLORS.markerOutline,
    );
  });

  test("without a row only the markers are drawn, on the given range", () => {
    const canvas = fakeCanvas();
    plots.drawTrace(
      canvas,
      { row: null, start: 0, end: 10 },
      { start: 0, end: 10 },
      {
        range: { min: 0, max: 10 },
        markers: [{ positions: [3], values: [5], color: "#abc" }],
      },
    );
    assert.deepEqual(pathPoints(canvas.ctx), []);
    assert.deepEqual(ops(canvas.ctx, "arc")[0].args.slice(0, 2), [100, 50]);
  });

  describe("selections", () => {
    // Samples 0-3 sit at x = 0, 100, 200, 300, under the trace's points.
    const view = { start: 0, end: 4 };
    const trace = samples(ramp(4));

    test("span the full height when they carry no y range", () => {
      const canvas = fakeCanvas();
      plots.drawTrace(canvas, trace, view, {
        selections: [{ start: 1, end: 3 }],
      });
      const [rect] = ops(canvas.ctx, "fillRect");
      assert.deepEqual(rect.args, [100, 0, 200, 100]);
      assert.equal(rect.fillStyle, COLORS.selectionFill);
      assert.deepEqual(
        ops(canvas.ctx, "strokeRect")[0].args,
        [100, 0, 200, 100],
      );
    });

    test("keep the dragged y range, clamped to the plot", () => {
      const canvas = fakeCanvas();
      plots.drawTrace(canvas, trace, view, {
        selections: [
          { start: 1, end: 3, yMin: 20, yMax: 60 },
          { start: 1, end: 3, yMin: -30, yMax: 500 },
        ],
      });
      const rects = ops(canvas.ctx, "fillRect").map((r) => r.args);
      assert.deepEqual(rects, [
        [100, 20, 200, 40],
        [100, 0, 200, 100],
      ]);
    });

    test("are clipped to the visible window", () => {
      const canvas = fakeCanvas();
      plots.drawTrace(canvas, trace, view, {
        selections: [{ start: -5, end: 2 }],
      });
      assert.deepEqual(ops(canvas.ctx, "fillRect")[0].args, [0, 0, 200, 100]);
    });

    test("with non-finite bounds are skipped", () => {
      const canvas = fakeCanvas();
      plots.drawTrace(canvas, trace, view, {
        selections: [{ start: NaN, end: 3 }],
      });
      assert.equal(ops(canvas.ctx, "fillRect").length, 0);
    });
  });

  describe("axes", () => {
    test("label four y ticks from min to max", () => {
      const canvas = fakeCanvas();
      plots.drawTrace(
        canvas,
        samples([0, 30]),
        { start: 0, end: 2 },
        {
          showAxes: true,
        },
      );
      assert.deepEqual(texts(canvas.ctx), ["0.0", "10.0", "20.0", "30.0"]);
    });

    test("a step under a second is labelled to a tenth", () => {
      const canvas = fakeCanvas();
      plots.drawTrace(
        canvas,
        samples([1, 1]),
        { start: 0, end: 2000 },
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

    test("a long recording shown whole keeps a few labels", () => {
      const canvas = fakeCanvas();
      plots.drawTrace(
        canvas,
        samples([1, 1]),
        { start: 0, end: 600 * 1000 },
        {
          showAxes: true,
          hideYAxis: true,
          fsamp: 1000,
        },
      );
      assert.deepEqual(texts(canvas.ctx), [
        "0:00",
        "2:00",
        "4:00",
        "6:00",
        "8:00",
        "10:00",
      ]);
    });

    test("whole seconds are labelled without decimals", () => {
      const canvas = fakeCanvas();
      plots.drawTrace(
        canvas,
        samples([1, 1]),
        { start: 0, end: 20 * 1000 },
        {
          showAxes: true,
          hideYAxis: true,
          fsamp: 1000,
        },
      );
      assert.deepEqual(texts(canvas.ctx), ["0s", "5s", "10s", "15s", "20s"]);
    });

    test("time labels sit on their ticks, inside the plot at its edges", () => {
      const canvas = fakeCanvas();
      plots.drawTrace(
        canvas,
        samples([1, 1]),
        { start: 0, end: 20 * 1000 + 1 },
        { showAxes: true, hideYAxis: true, fsamp: 1000 },
      );
      // Each time tick is a line down from the plot's top, 8 px.
      const ticks = ops(canvas.ctx, "moveTo")
        .filter((c) => c.args[1] === 8)
        .map((c) => c.args[0]);
      const labels = ops(canvas.ctx, "fillText");
      assert.deepEqual(
        labels.map((c) => c.textAlign),
        ["left", "center", "center", "center", "right"],
      );
      assert.deepEqual(
        labels.map((c) => c.args[1]),
        ticks,
      );
      assert.equal(canvas.ctx.textAlign, "start");
    });

    test("the lowest value label sits above the x axis", () => {
      const canvas = fakeCanvas({ width: 200, height: 100 });
      plots.drawTrace(
        canvas,
        samples([0, 30]),
        { start: 0, end: 2 },
        {
          showAxes: true,
        },
      );
      const [lowest] = ops(canvas.ctx, "fillText");
      const axisY = 100 - 20;
      assert.equal(lowest.args[0], "0.0");
      assert.ok(lowest.args[2] < axisY, `${lowest.args[2]} above ${axisY}`);
      assert.equal(canvas.ctx.textAlign, "start");
    });

    test("time labels follow the visible window", () => {
      const canvas = fakeCanvas();
      plots.drawTrace(
        canvas,
        samples([1, 1]),
        { start: 5000, end: 7000 },
        {
          showAxes: true,
          hideYAxis: true,
          fsamp: 1000,
        },
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

  test("a grid with no data leaves the others their colours", () => {
    const canvas = fakeCanvas({ width: 100, height: 50 });
    plots.drawGridOverlay(canvas, [[], [0, 10]], ["#a", "#b"]);
    const colours = ops(canvas.ctx, "lineTo").map((c) => c.strokeStyle);
    assert.deepEqual(colours, ["#b"]);
  });

  test("draws the ROI windows under the traces", () => {
    const canvas = fakeCanvas({ width: 100, height: 50 });
    plots.drawGridOverlay(canvas, [[0, 1]], ["#a"], [{ start: 0, end: 1 }], 2);
    assert.deepEqual(ops(canvas.ctx, "fillRect")[0].args, [0, 0, 50, 50]);
  });

  test("draws envelopes as the band between each bin's min and max", () => {
    const canvas = fakeCanvas({ width: 100, height: 50 });
    plots.drawGridOverlay(
      canvas,
      [{ min: new Float32Array([0, 5]), max: new Float32Array([10, 10]) }],
      ["#a"],
    );
    assert.deepEqual(pathPoints(canvas.ctx), [
      ["moveTo", 0, 0],
      ["lineTo", 0, 50],
      ["lineTo", 100, 0],
      ["lineTo", 100, 25],
    ]);
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

  test("an envelope zig-zags through each bin's max and min", () => {
    const canvas = fakeCanvas({ width: 60, height: 30 });
    plots.drawMiniSeries(canvas, {
      min: new Float32Array([0, 1]),
      max: new Float32Array([2, 3]),
    });
    assert.deepEqual(pathPoints(canvas.ctx), [
      ["moveTo", 0, 10],
      ["lineTo", 0, 30],
      ["lineTo", 60, 0],
      ["lineTo", 60, 20],
    ]);
  });

  test("the samples of a zoomed-in window are one line", () => {
    const canvas = fakeCanvas({ width: 60, height: 20 });
    plots.drawMiniSeries(canvas, new Float32Array([0, 2]));
    assert.deepEqual(pathPoints(canvas.ctx), [
      ["moveTo", 0, 20],
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

/** Resolve after the next animation frame. */
const nextFrame = () =>
  new Promise((resolve) => globalThis.window.requestAnimationFrame(resolve));

test("oncePerFrame draws once at the next frame however often asked", async () => {
  let draws = 0;
  const schedule = plots.oncePerFrame(() => {
    draws += 1;
  });
  schedule();
  schedule();
  schedule();
  assert.equal(draws, 0);
  await nextFrame();
  assert.equal(draws, 1);
  schedule();
  await nextFrame();
  assert.equal(draws, 2);
});

test("a box dragged over drawn samples is drawn over them", () => {
  const scale = plots.viewScale({ start: 100, end: 110 }, 38, 254);
  // The plot's edges are the view's first and last samples.
  assert.equal(scale.toX(100), 38);
  assert.equal(scale.toX(109), 292);
  // A pointer at a drawn sample picks that sample, at any zoom.
  for (let s = 100; s < 110; s++) {
    assert.equal(Math.round(scale.toSample(scale.toX(s))), s);
  }
});
