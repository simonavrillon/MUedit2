/**
 * Low-level 2D-canvas drawing primitives (series traces, grid overlays, axes)
 * shared across the QC, run, and edit stages. `getCanvasPlotMetrics` resolves
 * the padding/scale box; the draw helpers map data coordinates into that box.
 * Pure rendering — no application state is read or mutated here.
 */
import { COLORS, canvasFont } from "../config.js";
import {
  isEnvelope,
  isValues,
  seriesPoints,
  seriesRange,
  traceRange,
} from "../signal/series.js";

/** @typedef {import("../app/context.js").Span} Span */
/** @typedef {import("../state/state.js").ChannelTrace} ChannelTrace */
/** @typedef {import("../signal/series.js").TraceWindow} TraceWindow */
/** @typedef {{ left: number, right: number, top: number, bottom: number }} Padding */
/** @typedef {Span & { yMin?: number, yMax?: number, kind?: string }} Overlay */
/** @typedef {HTMLCanvasElement | null | undefined} CanvasRef */

/**
 * Points drawn at sample `positions`, `values` high.
 *
 * @typedef {object} MarkerSet
 * @property {ArrayLike<number>} positions
 * @property {ArrayLike<number>} values
 * @property {string} color
 * @property {number} [radius]
 * @property {boolean} [outlined]
 */

/**
 * @typedef {object} TraceOptions
 * @property {string} [color]
 * @property {MarkerSet[]} [markers]
 * @property {Overlay[]} [selections]
 * @property {{ min: number, max: number }} [range] The y range; else the row's.
 * @property {boolean} [showAxes]
 * @property {boolean} [hideYAxis]
 * @property {number | null} [fsamp]
 * @property {string} [noDataText]
 */

/**
 * Stroke one viewport row across `width` pixels from `x0`: a line through the
 * samples, or a zig-zag through each bin's max and min, which fills the band
 * the samples cover.
 *
 * @param {CanvasRenderingContext2D} ctx
 * @param {ChannelTrace} row
 * @param {number} x0
 * @param {number} width
 * @param {(v: number) => number} toY
 */
export function strokeSeries(ctx, row, x0, width, toY) {
  const n = seriesPoints(row);
  if (!n) return;
  const stepX = width / Math.max(1, n - 1);
  ctx.beginPath();
  if (isEnvelope(row)) {
    for (let i = 0; i < n; i++) {
      const x = x0 + i * stepX;
      if (i === 0) ctx.moveTo(x, toY(row.max[i]));
      else ctx.lineTo(x, toY(row.max[i]));
      ctx.lineTo(x, toY(row.min[i]));
    }
  } else {
    for (let i = 0; i < n; i++) {
      const x = x0 + i * stepX;
      if (i === 0) ctx.moveTo(x, toY(row[i]));
      else ctx.lineTo(x, toY(row[i]));
    }
  }
  ctx.stroke();
}

/**
 * Size a canvas's backing store to its layout box at the screen's pixel
 * density, and clear it. Drawing is then in CSS pixels, `width` x `height`.
 *
 * @param {CanvasRef} canvasEl
 * @param {{ width?: number, height?: number }} [fallback] The size while the canvas has no layout box.
 */
export function prepareCanvas(canvasEl, fallback = {}) {
  const ctx = canvasEl?.getContext("2d");
  if (!canvasEl || !ctx) return null;
  const width = canvasEl.clientWidth || fallback.width || 1;
  const height = canvasEl.clientHeight || fallback.height || 1;
  const dpr = window.devicePixelRatio || 1;
  const backingWidth = Math.round(width * dpr);
  const backingHeight = Math.round(height * dpr);
  // Assigning a size reallocates and clears the store even when it is unchanged.
  if (canvasEl.width !== backingWidth) canvasEl.width = backingWidth;
  if (canvasEl.height !== backingHeight) canvasEl.height = backingHeight;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);
  return { canvasEl, ctx, width, height };
}

