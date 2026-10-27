/**
 * QC drawing: the EMG grid with its windows and artifacts, ROI drags, the
 * channel grid and the aux channels. Draws the state it is given.
 */
import { COLORS, canvasFont, traceColors } from "../config.js";
import {
  drawGridOverlay,
  drawMiniSeries,
  drawRoiRects,
  prepareCanvas,
  strokeSeries,
} from "./plots.js";
import { seriesPoints, seriesRange } from "../signal/series.js";
import { gridColumnCount } from "../io/grid.js";
import { pickRoiSlot, syncRois } from "../signal/qc.js";
import {
  describeWindow,
  drawnRois,
  isPlaceholder,
  isSegmentChosen,
  segmentAt,
  segmentGlyph,
  segmentTypes,
  syncChosenWindows,
  toggleSegment,
} from "../signal/segments.js";
import { getCurrentGrid } from "../state/selectors.js";
import {
  addArtifactRegion,
  setArtifactDraft,
  setArtifactMode,
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
 * Target segments, decomposition ROIs and artifact windows are drawn on the
 * same canvases, so they travel as one selection list, in drawing order:
 * segments under the windows, artifacts on top. `kind` is what tells
 * `drawRoiRects` to switch colour. Drafts are appended so an in-flight drag
 * renders alongside the committed windows.
 *
 * @param {State} state
 * @returns {Overlay[]}
 */
export function buildSelections(state) {
  const segments = (state.segments || []).map((s) => ({
    start: s.start,
    end: s.end,
    kind: s.kind,
    chosen: isSegmentChosen(state, s),
    glyph: segmentGlyph(s),
    cycles: s.cycles,
  }));
  const total = state.totalSamples || 0;
  // A whole-recording placeholder stays faint, or it would hide every segment.
  const rois = (
    state.roiDraft ? [...(state.rois || []), state.roiDraft] : state.rois || []
  ).map((r) =>
    segments.length > 0 && !isPlaceholder(r, total)
      ? { start: r.start, end: r.end, strong: true }
      : r,
  );
  const artifacts = state.artifactDraft
    ? [...(state.artifactRegions || []), state.artifactDraft]
    : state.artifactRegions || [];
  return [
    ...segments,
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

/**
 * Sync the segment-type picker with state: a type is ticked when the windows
 * cover all its segments, half-ticked when they cover some. The rows are
 * rebuilt only when the types change, so a ticked box keeps focus.
 *
 * @param {Els} els
 * @param {State} state
 */
export function renderSegmentTypes(els, state) {
  const btn = els.segmentTypeBtn;
  const menu = els.segmentTypeMenu;
  if (!btn || !menu) return;
  const types = segmentTypes(state);
  btn.disabled = !types.length;
  const picked = types.filter((t) => t.chosen === t.count);
  btn.textContent = !types.length
    ? "No segments"
    : picked.map((t) => `${t.glyph} ${t.type}`).join(" + ") || "Segment types";
  if (!types.length) {
    menu.hidden = true;
    btn.setAttribute("aria-expanded", "false");
  }
  const key = types.map((t) => `${t.type}:${t.count}`).join(",");
  if (menu.dataset.types !== key) {
    menu.dataset.types = key;
    menu.innerHTML = "";
    for (const t of types) {
      const row = document.createElement("label");
      row.className = "segment-picker-row";
      const box = document.createElement("input");
      box.type = "checkbox";
      box.value = t.type;
      const glyph = document.createElement("span");
      glyph.className = "segment-picker-glyph";
      glyph.dataset.kind = t.kind;
      glyph.textContent = t.glyph;
      const name = document.createElement("span");
      name.textContent = `${t.type} (${t.count})`;
      row.appendChild(box);
      row.appendChild(glyph);
      row.appendChild(name);
      menu.appendChild(row);
    }
  }
  types.forEach((t, i) => {
    const box = /** @type {HTMLInputElement} */ (menu.children[i].children[0]);
    box.checked = t.chosen === t.count;
    box.indeterminate = t.chosen > 0 && t.chosen < t.count;
  });
}

/** @param {App} app */
export function refreshVisuals(app) {
  const { state, els, renderAuxiliaryChannels } = app;
  const selections = buildSelections(state);
  renderArtifactControls(els, state);
  // A drag redraws every frame but leaves the windows as they were.
  if (!state.roiDraft && !state.artifactDraft) renderSegmentTypes(els, state);
  drawGridOverlay(
    els.emgCanvas,
    state.gridSeries,
    traceColors(),
    selections,
    state.totalSamples,
  );
  renderAuxiliaryChannels();
}

/**
 * Let drags on the canvas set the analysis windows, or add an artifact window
 * when one is armed. Bound once, at setup; a drag before a signal is loaded
 * is ignored.
 *
 * @param {App} app
 * @param {RoiCanvasId} canvasId
 */
export function enableRoiSelection(app, canvasId) {
  const { state, els, refreshVisuals, setStatus } = app;
  const canvas = els[canvasId];
  if (!canvas) return;

  let dragging = false;
  let startX = 0;
  let endX = 0;
  let dragIsArtifact = false;

  const toSamples = (/** @type {number} */ sx, /** @type {number} */ ex) => {
    const width = canvas.clientWidth || 1;
    const total = state.totalSamples || 0;
    const s = Math.max(0, Math.min(width, Math.min(sx, ex)));
    const e = Math.max(0, Math.min(width, Math.max(sx, ex)));
    const startSample = Math.round((s / width) * total);
    const endSample = Math.round((e / width) * total);
    return { startSample, endSample };
  };

  const commitSelection = () => {
    if (!state.totalSamples) return;
    const { startSample, endSample } = toSamples(startX, endX);
    const nwin = Number(els.nwindows?.value) || 1;
    syncRois(state, nwin);
    const span = {
      start: startSample,
      end: Math.max(startSample + 1, endSample),
    };
    setRoiForIndex(
      state,
      pickRoiSlot(state.rois, span, state.totalSamples),
      span,
    );
    setRoiDraft(state, null);
    refreshVisuals();
    app.ensureQcTraces();
    setStatus(
      `ROI updated (${state.rois.length} window${state.rois.length > 1 ? "s" : ""})`,
    );
  };

  const commitArtifact = () => {
    if (!state.totalSamples) return;
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

  const toggleSegmentAt = (/** @type {number} */ x) => {
    const idx = segmentAt(state.segments, toSamples(x, x).startSample);
    if (idx < 0) return;
    const total = syncChosenWindows(app, toggleSegment(state, idx));
    const seg = state.segments[idx];
    const holder = drawnRois(state).find(
      (r) => r.start <= seg.start && r.end >= seg.end,
    );
    setStatus(
      holder
        ? `Window ${state.rois.indexOf(holder) + 1}: ${describeWindow(state, holder)} — ${total}`
        : `Segment removed — ${total}`,
    );
  };

  canvas.addEventListener("pointerdown", (e) => {
    if (!state.totalSamples) return;
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
    if (!dragging || !state.totalSamples) return;
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
    if (!dragging || !state.totalSamples) {
      dragging = false;
      return;
    }
    dragging = false;
    if (Math.abs(endX - startX) < 4) {
      setRoiDraft(state, null);
      setArtifactDraft(state, null);
      if (!dragIsArtifact) toggleSegmentAt(endX);
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
 * @property {number} grid
 * @property {number[]} means
 * @property {number[][] | undefined} coords
 * @property {{ cell: HTMLElement, mini: HTMLCanvasElement }[]} cells
 */

/** @type {WeakMap<HTMLElement, BuiltGrid>} */
const builtGrids = new WeakMap();

/**
 * Lay out one cell per channel of `grid`. A click toggles that channel
 * alone: its mask, its cell and its trace.
 *
 * @param {App} app
 * @param {HTMLElement} section
 * @param {number} grid
 * @param {number[]} means
 * @param {number[][] | undefined} coords
 * @returns {BuiltGrid}
 */
function buildChannelGrid(app, section, grid, means, coords) {
  const { state } = app;
  const positions = coords || [];
  const cols = gridColumnCount(positions);
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
      const off = state.discardMasks[grid]?.[chIdx] !== 1;
      setDiscardMaskChannel(state, grid, chIdx, off ? 1 : 0);
      cell.classList.toggle("off", off);
      drawMiniSeries(mini, state.channelTraces[grid]?.[chIdx], off);
    });
    cellsEl.appendChild(cell);
    return { cell, mini };
  });

  wrap.appendChild(cellsEl);
  section.innerHTML = "";
  section.appendChild(wrap);
  return { grid, means, coords, cells };
}

/**
 * Draw the current grid's channels: their cells are laid out once per grid's
 * data, and each redraw only marks the discarded ones and redraws the traces.
 *
 * @param {App} app
 */
export function drawChannelQC(app) {
  const { state, els } = app;
  const section = els.qcSection;
  if (!section) return;
  const grid = getCurrentGrid(state);
  const means = state.channelMeans[grid];
  if (!means?.length) {
    section.innerHTML = "";
    builtGrids.delete(section);
    return;
  }
  const coords = state.coordinates[grid];
  let built = builtGrids.get(section);
  const fresh =
    !built ||
    built.grid !== grid ||
    built.means !== means ||
    built.coords !== coords;
  if (!built || fresh) {
    built = buildChannelGrid(app, section, grid, means, coords);
    builtGrids.set(section, built);
  }

  const mask = state.discardMasks[grid] || [];
  const traces = state.channelTraces[grid] || [];
  const { cells } = built;
  cells.forEach(({ cell }, chIdx) => {
    cell.classList.toggle("off", mask[chIdx] === 1);
  });
  // Reading a new cell's size lays it out, so its trace is drawn to fit.
  cells.forEach(({ mini }, chIdx) => {
    drawMiniSeries(mini, traces[chIdx], mask[chIdx] === 1);
  });
}

/**
 * @param {Els} els
 * @param {State} state
 */
export function populateAuxSelector(els, state) {
  if (els.qcSegmentBtn) els.qcSegmentBtn.disabled = !state.auxNames.length;
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
    ctx.font = canvasFont();
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
  drawRoiRects(ctx, selections, state.totalSamples, width, height);

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
    ctx.font = canvasFont();
    const name = state.auxNames[idx] || `Aux ${idx + 1}`;
    ctx.fillText(name, 5, 12 + labelCount * 12);
    labelCount++;
  });
}
