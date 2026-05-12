import { COLORS } from "./config.js";

export function getAxisPadding(showAxes) {
  return showAxes
    ? { left: 38, right: 8, top: 8, bottom: 20 }
    : { left: 0, right: 0, top: 0, bottom: 0 };
}

export function getCanvasPlotMetrics(canvas, showAxes, { hideYAxis = false } = {}) {
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
  const canvasEl =
    typeof canvas === "string" ? document.getElementById(canvas) : canvas;
  if (!canvasEl) return;
  const ctx = canvasEl.getContext("2d");
  const w = canvasEl.clientWidth || canvasEl.width || 1;
  canvasEl.width = w;
  const h = canvasEl.clientHeight || canvasEl.height || 220;
  canvasEl.height = h;
  ctx.clearRect(0, 0, canvasEl.width, canvasEl.height);

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
  const padding = showAxes
    ? hideYAxis
      ? { left: 8, right: 8, top: 8, bottom: 20 }
      : getAxisPadding(true)
    : getAxisPadding(false);
  const plotWidth = Math.max(1, canvasEl.width - padding.left - padding.right);
  const plotHeight = Math.max(1, canvasEl.height - padding.top - padding.bottom);

  const startIdx = viewRange?.start ?? 0;
  const endIdx = viewRange?.end ?? series.length;
  const clampedStart = Math.max(0, Math.min(series.length - 1, startIdx));
  const clampedEnd = Math.max(
    clampedStart + 1,
    Math.min(series.length, endIdx),
  );
  const sliced = series.slice(clampedStart, clampedEnd);
  const viewSpan = clampedEnd - clampedStart;

  let max = -Infinity, min = Infinity;
  for (let i = 0; i < sliced.length; i++) {
    if (sliced[i] > max) max = sliced[i];
    if (sliced[i] < min) min = sliced[i];
  }
  const span = max - min || 1;
  const stepX = plotWidth / Math.max(1, sliced.length - 1);

  const toCanvasX = (idx) => padding.left + idx * stepX;
  const toCanvasY = (v) =>
    padding.top + plotHeight - ((v - min) / span) * plotHeight;

  if (selections && selections.length && viewSpan > 0) {
    selections.forEach((sel) => {
      const rawStart = sel?.start ?? sel?.[0];
      const rawEnd = sel?.end ?? sel?.[1];
      if (!Number.isFinite(rawStart) || !Number.isFinite(rawEnd)) return;
      const s = Math.max(clampedStart, Math.min(clampedEnd, rawStart));
      const e = Math.max(s + 1, Math.min(clampedEnd, rawEnd));
      const startX = padding.left + ((s - clampedStart) / viewSpan) * plotWidth;
      const endX = padding.left + ((e - clampedStart) / viewSpan) * plotWidth;
      const width = Math.max(1, endX - startX);
      const hasY = Number.isFinite(sel?.yMin) && Number.isFinite(sel?.yMax);
      const yMin = hasY ? Math.max(0, Math.min(plotHeight, sel.yMin)) : 0;
      const yMax = hasY
        ? Math.max(0, Math.min(plotHeight, sel.yMax))
        : plotHeight;
      const rectTop = padding.top + Math.min(yMin, yMax);
      const rectHeight = Math.max(1, Math.abs(yMax - yMin));
      ctx.fillStyle = COLORS.selectionFill;
      ctx.fillRect(startX, rectTop, width, rectHeight);
      ctx.strokeStyle = COLORS.selectionStroke;
      ctx.lineWidth = 1;
      ctx.strokeRect(startX, rectTop, width, rectHeight);
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
        ctx.strokeStyle = COLORS.gridLineDim;
        ctx.beginPath();
        ctx.moveTo(padding.left, y);
        ctx.lineTo(padding.left + plotWidth, y);
        ctx.stroke();
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

  if (markers && markers.length) {
    markers.forEach((m, idx) => {
      if (m < clampedStart || m >= clampedEnd) return;
      const relIdx = m - clampedStart;
      const x = Math.min(
        padding.left + plotWidth,
        padding.left + (relIdx / Math.max(1, sliced.length - 1)) * plotWidth,
      );
      const val =
        markerValues && markerValues.length
          ? markerValues[idx]
          : sliced[relIdx];
      const y = toCanvasY(val);
      ctx.fillStyle = markerColor;
      ctx.beginPath();
      ctx.arc(x, y, 3, 0, Math.PI * 2);
      ctx.fill();
    });
  }

  if (selections && selections.length && totalSamples && !viewRange) {
    selections.forEach((sel) => {
      const startX = padding.left + (sel.start / totalSamples) * plotWidth;
      const endX = padding.left + (sel.end / totalSamples) * plotWidth;
      const hasY = Number.isFinite(sel?.yMin) && Number.isFinite(sel?.yMax);
      const yMin = hasY ? Math.max(0, Math.min(plotHeight, sel.yMin)) : 0;
      const yMax = hasY
        ? Math.max(0, Math.min(plotHeight, sel.yMax))
        : plotHeight;
      const rectTop = padding.top + Math.min(yMin, yMax);
      const rectHeight = Math.max(1, Math.abs(yMax - yMin));
      ctx.fillStyle = COLORS.selectionFill;
      ctx.fillRect(
        Math.min(startX, endX),
        rectTop,
        Math.abs(endX - startX),
        rectHeight,
      );
      ctx.strokeStyle = COLORS.selectionStroke;
      ctx.lineWidth = 1;
      ctx.strokeRect(
        Math.min(startX, endX),
        rectTop,
        Math.abs(endX - startX),
        rectHeight,
      );
    });
  }
}

export function drawGridOverlay(
  canvas,
  seriesList = [],
  colors = [],
  selections = [],
  totalSamples = null,
) {
  const canvasEl =
    typeof canvas === "string" ? document.getElementById(canvas) : canvas;
  if (!canvasEl) return;
  const ctx = canvasEl.getContext("2d");
  const w = canvasEl.clientWidth || canvasEl.width || 1;
  canvasEl.width = w;
  const h = canvasEl.clientHeight || canvasEl.height || 220;
  canvasEl.height = h;
  ctx.clearRect(0, 0, canvasEl.width, canvasEl.height);

  const validSeries = (seriesList || []).filter(
    (s) => Array.isArray(s) && s.length,
  );
  if (!validSeries.length) {
    ctx.fillStyle = COLORS.muted;
    ctx.font = "12px sans-serif";
    ctx.fillText("No data", 12, 24);
    return;
  }

  let globalMin = Infinity;
  let globalMax = -Infinity;
  validSeries.forEach((arr) => {
    arr.forEach((v) => {
      if (Number.isFinite(v)) {
        if (v < globalMin) globalMin = v;
        if (v > globalMax) globalMax = v;
      }
    });
  });
  if (!Number.isFinite(globalMin) || !Number.isFinite(globalMax)) {
    ctx.fillStyle = COLORS.muted;
    ctx.font = "12px sans-serif";
    ctx.fillText("No numeric data", 12, 24);
    return;
  }
  const span = globalMax - globalMin || 1;

  if (selections && selections.length && totalSamples) {
    selections.forEach((sel) => {
      const startX = (sel.start / totalSamples) * canvasEl.width;
      const endX = (sel.end / totalSamples) * canvasEl.width;
      ctx.fillStyle = COLORS.roiFill;
      ctx.fillRect(
        Math.min(startX, endX),
        0,
        Math.abs(endX - startX),
        canvasEl.height,
      );
      ctx.strokeStyle = COLORS.roiStroke;
      ctx.lineWidth = 1;
      ctx.strokeRect(
        Math.min(startX, endX),
        0,
        Math.abs(endX - startX),
        canvasEl.height,
      );
    });
  }

  validSeries.forEach((arr, idx) => {
    if (!arr.length) return;
    const stepX = canvasEl.width / Math.max(1, arr.length - 1);
    const color = colors[idx % colors.length] || COLORS.primary;
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.2;
    ctx.beginPath();
    arr.forEach((v, i) => {
      const x = i * stepX;
      const y = canvasEl.height - ((v - globalMin) / span) * canvasEl.height;
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();
  });
}

export function drawMiniSeries(canvas, series, off = false) {
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  canvas.width = canvas.clientWidth || 60;
  canvas.height = canvas.clientHeight || 28;
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  if (!series || (!series.length && !(series.min && series.max))) {
    ctx.fillStyle = COLORS.gridEmpty;
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    return;
  }
  const hasEnvelope =
    !Array.isArray(series) &&
    Array.isArray(series.min) &&
    Array.isArray(series.max);
  const valuesMin = hasEnvelope ? series.min : series;
  const valuesMax = hasEnvelope ? series.max : series;
  let max = -Infinity, min = Infinity;
  for (let i = 0; i < valuesMax.length; i++) { if (valuesMax[i] > max) max = valuesMax[i]; }
  for (let i = 0; i < valuesMin.length; i++) { if (valuesMin[i] < min) min = valuesMin[i]; }
  const span = max - min || 1;
  const count = Math.max(valuesMin.length, valuesMax.length);
  const stepX = canvas.width / Math.max(1, count - 1);
  ctx.strokeStyle = off ? COLORS.warning : COLORS.primary;
  ctx.lineWidth = 1;
  if (hasEnvelope) {
    for (let idx = 0; idx < count; idx++) {
      const x = idx * stepX;
      const vMin = valuesMin[idx] ?? valuesMin[valuesMin.length - 1] ?? 0;
      const vMax = valuesMax[idx] ?? valuesMax[valuesMax.length - 1] ?? vMin;
      const y1 = canvas.height - ((vMin - min) / span) * canvas.height;
      const y2 = canvas.height - ((vMax - min) / span) * canvas.height;
      ctx.beginPath();
      ctx.moveTo(x, y1);
      ctx.lineTo(x, y2);
      ctx.stroke();
    }
  } else {
    ctx.beginPath();
    series.forEach((v, idx) => {
      const x = idx * stepX;
      const y = canvas.height - ((v - min) / span) * canvas.height;
      if (idx === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();
  }
}