/**
 * Wrap `draw` so that however often it is asked for, it runs once, at the
 * next animation frame: a drag's pointer events can outpace the screen.
 *
 * @param {() => void} draw
 */
export function oncePerFrame(draw) {
  let pending = false;
  return () => {
    if (pending) return;
    pending = true;
    window.requestAnimationFrame(() => {
      pending = false;
      draw();
    });
  };
}

/**
 * The x scale of a plot over `view`: the samples `[view.start, view.end)`
 * span `width` pixels from `left`, the first at the left edge and the last at
 * the right one. The traces, selections, time axis, bookmark and pointer all
 * use it, so what is drawn and what a pointer picks agree.
 *
 * @param {Span} view
 * @param {number} left
 * @param {number} width
 */
export function viewScale(view, left, width) {
  const span = Math.max(1, view.end - view.start - 1);
  return {
    /** @param {number} sample */
    toX: (sample) => left + ((sample - view.start) / span) * width,
    /** @param {number} x */
    toSample: (x) => view.start + ((x - left) / width) * span,
  };
}

/**
 * @param {boolean} showAxes
 * @returns {Padding}
 */
function getAxisPadding(showAxes) {
  return showAxes
    ? { left: 38, right: 8, top: 8, bottom: 20 }
    : { left: 0, right: 0, top: 0, bottom: 0 };
}

/**
 * @param {HTMLCanvasElement} canvas
 * @param {boolean} showAxes
 * @param {{ hideYAxis?: boolean }} [options]
 */
export function getCanvasPlotMetrics(
  canvas,
  showAxes,
  { hideYAxis = false } = {},
) {
  const padding = showAxes
    ? hideYAxis
      ? { left: 8, right: 8, top: 8, bottom: 20 }
      : getAxisPadding(true)
    : getAxisPadding(false);
  const width = canvas.clientWidth || canvas.width || 1;
  const height = canvas.clientHeight || canvas.height || 1;
  return {
    padding,
    width,
    height,
    plotWidth: Math.max(1, width - padding.left - padding.right),
    plotHeight: Math.max(1, height - padding.top - padding.bottom),
  };
}

/**
 * @param {CanvasRenderingContext2D} ctx
 * @param {number} startX
 * @param {number} endX
 * @param {Overlay} sel
 * @param {Padding} padding
 * @param {number} plotHeight
 */
function drawSelectionRect(ctx, startX, endX, sel, padding, plotHeight) {
  const top = sel?.yMin;
  const bottom = sel?.yMax;
  const hasY =
    typeof top === "number" &&
    typeof bottom === "number" &&
    Number.isFinite(top) &&
    Number.isFinite(bottom);
  const yMin = hasY ? Math.max(0, Math.min(plotHeight, top)) : 0;
  const yMax = hasY ? Math.max(0, Math.min(plotHeight, bottom)) : plotHeight;
  const rectTop = padding.top + Math.min(yMin, yMax);
  const rectHeight = Math.max(1, Math.abs(yMax - yMin));
  const x = Math.min(startX, endX);
  const width = Math.max(1, Math.abs(endX - startX));
  ctx.fillStyle = COLORS.selectionFill;
  ctx.fillRect(x, rectTop, width, rectHeight);
  ctx.strokeStyle = COLORS.selectionStroke;
  ctx.lineWidth = 1;
  ctx.strokeRect(x, rectTop, width, rectHeight);
}

/**
 * @param {CanvasRenderingContext2D} ctx
 * @param {Overlay[] | null | undefined} selections
 * @param {number | null | undefined} totalSamples
 * @param {number} width
 * @param {number} height
 */
