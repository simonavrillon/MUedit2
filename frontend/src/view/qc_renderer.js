import { COLORS } from "../config.js";

export function refreshVisuals(deps) {
  const {
    state,
    els,
    drawGridOverlay,
    renderAuxiliaryChannels,
    renderMuExplorer,
  } = deps;
  const selections = state.roiDraft
    ? [...(state.rois || []), state.roiDraft]
    : state.rois;
  const emgCanvas = els?.emgCanvas || "emgCanvas";
  drawGridOverlay(
    emgCanvas,
    state.gridSeries,
    state.gridColors,
    selections,
    state.seriesLength,
  );
  renderAuxiliaryChannels();
  renderMuExplorer();
}

export function enableRoiSelection(deps, canvasId) {
  const {
    state,
    els,
    syncRois,
    refreshVisualsFn,
    requestQcGridWindow,
    updateProgress,
  } = deps;
  const canvas = els?.[canvasId] || document.getElementById(canvasId);
  if (!canvas || canvas.dataset.roiBound === "1") return;
  canvas.dataset.roiBound = "1";

  let dragging = false;
  let startX = 0;
  let endX = 0;

  const toSamples = (sx, ex) => {
    const width = canvas.clientWidth || 1;
    const s = Math.max(0, Math.min(width, Math.min(sx, ex)));
    const e = Math.max(0, Math.min(width, Math.max(sx, ex)));
    const startSample = Math.round((s / width) * state.seriesLength);
    const endSample = Math.round((e / width) * state.seriesLength);
    return { startSample, endSample };
  };

  const commitSelection = () => {
    if (!state.seriesLength) return;
    const { startSample, endSample } = toSamples(startX, endX);
    const nwin = Number(els.nwindows?.value) || 1;
    syncRois(nwin);
    let idx = 0;
    let best = Number.MAX_SAFE_INTEGER;
    state.rois.forEach((r, i) => {
      const dist = Math.abs(r.start - startSample);
      if (dist < best) {
        best = dist;
        idx = i;
      }
    });
    state.rois[idx] = {
      start: startSample,
      end: Math.max(startSample + 1, endSample),
    };
    state.roiDraft = null;
    state.channelTraces = [];
    refreshVisualsFn();
    requestQcGridWindow(
      state.currentGrid,
      state.rois[0]?.start || 0,
      state.rois[0]?.end || state.seriesLength,
    );
    updateProgress(
      undefined,
      `ROI updated (${state.rois.length} window${state.rois.length > 1 ? "s" : ""})`,
    );
  };

  canvas.addEventListener("mousedown", (e) => {
    if (!state.seriesLength) return;
    dragging = true;
    const rect = canvas.getBoundingClientRect();
    startX = e.clientX - rect.left;
    endX = startX;
    state.roiDraft = null;
  });

  canvas.addEventListener("mousemove", (e) => {
    if (!dragging || !state.seriesLength) return;
    const rect = canvas.getBoundingClientRect();
    endX = e.clientX - rect.left;
    const { startSample, endSample } = toSamples(startX, endX);
    state.roiDraft = {
      start: startSample,
      end: Math.max(startSample + 1, endSample),
    };
    refreshVisualsFn();
  });

  window.addEventListener("mouseup", () => {
    if (!dragging || !state.seriesLength) {
      dragging = false;
      return;
    }
    dragging = false;
    if (Math.abs(endX - startX) < 4) {
      state.roiDraft = null;
      refreshVisualsFn();
      return;
    }
    commitSelection();
  });
}

