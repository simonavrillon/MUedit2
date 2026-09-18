import { COLORS } from "../config.js";
import {
  drawGridOverlay,
  drawMiniSeries,
  drawRoiRects,
  nextFrame,
} from "./plots.js";
import { gridDimensionsFor } from "../io/grid.js";
import { getCurrentGrid, roiStart, roiEnd } from "../state/selectors.js";
import {
  addArtifactRegion,
  ensureDiscardMasks,
  setArtifactDraft,
  setArtifactMode,
  setChannelTraces,
  setCurrentGrid,
  setDiscardMaskChannel,
  setRoiDraft,
  setRoiForIndex,
} from "../state/actions.js";

/** @typedef {import("../app/context.js").App} App */

/**
 * Decomposition ROIs and artifact windows are drawn on the same canvases, so
 * they travel as one selection list; `kind: "artifact"` is what tells
 * `drawRoiRects` to switch colour. Drafts are appended so an in-flight drag
 * renders alongside the committed windows.
 */
export function buildSelections(state) {
  const rois = state.roiDraft
    ? [...(state.rois || []), state.roiDraft]
    : state.rois || [];
  const artifacts = state.artifactDraft
    ? [...(state.artifactRegions || []), state.artifactDraft]
    : state.artifactRegions || [];
  return [
    ...rois,
    ...artifacts.map((a) => ({ start: a.start, end: a.end, kind: "artifact" })),
  ];
}

/**
 * Sync the +/- control row with state. Driven from `refreshVisuals` so every
 * path that changes artifact windows updates the count and armed state
 * without having to remember to call this itself.
 */
export function renderArtifactControls(els, state) {
  if (els?.artifactCount) {
    els.artifactCount.textContent = String(
      (state.artifactRegions || []).length,
    );
  }
  if (els?.artifactAddBtn) {
    const armed = !!state.artifactMode;
    els.artifactAddBtn.classList.toggle("armed", armed);
    els.artifactAddBtn.setAttribute("aria-pressed", armed ? "true" : "false");
  }
  if (els?.artifactRemoveBtn) {
    els.artifactRemoveBtn.disabled = !(state.artifactRegions || []).length;
  }
}

/** @param {App} app */
export function refreshVisuals(app) {
  const { state, els, renderAuxiliaryChannels, renderMuExplorer } = app;
  const selections = buildSelections(state);
  renderArtifactControls(els, state);
  drawGridOverlay(
    els.emgCanvas,
    state.gridSeries,
    state.gridColors,
    selections,
    state.seriesLength,
  );
  renderAuxiliaryChannels();
  renderMuExplorer();
}

/** @param {App} app */
export function enableRoiSelection(app, canvasId) {
  const {
    state,
    els,
    syncRois,
    refreshVisuals,
    requestQcGridWindow,
    updateProgress,
  } = app;
  const canvas = els?.[canvasId] || document.getElementById(canvasId);
  if (!canvas || canvas.dataset.roiBound === "1") return;
  canvas.dataset.roiBound = "1";

  let dragging = false;
  let startX = 0;
  let endX = 0;
  let dragIsArtifact = false;

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
    setRoiForIndex(state, idx, {
      start: startSample,
      end: Math.max(startSample + 1, endSample),
    });
    setRoiDraft(state, null);
    setChannelTraces(state, []);
    refreshVisuals();
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

  const commitArtifact = () => {
    if (!state.seriesLength) return;
    const { startSample, endSample } = toSamples(startX, endX);
    addArtifactRegion(state, {
      start: startSample,
      end: Math.max(startSample + 1, endSample),
    });
    setArtifactDraft(state, null);
    setArtifactMode(state, false);
    refreshVisuals();
    const n = state.artifactRegions.length;
    updateProgress(
      undefined,
      `Artifact window added (${n} window${n > 1 ? "s" : ""})`,
    );
  };

  canvas.addEventListener("mousedown", (e) => {
    if (!state.seriesLength) return;
    dragging = true;
    dragIsArtifact = !!state.artifactMode;
    const rect = canvas.getBoundingClientRect();
    startX = e.clientX - rect.left;
    endX = startX;
    setRoiDraft(state, null);
    setArtifactDraft(state, null);
  });

  canvas.addEventListener("mousemove", (e) => {
    if (!dragging || !state.seriesLength) return;
    const rect = canvas.getBoundingClientRect();
    endX = e.clientX - rect.left;
    const { startSample, endSample } = toSamples(startX, endX);
    const draft = {
      start: startSample,
      end: Math.max(startSample + 1, endSample),
    };
    if (dragIsArtifact) setArtifactDraft(state, draft);
    else setRoiDraft(state, draft);
    refreshVisuals();
  });

  window.addEventListener("mouseup", () => {
    if (!dragging || !state.seriesLength) {
      dragging = false;
      return;
    }
    dragging = false;
    if (Math.abs(endX - startX) < 4) {
      setRoiDraft(state, null);
      setArtifactDraft(state, null);
      refreshVisuals();
      return;
    }
    if (dragIsArtifact) commitArtifact();
    else commitSelection();
  });
}

/** @param {App} app */
export function renderChannelQC(app, waitForMiniPlots = false) {
  const { state, els, requestQcGridWindow } = app;
  const section = els.qcSection;
  if (!section) return waitForMiniPlots ? Promise.resolve() : undefined;
  section.innerHTML = "";
  ensureDiscardMasks(state);
  let gridIdx = getCurrentGrid(state);
  const allMeans = state.channelMeans || [];
  if (!allMeans[gridIdx] && allMeans.length) {
    gridIdx = 0;
    setCurrentGrid(state, 0);
  }
  const means = allMeans[gridIdx];
  const meanList = Array.isArray(means) ? means : Array.from(means || []);
  if (!means || !meanList.length)
    return waitForMiniPlots ? Promise.resolve() : undefined;
  const wrap = document.createElement("div");
  wrap.className = "qc-grid";

  const cells = document.createElement("div");
  cells.className = "cells";
  const coords = state.coordinates?.[gridIdx] || [];
  const { cols } = gridDimensionsFor(coords);
  cells.style.gridTemplateColumns = `repeat(${cols}, minmax(26px, 1fr))`;

  const mask = state.discardMasks?.[gridIdx] || [];
  const traces = state.channelTraces?.[gridIdx] || [];
  const miniDrawJobs = [];
  if (!traces.length) {
    const roi = state.rois?.[0];
    requestQcGridWindow(
      gridIdx,
      roiStart(roi),
      roiEnd(roi, state.seriesLength),
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
    label.textContent = String(chIdx + 1);
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
      setDiscardMaskChannel(state, gridIdx, chIdx, off ? 0 : 1);
      renderChannelQC(app, false);
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

  const selections = buildSelections(state);
  drawRoiRects(
    ctx,
    selections,
    state.seriesLength,
    canvas.width,
    canvas.height,
  );

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