export function drawRoiRects(ctx, selections, totalSamples, width, height) {
  if (!selections || !selections.length || !totalSamples) return;
  selections.forEach((sel) => {
    const startX = (sel.start / totalSamples) * width;
    const endX = (sel.end / totalSamples) * width;
    const isArtifact = sel?.kind === "artifact";
    ctx.fillStyle = isArtifact ? COLORS.artifactFill : COLORS.roiFill;
    ctx.fillRect(Math.min(startX, endX), 0, Math.abs(endX - startX), height);
    ctx.strokeStyle = isArtifact ? COLORS.artifactStroke : COLORS.roiStroke;
    ctx.lineWidth = 1;
    ctx.strokeRect(Math.min(startX, endX), 0, Math.abs(endX - startX), height);
  });
}

/**
 * Draw a series over `view`, the samples `[view.start, view.end)` of the x
 * axis. The trace may cover another window (a pan still being fetched): it is
 * drawn where its samples fall, and what falls outside the view is skipped.
 * An envelope is drawn as each bin's min-max stroke, so a zoomed-out view costs
 * two points per pixel column; markers are drawn once per pixel.
 *
 * @param {CanvasRef} canvas
 * @param {TraceWindow | null} trace
 * @param {Span} view
 * @param {TraceOptions} [options]
 */
export function drawTrace(canvas, trace, view, options = {}) {
  const prepared = prepareCanvas(canvas, { height: 220 });
  if (!prepared) return;
  const { canvasEl, ctx } = prepared;

  const markers = options.markers || [];
  const viewSpan = view.end - view.start;
  if (!trace || viewSpan <= 0) {
    const noDataText = options.noDataText ?? "No data";
    if (noDataText) {
      ctx.fillStyle = COLORS.muted;
      ctx.font = canvasFont();
      ctx.fillText(noDataText, 12, 24);
    }
    return;
  }

  const showAxes = !!options.showAxes;
  const hideYAxis = !!options.hideYAxis;
  const fsamp = options.fsamp || null;
  const { padding, plotWidth, plotHeight } = getCanvasPlotMetrics(
    canvasEl,
    showAxes,
    { hideYAxis },
  );
  const { min, max } = options.range || traceRange(trace);
  const span = max - min || 1;
  const { toX } = viewScale(view, padding.left, plotWidth);
  const toY = (/** @type {number} */ v) =>
    padding.top + plotHeight - ((v - min) / span) * plotHeight;
  const inView = (/** @type {number} */ sample) =>
    sample >= view.start && sample < view.end;

  for (const sel of options.selections || []) {
    if (!Number.isFinite(sel?.start) || !Number.isFinite(sel?.end)) continue;
    const last = view.end - 1;
    const s = Math.max(view.start, Math.min(last, sel.start));
    const e = Math.max(s, Math.min(last, sel.end));
    drawSelectionRect(ctx, toX(s), toX(e), sel, padding, plotHeight);
  }

  if (showAxes) {
    drawAxes(ctx, padding, plotWidth, plotHeight, { hideYAxis, min, span });
    if (fsamp) drawTimeAxis(ctx, padding, plotWidth, plotHeight, view, fsamp);
  }

  const row = trace.row;
  if (row && seriesPoints(row)) {
    ctx.strokeStyle = options.color || COLORS.primary;
    ctx.lineWidth = 1.4;
    ctx.beginPath();
    let started = false;
    /** @param {number} x @param {number} y */
    const to = (x, y) => {
      if (started) ctx.lineTo(x, y);
      else ctx.moveTo(x, y);
      started = true;
    };
    const n = seriesPoints(row);
    const traceSpan = trace.end - trace.start;
    if (isEnvelope(row)) {
      for (let i = 0; i < n; i++) {
        const first = trace.start + Math.floor((i * traceSpan) / n);
        const next = trace.start + Math.floor(((i + 1) * traceSpan) / n);
        const at = (first + next - 1) / 2;
        if (!inView(at)) continue;
        to(toX(at), toY(row.max[i]));
        ctx.lineTo(toX(at), toY(row.min[i]));
      }
    } else if (isValues(row)) {
      for (let i = 0; i < n; i++) {
        const at = trace.start + i;
        if (inView(at)) to(toX(at), toY(row[i]));
      }
    }
    ctx.stroke();
  }

  for (const set of markers) {
    ctx.fillStyle = set.color;
    if (set.outlined) {
      ctx.strokeStyle = COLORS.markerOutline;
      ctx.lineWidth = 1;
    }
    // Each marker is its own small path: one path of many overlapping
    // circles fills several times slower in Chromium.
    /** @type {Set<number>} */
    const drawn = new Set();
    for (let i = 0; i < set.positions.length; i++) {
      const m = set.positions[i];
      if (!inView(m)) continue;
      const x = Math.min(padding.left + plotWidth, toX(m));
      const y = toY(set.values[i]);
      const pixel = Math.round(x) * 0x10000 + (Math.round(y) & 0xffff);
      if (drawn.has(pixel)) continue;
      drawn.add(pixel);
      ctx.beginPath();
      ctx.arc(x, y, set.radius ?? 3, 0, Math.PI * 2);
      ctx.fill();
      if (set.outlined) ctx.stroke();
    }
  }
}

