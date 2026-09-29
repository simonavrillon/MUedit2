/**
 * Low-level 2D-canvas drawing primitives (series traces, grid overlays, axes)
 * shared across the QC, run, and edit stages. `getCanvasPlotMetrics` resolves
 * the padding/scale box; the draw helpers map data coordinates into that box.
 * Pure rendering — no application state is read or mutated here.
 */
import { COLORS } from "../config.js";

/** @typedef {import("../app/context.js").Span} Span */
/** @typedef {import("../app/state.js").ChannelTrace} ChannelTrace */
/** @typedef {{ left: number, right: number, top: number, bottom: number }} Padding */
/** @typedef {Span & { yMin?: number, yMax?: number, kind?: string }} Overlay */
/** @typedef {HTMLCanvasElement | string | null | undefined} CanvasRef */

/**
 * @typedef {object} SeriesOptions
 * @property {string} [noDataText]
 * @property {boolean} [showAxes]
 * @property {boolean} [hideYAxis]
 * @property {number | null} [fsamp]
 * @property {string} [markerColor]
 * @property {{ positions: number[], values?: number[] | null, color?: string }[]} [extraMarkers]
 */

/**
 * @param {unknown} x
 * @returns {x is ArrayLike<number>}
 */
function isValues(x) {
  return (
    Array.isArray(x) || (ArrayBuffer.isView(x) && !(x instanceof DataView))
  );
}

/**
 * @param {unknown} row
 * @returns {row is import("../api/binary-payloads.js").Envelope}
 */
function isEnvelope(row) {
  if (!row || typeof row !== "object" || isValues(row)) return false;
  const env = /** @type {{ min?: unknown, max?: unknown }} */ (row);
  return isValues(env.min) && isValues(env.max);
}

/**
 * Points in a viewport row: its bins, or its samples (0 when it is neither).
 *
 * @param {ChannelTrace | null | undefined} row
 */
export function seriesPoints(row) {
  if (isEnvelope(row)) return row.min.length;
  return isValues(row) ? row.length : 0;
}

/**
 * Smallest and largest finite value over viewport rows.
 *
 * @param {(ChannelTrace | null | undefined)[]} rows
 */
export function seriesRange(rows) {
  let min = Infinity;
  let max = -Infinity;
  for (const row of rows) {
    /** @type {ArrayLike<number>} */
    let lows;
    /** @type {ArrayLike<number>} */
    let highs;
    if (isEnvelope(row)) {
      lows = row.min;
      highs = row.max;
    } else if (isValues(row)) {
      lows = highs = row;
    } else {
      continue;
    }
    for (let i = 0; i < lows.length; i++) {
      if (lows[i] < min) min = lows[i];
    }
    for (let i = 0; i < highs.length; i++) {
      if (highs[i] > max) max = highs[i];
    }
  }
  return { min, max };
}

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
 * @param {CanvasRef} canvas
 * @returns {HTMLCanvasElement | null}
 */
function resolveCanvas(canvas) {
  if (typeof canvas !== "string") return canvas ?? null;
  return /** @type {HTMLCanvasElement | null} */ (
    document.getElementById(canvas)
  );
}

/**
 * Size a canvas's backing store to its layout box and clear it.
 * @param {CanvasRef} canvas
 */
function prepareCanvas(canvas) {
  const canvasEl = resolveCanvas(canvas);
  const ctx = canvasEl?.getContext("2d");
  if (!canvasEl || !ctx) return null;
  canvasEl.width = canvasEl.clientWidth || canvasEl.width || 1;
  canvasEl.height = canvasEl.clientHeight || canvasEl.height || 220;
  ctx.clearRect(0, 0, canvasEl.width, canvasEl.height);
  return { canvasEl, ctx };
}

