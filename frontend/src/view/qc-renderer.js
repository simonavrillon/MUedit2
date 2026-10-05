import { COLORS, traceColors } from "../config.js";
import {
  drawGridOverlay,
  drawMiniSeries,
  drawRoiRects,
  nextFrame,
  prepareCanvas,
  strokeSeries,
} from "./plots.js";
import { seriesPoints, seriesRange } from "../signal/series.js";
import { gridDimensionsFor } from "../io/grid.js";
import { pickRoiSlot, syncRois } from "../signal/qc.js";
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
/** @typedef {import("../app/context.js").Els} Els */
/** @typedef {import("../app/context.js").RoiCanvasId} RoiCanvasId */
/** @typedef {import("../state/state.js").State} State */
/** @typedef {import("./plots.js").Overlay} Overlay */

/**
 * Decomposition ROIs and artifact windows are drawn on the same canvases, so
 * they travel as one selection list; `kind: "artifact"` is what tells
 * `drawRoiRects` to switch colour. Drafts are appended so an in-flight drag
 * renders alongside the committed windows.
 *
 * @param {State} state
 * @returns {Overlay[]}
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
 *
 * @param {Els} els
 * @param {State} state
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
  const { state, els, renderAuxiliaryChannels } = app;
  const selections = buildSelections(state);
  renderArtifactControls(els, state);
  drawGridOverlay(
    els.emgCanvas,
    state.gridSeries,
    traceColors(),
    selections,
    state.seriesLength,
  );
  renderAuxiliaryChannels();
}

/**
 * @param {App} app
 * @param {RoiCanvasId} canvasId
 */