export function renderChannelQC(deps, waitForMiniPlots = false) {
  const {
    state,
    els,
    nextFrame,
    drawMiniSeries,
    requestQcGridWindow,
    getCurrentGrid,
    ensureDiscardMasks,
  } = deps;
  const section = els.qcSection;
  if (!section) return waitForMiniPlots ? Promise.resolve() : undefined;
  section.innerHTML = "";
  ensureDiscardMasks();
  let gridIdx = getCurrentGrid();
  const allMeans = state.channelMeans || [];
  if (!allMeans[gridIdx] && allMeans.length) {
    gridIdx = 0;
    state.currentGrid = 0;
  }
  const means = allMeans[gridIdx];
  if (!means) return waitForMiniPlots ? Promise.resolve() : undefined;
  const meanList = Array.isArray(means) ? means : Array.from(means || []);
  if (!meanList.length) return waitForMiniPlots ? Promise.resolve() : undefined;
  const wrap = document.createElement("div");
  wrap.className = "qc-grid";

  const cells = document.createElement("div");
  cells.className = "cells";
  const coords = state.coordinates?.[gridIdx] || [];
  let maxRow = 0;
  let maxCol = 0;
  coords.forEach((c) => {
    if (Array.isArray(c) && c.length >= 2) {
      maxRow = Math.max(maxRow, c[0]);
      maxCol = Math.max(maxCol, c[1]);
    }
  });
  const cols = (maxCol || 0) + 1;
  cells.style.gridTemplateColumns = `repeat(${cols}, minmax(26px, 1fr))`;

  const mask = state.discardMasks?.[gridIdx] || [];
  const traces = state.channelTraces?.[gridIdx] || [];
  const miniDrawJobs = [];
  if (!traces.length) {
    const roi = state.rois?.[0];
    requestQcGridWindow(
      gridIdx,
      Number.isFinite(roi?.start) ? roi.start : 0,
      Number.isFinite(roi?.end) ? roi.end : state.seriesLength,
    );
  }
  meanList.forEach((val, chIdx) => {
    const pos = coords[chIdx] || [];
    const r = pos[0] ?? Math.floor(chIdx / cols);
    const c = pos[1] ?? chIdx % cols;
    const cell = document.createElement("button");
    cell.className = "qc-cell";
    cell.style.gridRow = `${r + 1}`;
    cell.style.gridColumn = `${c + 1}`;
    const off = mask[chIdx] === 1;
    if (off) cell.classList.add("off");
    const label = document.createElement("div");
    label.textContent = chIdx + 1;
    label.className = "qc-label";
    const mini = document.createElement("canvas");
    mini.height = 26;
    mini.className = "qc-mini";
    cell.appendChild(label);
    cell.appendChild(mini);
    const meanVal = Number(val);
    const meanText = Number.isFinite(meanVal) ? meanVal.toFixed(3) : "n/a";
    cell.title = `Channel ${chIdx + 1} • mean |EMG| ${meanText}`;
    cell.addEventListener("click", () => {
      mask[chIdx] = off ? 0 : 1;
      state.discardMasks[gridIdx] = mask;
      renderChannelQC(deps, false);
    });
    miniDrawJobs.push(() =>
      drawMiniSeries(mini, traces[chIdx], mask[chIdx] === 1),
    );
    cells.appendChild(cell);
  });

  wrap.appendChild(cells);
  section.appendChild(wrap);
  const runMiniDraw = () => {
    miniDrawJobs.forEach((job) => job());
  };
  if (waitForMiniPlots) {
    return nextFrame().then(() => {
      runMiniDraw();
      return nextFrame();
    });
  }
  setTimeout(runMiniDraw, 0);
  return undefined;
}

export function populateAuxSelector(els, state) {
  const sel = els.auxSelector;
  if (!sel) return;
  sel.innerHTML = '<option value="-1">All channels</option>';
  if (state.auxNames && state.auxNames.length) {
    state.auxNames.forEach((name, idx) => {
      const opt = document.createElement("option");
      opt.value = idx;
      opt.textContent = name || `Aux ${idx + 1}`;
      sel.appendChild(opt);
    });
  }
}

export function renderAuxiliaryChannels(els, state) {
  const canvas = els.auxCanvas;
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  const w = canvas.clientWidth || canvas.width || 1;
  const h = canvas.clientHeight || canvas.height || 120;
  canvas.width = w;
  canvas.height = h;
  ctx.clearRect(0, 0, canvas.width, canvas.height);

  if (!state.auxSeries || !state.auxSeries.length) {
    ctx.fillStyle = COLORS.muted;
    ctx.font = "12px sans-serif";
    ctx.fillText("No auxiliary data", 12, 24);
    return;
  }

  const selectedIdx = parseInt(els.auxSelector?.value ?? "-1", 10);
  let globalMin = Infinity;
  let globalMax = -Infinity;
  state.auxSeries.forEach((s, idx) => {
    if (!Array.isArray(s)) return;
    if (selectedIdx !== -1 && selectedIdx !== idx) return;
    s.forEach((v) => {
      if (v < globalMin) globalMin = v;
      if (v > globalMax) globalMax = v;
    });
  });

  if (globalMin === Infinity) return;
  const span = globalMax - globalMin || 1;

  const selections = state.roiDraft
    ? [...(state.rois || []), state.roiDraft]
    : state.rois;
  if (selections && selections.length && state.seriesLength) {
    selections.forEach((sel) => {
      const startX = (sel.start / state.seriesLength) * canvas.width;
      const endX = (sel.end / state.seriesLength) * canvas.width;
      ctx.fillStyle = COLORS.roiFill;
      ctx.fillRect(
        Math.min(startX, endX),
        0,
        Math.abs(endX - startX),
        canvas.height,
      );
      ctx.strokeStyle = COLORS.roiStroke;
      ctx.lineWidth = 1;
      ctx.strokeRect(
        Math.min(startX, endX),
        0,
        Math.abs(endX - startX),
        canvas.height,
      );
    });
  }

  let labelCount = 0;
  state.auxSeries.forEach((s, idx) => {
    if (!s || !s.length) return;
    if (selectedIdx !== -1 && selectedIdx !== idx) return;
    const stepX = canvas.width / Math.max(1, s.length - 1);
    ctx.strokeStyle = state.gridColors[idx % state.gridColors.length];
    ctx.lineWidth = 1;
    ctx.beginPath();
    s.forEach((v, i) => {
      const x = i * stepX;
      const y = canvas.height - ((v - globalMin) / span) * canvas.height;
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();

    ctx.fillStyle = ctx.strokeStyle;
    ctx.font = "10px sans-serif";
    const name = state.auxNames[idx] || `Aux ${idx + 1}`;
    ctx.fillText(name, 5, 12 + labelCount * 12);
    labelCount++;
  });
}