/** Resolve after the browser's next paint, once layout has settled. */
export function nextFrame() {
  return new Promise((/** @type {(value?: void) => void} */ resolve) => {
    window.requestAnimationFrame(() => resolve());
  });
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
 * @param {CanvasRef} canvas
 * @param {number[]} series
 * @param {string} [color]
 * @param {number[]} [markers]
 * @param {Overlay[]} [selections]
 * @param {number | null} [totalSamples]
 * @param {Span | null} [viewRange]
 * @param {number[] | null} [markerValues]
 * @param {boolean} [drawLine]
 * @param {SeriesOptions} [options]
 */
export function drawSeries(
  canvas,
  series,
  color = COLORS.primary,
  markers = [],
  selections = [],
  totalSamples = null,
  viewRange = null,
  markerValues = null,
  drawLine = true,
  options = {},
) {
  const prepared = prepareCanvas(canvas);
  if (!prepared) return;
  const { canvasEl, ctx } = prepared;

  if (!series || !series.length) {
    const noDataText = options.noDataText ?? "No data";
    if (noDataText) {
      ctx.fillStyle = COLORS.muted;
      ctx.font = "12px sans-serif";
      ctx.fillText(noDataText, 12, 24);
    }
    return;
  }

  const showAxes = !!options.showAxes;
  const hideYAxis = !!options.hideYAxis;
  const fsamp = options.fsamp || null;
  const markerColor = options.markerColor || COLORS.secondary;
  const { padding, plotWidth, plotHeight } = getCanvasPlotMetrics(
    canvasEl,
    showAxes,
    { hideYAxis },
  );

  const startIdx = viewRange?.start ?? 0;
  const endIdx = viewRange?.end ?? series.length;
  const clampedStart = Math.max(0, Math.min(series.length - 1, startIdx));
  const clampedEnd = Math.max(
    clampedStart + 1,
    Math.min(series.length, endIdx),
  );
  const sliced = series.slice(clampedStart, clampedEnd);
  const viewSpan = clampedEnd - clampedStart;

  let max = -Infinity;
  let min = Infinity;
  for (let i = 0; i < sliced.length; i++) {
    if (sliced[i] > max) max = sliced[i];
    if (sliced[i] < min) min = sliced[i];
  }
  const span = max - min || 1;
  const stepX = plotWidth / Math.max(1, sliced.length - 1);

  const toCanvasX = (/** @type {number} */ idx) => padding.left + idx * stepX;
  const toCanvasY = (/** @type {number} */ v) =>
    padding.top + plotHeight - ((v - min) / span) * plotHeight;

  if (selections && selections.length && viewSpan > 0) {
    selections.forEach((sel) => {
      const rawStart = sel?.start;
      const rawEnd = sel?.end;
      if (!Number.isFinite(rawStart) || !Number.isFinite(rawEnd)) return;
      const s = Math.max(clampedStart, Math.min(clampedEnd, rawStart));
      const e = Math.max(s + 1, Math.min(clampedEnd, rawEnd));
      const startX = padding.left + ((s - clampedStart) / viewSpan) * plotWidth;
      const endX = padding.left + ((e - clampedStart) / viewSpan) * plotWidth;
      drawSelectionRect(ctx, startX, endX, sel, padding, plotHeight);
    });
  }

  if (showAxes) {
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

    if (!hideYAxis) {
      ctx.fillStyle = COLORS.muted;
      ctx.font = "10px sans-serif";
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
        ctx.fillText(`${value.toFixed(1)}`, padding.left - 8, y + 3);
      }
    }

    if (fsamp) {
      const duration = (clampedEnd - clampedStart) / fsamp;
      const targets = [0.1, 0.2, 0.5, 1, 2, 5, 10, 20];
      const desired = duration / 5;
      let step = targets[targets.length - 1];
      for (const cand of targets) {
        if (cand >= desired) {
          step = cand;
          break;
        }
      }
      const tStart = clampedStart / fsamp;
      const tEnd = clampedEnd / fsamp;
      const first = Math.ceil(tStart / step) * step;
      ctx.fillStyle = COLORS.muted;
      ctx.font = "10px sans-serif";
      for (let t = first; t <= tEnd; t += step) {
        const frac = (t - tStart) / duration;
        const x = padding.left + frac * plotWidth;
        ctx.strokeStyle = COLORS.gridLineDim;
        ctx.beginPath();
        ctx.moveTo(x, padding.top);
        ctx.lineTo(x, padding.top + plotHeight);
        ctx.stroke();
        ctx.fillText(`${t.toFixed(1)}s`, x - 10, padding.top + plotHeight + 12);
      }
    }
  }

  if (drawLine) {
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.4;
    ctx.beginPath();
    sliced.forEach((v, idx) => {
      const x = toCanvasX(idx);
      const y = toCanvasY(v);
      if (idx === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();
  }

  // Canvas point for marker `idx` at sample `m`; null when outside the view.
  // Without explicit values the marker sits on the trace.
  const markerPoint = (
    /** @type {number} */ m,
    /** @type {number} */ idx,
    /** @type {number[] | null | undefined} */ values,
  ) => {
    if (m < clampedStart || m >= clampedEnd) return null;
    const relIdx = m - clampedStart;
    const x = Math.min(padding.left + plotWidth, toCanvasX(relIdx));
    const val = values && values.length ? values[idx] : sliced[relIdx];
    return { x, y: toCanvasY(val) };
  };

  if (markers && markers.length) {
    ctx.fillStyle = markerColor;
    markers.forEach((m, idx) => {
      const pt = markerPoint(m, idx, markerValues);
      if (!pt) return;
      ctx.beginPath();
      ctx.arc(pt.x, pt.y, 3, 0, Math.PI * 2);
      ctx.fill();
    });
  }

  if (options.extraMarkers && options.extraMarkers.length) {
    options.extraMarkers.forEach(({ positions, values, color }) => {
      if (!positions || !positions.length) return;
      ctx.fillStyle = color || COLORS.secondary;
      positions.forEach((m, idx) => {
        const pt = markerPoint(m, idx, values);
        if (!pt) return;
        const { x, y } = pt;
        ctx.beginPath();
        ctx.arc(x, y, 4, 0, Math.PI * 2);
        ctx.fill();
        ctx.strokeStyle = "rgba(0,0,0,0.4)";
        ctx.lineWidth = 1;
        ctx.stroke();
      });
    });
  }

  if (selections && selections.length && totalSamples && !viewRange) {
    selections.forEach((sel) => {
      const startX = padding.left + (sel.start / totalSamples) * plotWidth;
      const endX = padding.left + (sel.end / totalSamples) * plotWidth;
      drawSelectionRect(ctx, startX, endX, sel, padding, plotHeight);
    });
  }
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
  const prepared = prepareCanvas(canvas);
  if (!prepared) return;
  const { canvasEl, ctx } = prepared;

  const validSeries = (seriesList || []).filter((s) => seriesPoints(s) > 0);
  if (!validSeries.length) {
    ctx.fillStyle = COLORS.muted;
    ctx.font = "12px sans-serif";
    ctx.fillText("No data", 12, 24);
    return;
  }

  const { min: globalMin, max: globalMax } = seriesRange(validSeries);
  if (!Number.isFinite(globalMin) || !Number.isFinite(globalMax)) {
    ctx.fillStyle = COLORS.muted;
    ctx.font = "12px sans-serif";
    ctx.fillText("No numeric data", 12, 24);
    return;
  }
  const span = globalMax - globalMin || 1;

  if (selections && selections.length && totalSamples) {
    drawRoiRects(
      ctx,
      selections,
      totalSamples,
      canvasEl.width,
      canvasEl.height,
    );
  }

  const toY = (/** @type {number} */ v) =>
    canvasEl.height - ((v - globalMin) / span) * canvasEl.height;
  validSeries.forEach((row, idx) => {
    ctx.strokeStyle = colors[idx % colors.length] || COLORS.primary;
    ctx.lineWidth = 1.2;
    strokeSeries(ctx, row, 0, canvasEl.width, toY);
  });
}

/**
 * @param {HTMLCanvasElement | null | undefined} canvas
 * @param {ChannelTrace | null | undefined} series
 * @param {boolean} [off]
 */
export function drawMiniSeries(canvas, series, off = false) {
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  canvas.width = canvas.clientWidth || 60;
  canvas.height = canvas.clientHeight || 28;
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  if (!series || !seriesPoints(series)) {
    ctx.fillStyle = COLORS.gridEmpty;
    ctx.fillRect(0, 0, canvas.width, canvas.height);
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
    canvas.width,
    (v) => canvas.height - ((v - min) / span) * canvas.height,
  );
}