export function enableRoiSelection(app, canvasId) {
  const { state, els, refreshVisuals, requestQcGridWindow, setStatus } = app;
  const canvas = els[canvasId];
  if (!canvas || canvas.dataset.roiBound === "1") return;
  canvas.dataset.roiBound = "1";

  let dragging = false;
  let startX = 0;
  let endX = 0;
  let dragIsArtifact = false;

  const toSamples = (/** @type {number} */ sx, /** @type {number} */ ex) => {
    const width = canvas.clientWidth || 1;
    const total = state.seriesLength || 0;
    const s = Math.max(0, Math.min(width, Math.min(sx, ex)));
    const e = Math.max(0, Math.min(width, Math.max(sx, ex)));
    const startSample = Math.round((s / width) * total);
    const endSample = Math.round((e / width) * total);
    return { startSample, endSample };
  };

  const commitSelection = () => {
    if (!state.seriesLength) return;
    const { startSample, endSample } = toSamples(startX, endX);
    const nwin = Number(els.nwindows?.value) || 1;
    syncRois(state, nwin);
    const span = {
      start: startSample,
      end: Math.max(startSample + 1, endSample),
    };
    setRoiForIndex(
      state,
      pickRoiSlot(state.rois, span, state.seriesLength),
      span,
    );
    setRoiDraft(state, null);
    setChannelTraces(state, []);
    refreshVisuals();
    requestQcGridWindow(
      state.currentGrid,
      state.rois[0]?.start || 0,
      state.rois[0]?.end || state.seriesLength,
    );
    setStatus(
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
    setStatus(`Artifact window added (${n} window${n > 1 ? "s" : ""})`);
  };

  canvas.addEventListener("pointerdown", (e) => {
    if (!state.seriesLength) return;
    canvas.setPointerCapture(e.pointerId);
    dragging = true;
    dragIsArtifact = !!state.artifactMode;
    const rect = canvas.getBoundingClientRect();
    startX = e.clientX - rect.left;
    endX = startX;
    setRoiDraft(state, null);
    setArtifactDraft(state, null);
  });

  canvas.addEventListener("pointermove", (e) => {
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
    app.scheduleRefreshVisuals();
  });

  canvas.addEventListener("pointercancel", () => {
    if (!dragging) return;
    dragging = false;
    setRoiDraft(state, null);
    setArtifactDraft(state, null);
    app.scheduleRefreshVisuals();
  });

  canvas.addEventListener("pointerup", () => {
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

/**
 * The channel grid on screen: the data it lays out, and each channel's cell
 * and trace canvas.
 *
 * @typedef {object} BuiltGrid
 * @property {number} gridIdx
 * @property {number[]} means
 * @property {number[][] | undefined} coords
 * @property {{ cell: HTMLElement, mini: HTMLCanvasElement }[]} cells
 */

/** @type {WeakMap<HTMLElement, BuiltGrid>} */
const builtGrids = new WeakMap();

/**
 * Lay out one cell per channel of `gridIdx`. A click toggles that channel
 * alone: its mask, its cell and its trace.
 *
 * @param {App} app
 * @param {HTMLElement} section
 * @param {number} gridIdx
 * @param {number[]} means
 * @param {number[][] | undefined} coords
 * @returns {BuiltGrid}
 */
function buildChannelGrid(app, section, gridIdx, means, coords) {
  const { state } = app;
  const positions = coords || [];
  const { cols } = gridDimensionsFor(positions);
  const wrap = document.createElement("div");
  wrap.className = "qc-grid";
  const cellsEl = document.createElement("div");
  cellsEl.className = "cells";
  cellsEl.style.gridTemplateColumns = `repeat(${cols}, minmax(26px, 1fr))`;

  const cells = means.map((val, chIdx) => {
    const pos = positions[chIdx] || [];
    const r = pos[0] ?? Math.floor(chIdx / cols);
    const c = pos[1] ?? chIdx % cols;
    const cell = document.createElement("button");
    cell.className = "qc-cell";
    cell.style.gridRow = `${r + 1}`;
    cell.style.gridColumn = `${c + 1}`;
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
      const off = state.discardMasks[gridIdx]?.[chIdx] !== 1;
      setDiscardMaskChannel(state, gridIdx, chIdx, off ? 1 : 0);
      cell.classList.toggle("off", off);
      drawMiniSeries(mini, state.channelTraces[gridIdx]?.[chIdx], off);
    });
    cellsEl.appendChild(cell);
    return { cell, mini };
  });

  wrap.appendChild(cellsEl);
  section.innerHTML = "";
  section.appendChild(wrap);
  return { gridIdx, means, coords, cells };
}

/**
 * Show the current grid's channels: their cells are laid out once per grid's
 * data, and each redraw only marks the discarded ones and redraws the traces.
 *
 * @param {App} app
 * @param {boolean} [waitForMiniPlots]
 * @returns {Promise<void> | undefined}
 */
export function renderChannelQC(app, waitForMiniPlots = false) {
  const { state, els, requestQcGridWindow } = app;
  const section = els.qcSection;
  const nothing = () => (waitForMiniPlots ? Promise.resolve() : undefined);
  if (!section) return nothing();
  ensureDiscardMasks(state);
  let gridIdx = getCurrentGrid(state);
  if (!state.channelMeans[gridIdx] && state.channelMeans.length) {
    gridIdx = 0;
    setCurrentGrid(state, 0);
  }
  const means = state.channelMeans[gridIdx];
  if (!means?.length) {
    section.innerHTML = "";
    builtGrids.delete(section);
    return nothing();
  }
  const coords = state.coordinates[gridIdx];
  let built = builtGrids.get(section);
  const fresh =
    !built ||
    built.gridIdx !== gridIdx ||
    built.means !== means ||
    built.coords !== coords;
  if (!built || fresh) {
    built = buildChannelGrid(app, section, gridIdx, means, coords);
    builtGrids.set(section, built);
  }

  const mask = state.discardMasks[gridIdx] || [];
  const traces = state.channelTraces[gridIdx] || [];
  if (!traces.length) {
    const roi = state.rois[0];
    requestQcGridWindow(
      gridIdx,
      roiStart(roi),
      roiEnd(roi, state.seriesLength),
    );
  }
  const { cells } = built;
  cells.forEach(({ cell }, chIdx) => {
    cell.classList.toggle("off", mask[chIdx] === 1);
  });
  const drawMinis = () => {
    cells.forEach(({ mini }, chIdx) => {
      drawMiniSeries(mini, traces[chIdx], mask[chIdx] === 1);
    });
  };
  if (waitForMiniPlots) {
    return nextFrame().then(() => {
      drawMinis();
      return nextFrame();
    });
  }
  // New cells get their size at the next layout; kept ones have it now.
  if (fresh) setTimeout(drawMinis, 0);
  else drawMinis();
  return undefined;
}

/**
 * @param {Els} els
 * @param {State} state
 */
export function populateAuxSelector(els, state) {
  const sel = els.auxSelector;
  if (!sel) return;
  sel.innerHTML = '<option value="-1">All channels</option>';
  if (state.auxNames && state.auxNames.length) {
    state.auxNames.forEach((name, idx) => {
      const opt = document.createElement("option");
      opt.value = String(idx);
      opt.textContent = name || `Aux ${idx + 1}`;
      sel.appendChild(opt);
    });
  }
}

/**
 * @param {Els} els
 * @param {State} state
 */
export function renderAuxiliaryChannels(els, state) {
  const prepared = prepareCanvas(els.auxCanvas, { height: 120 });
  if (!prepared) return;
  const { ctx, width, height } = prepared;

  if (!state.auxSeries || !state.auxSeries.length) {
    ctx.fillStyle = COLORS.muted;
    ctx.font = "12px sans-serif";
    ctx.fillText("No auxiliary data", 12, 24);
    return;
  }

  const selectedIdx = parseInt(els.auxSelector?.value ?? "-1", 10);
  const shown = state.auxSeries.map((row, idx) =>
    selectedIdx === -1 || selectedIdx === idx ? row : null,
  );
  const { min: globalMin, max: globalMax } = seriesRange(shown);
  if (!Number.isFinite(globalMin) || !Number.isFinite(globalMax)) return;
  const span = globalMax - globalMin || 1;

  const selections = buildSelections(state);
  drawRoiRects(ctx, selections, state.seriesLength, width, height);

  const colors = traceColors();
  let labelCount = 0;
  shown.forEach((row, idx) => {
    if (!row || !seriesPoints(row)) return;
    ctx.strokeStyle = colors[idx % colors.length];
    ctx.lineWidth = 1;
    strokeSeries(
      ctx,
      row,
      0,
      width,
      (v) => height - ((v - globalMin) / span) * height,
    );

    ctx.fillStyle = ctx.strokeStyle;
    ctx.font = "10px sans-serif";
    const name = state.auxNames[idx] || `Aux ${idx + 1}`;
    ctx.fillText(name, 5, 12 + labelCount * 12);
    labelCount++;
  });
}