/**
 * The plot's axis lines, and four y ticks from `min` to `min + span`.
 *
 * @param {CanvasRenderingContext2D} ctx
 * @param {Padding} padding
 * @param {number} plotWidth
 * @param {number} plotHeight
 * @param {{ hideYAxis: boolean, min: number, span: number }} y
 */
function drawAxes(
  ctx,
  padding,
  plotWidth,
  plotHeight,
  { hideYAxis, min, span },
) {
  ctx.strokeStyle = COLORS.gridAxis;
  ctx.lineWidth = 1;
  ctx.beginPath();
  if (!hideYAxis) {
    ctx.moveTo(padding.left, padding.top);
    ctx.lineTo(padding.left, padding.top + plotHeight);
  } else {
    ctx.moveTo(padding.left, padding.top + plotHeight);
  }
  ctx.lineTo(padding.left + plotWidth, padding.top + plotHeight);
  ctx.stroke();
  if (hideYAxis) return;

  ctx.fillStyle = COLORS.muted;
  ctx.font = canvasFont();
  const yTicks = 3;
  for (let i = 0; i <= yTicks; i++) {
    const t = i / yTicks;
    const y = padding.top + plotHeight - t * plotHeight;
    const value = min + t * span;
    ctx.strokeStyle = COLORS.gridLineDim;
    ctx.beginPath();
    ctx.moveTo(padding.left, y);
    ctx.lineTo(padding.left + plotWidth, y);
    ctx.stroke();
    ctx.fillStyle = COLORS.muted;
    ctx.textAlign = "right";
    // The lowest label sits above the x axis, clear of the first time label.
    ctx.fillText(`${value.toFixed(1)}`, padding.left - 8, i ? y + 3 : y - 2);
  }
  ctx.textAlign = "start";
}

/** How near an edge of the plot a time label is aligned to its tick from inside, not centred. */
const TIME_LABEL_EDGE = 12;

/**
 * A time label as precise as its step: "0.5s", "12s", or "2:00" from a
 * minute up.
 *
 * @param {number} t Seconds.
 * @param {number} step
 */
function timeLabel(t, step) {
  if (step < 1) return `${t.toFixed(1)}s`;
  const s = Math.round(t);
  if (step < 60) return `${s}s`;
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/**
 * Time labels at a round step, over the samples of `view`.
 *
 * @param {CanvasRenderingContext2D} ctx
 * @param {Padding} padding
 * @param {number} plotWidth
 * @param {number} plotHeight
 * @param {Span} view
 * @param {number} fsamp
 */
function drawTimeAxis(ctx, padding, plotWidth, plotHeight, view, fsamp) {
  const { toX } = viewScale(view, padding.left, plotWidth);
  const duration = (view.end - view.start) / fsamp;
  // Up to 10 min a step, so even an hour-long recording shown whole keeps
  // its labels apart.
  const targets = [0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 30, 60, 120, 300, 600];
  const desired = duration / 5;
  let step = targets[targets.length - 1];
  for (const cand of targets) {
    if (cand >= desired) {
      step = cand;
      break;
    }
  }
  const tStart = view.start / fsamp;
  const tEnd = view.end / fsamp;
  const first = Math.ceil(tStart / step) * step;
  ctx.fillStyle = COLORS.muted;
  ctx.font = canvasFont();
  for (let t = first; t <= tEnd; t += step) {
    const x = toX(t * fsamp);
    ctx.strokeStyle = COLORS.gridLineDim;
    ctx.beginPath();
    ctx.moveTo(x, padding.top);
    ctx.lineTo(x, padding.top + plotHeight);
    ctx.stroke();
    // Centred on its tick; one at an edge of the plot stays inside it.
    if (x - padding.left < TIME_LABEL_EDGE) ctx.textAlign = "left";
    else if (padding.left + plotWidth - x < TIME_LABEL_EDGE)
      ctx.textAlign = "right";
    else ctx.textAlign = "center";
    ctx.fillText(timeLabel(t, step), x, padding.top + plotHeight + 12);
  }
  ctx.textAlign = "start";
}

/**
 * The smoothed mean |EMG| of each grid over the whole recording, one colour
 * per grid, behind the ROI and artifact windows.
 *
 * @param {CanvasRef} canvas
 * @param {ChannelTrace[]} [seriesList]
 * @param {string[]} [colors]
 * @param {Overlay[]} [selections]
 * @param {number | null} [totalSamples]
 */
export function drawGridOverlay(
  canvas,
  seriesList = [],
  colors = [],
  selections = [],
  totalSamples = null,
) {
  const prepared = prepareCanvas(canvas, { height: 220 });
  if (!prepared) return;
  const { ctx, width, height } = prepared;

  const validSeries = (seriesList || []).filter((s) => seriesPoints(s) > 0);
  if (!validSeries.length) {
    ctx.fillStyle = COLORS.muted;
    ctx.font = canvasFont();
    ctx.fillText("No data", 12, 24);
    return;
  }

  const { min: globalMin, max: globalMax } = seriesRange(validSeries);
  if (!Number.isFinite(globalMin) || !Number.isFinite(globalMax)) {
    ctx.fillStyle = COLORS.muted;
    ctx.font = canvasFont();
    ctx.fillText("No numeric data", 12, 24);
    return;
  }
  const span = globalMax - globalMin || 1;

  if (selections && selections.length && totalSamples) {
    drawRoiRects(ctx, selections, totalSamples, width, height);
  }

  const toY = (/** @type {number} */ v) =>
    height - ((v - globalMin) / span) * height;
  // Each grid keeps its own colour, whichever others have no data.
  (seriesList || []).forEach((row, idx) => {
    if (!seriesPoints(row)) return;
    ctx.strokeStyle = colors[idx % colors.length] || COLORS.primary;
    ctx.lineWidth = 1.2;
    strokeSeries(ctx, row, 0, width, toY);
  });
}

/**
 * @param {HTMLCanvasElement | null | undefined} canvas
 * @param {ChannelTrace | null | undefined} series
 * @param {boolean} [off]
 */
export function drawMiniSeries(canvas, series, off = false) {
  const prepared = prepareCanvas(canvas, { width: 60, height: 28 });
  if (!prepared) return;
  const { ctx, width, height } = prepared;
  if (!series || !seriesPoints(series)) {
    ctx.fillStyle = COLORS.gridEmpty;
    ctx.fillRect(0, 0, width, height);
    return;
  }
  const { min, max } = seriesRange([series]);
  const span = max - min || 1;
  ctx.strokeStyle = off ? COLORS.warning : COLORS.primary;
  ctx.lineWidth = 1;
  strokeSeries(
    ctx,
    series,
    0,
    width,
    (v) => height - ((v - min) / span) * height,
  );
}
