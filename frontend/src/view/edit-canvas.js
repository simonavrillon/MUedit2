/**
 * Edit-stage canvas rendering and pointer interaction: pulse-train and
 * discharge-rate plots, the navigation timeline, the bookmark marker, and the
 * drag-to-select ROI handlers. Selection gestures update draft/committed
 * selection state via the action helpers; the container re-renders in response.
 */
import { COLORS, UNIFORM_PULSE_COLOR } from "../config.js";
import { drawSeries, getCanvasPlotMetrics } from "./plots.js";
import {
  clearEditDrSelections,
  clearEditPulseSelections,
  setEditDrDraftSelection,
  setEditDrSelection,
  setEditPulseDraftSelection,
  setEditPulseSelection,
  setEditView,
  setShowBookmark,
} from "../state/actions.js";
import { computeInstantaneousDr } from "../editing/operations.js";
import { renderSelectPair } from "./select-renderers.js";

/** @typedef {import("../app/context.js").App} App */

const TIMELINE_PAD_L = 38;
const TIMELINE_PAD_R = 8;
const TIMELINE_BAR_TOP = 4;
const TIMELINE_BAR_H = 12;

function renderBookmark(canvas, state, muIdx, view, getCanvasPlotMetrics) {
  const bookmark = state.edit.bookmarkPosition;
  if (!bookmark || bookmark.muIdx !== muIdx) return;
  if (!state.edit.showBookmark) return;

  const ctx = canvas.getContext("2d");
  const metrics = getCanvasPlotMetrics(canvas, true, { hideYAxis: false });

  const bookmarkPos = Math.max(
    view.start,
    Math.min(view.end - 1, bookmark.position),
  );
  const frac = (bookmarkPos - view.start) / Math.max(1, view.end - view.start);
  const x = metrics.padding.left + frac * metrics.plotWidth;

  ctx.strokeStyle = "#22c55e";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(x, metrics.padding.top);
  ctx.lineTo(x, metrics.padding.top + metrics.plotHeight);
  ctx.stroke();

  ctx.fillStyle = "#22c55e";
  ctx.font = "12px sans-serif";
  ctx.textAlign = "center";
  ctx.fillText("You stopped here", x, metrics.padding.top + 15);
  ctx.textAlign = "start";
}

function clampY(py, canvas, getCanvasPlotMetrics) {
  const metrics = getCanvasPlotMetrics(canvas, true);
  const clamped = Math.max(
    metrics.padding.top,
    Math.min(metrics.padding.top + metrics.plotHeight, py),
  );
  return clamped - metrics.padding.top;
}

function createDragState(canvas, getCanvasPlotMetrics, pxToSample) {
  let dragging = false;
  let startPx = 0;
  let endPx = 0;
  let startPy = 0;
  let endPy = 0;

  return {
    begin(e) {
      const rect = canvas.getBoundingClientRect();
      startPx = e.clientX - rect.left;
      endPx = startPx;
      startPy = e.clientY - rect.top;
      endPy = startPy;
      dragging = true;
    },
    update(e) {
      if (!dragging) return null;
      const rect = canvas.getBoundingClientRect();
      endPx = e.clientX - rect.left;
      endPy = e.clientY - rect.top;
      return this.selection();
    },
    selection() {
      const startSample = pxToSample(Math.min(startPx, endPx));
      const endSample = pxToSample(Math.max(startPx, endPx));
      return {
        start: Math.max(0, startSample),
        end: Math.max(startSample + 1, endSample),
        yMin: clampY(Math.min(startPy, endPy), canvas, getCanvasPlotMetrics),
        yMax: clampY(Math.max(startPy, endPy), canvas, getCanvasPlotMetrics),
      };
    },
    get dragging() {
      return dragging;
    },
    get delta() {
      return Math.abs(endPx - startPx);
    },
    stop() {
      dragging = false;
    },
  };
}

export function renderEditDropdownsView(els, model) {
  const gridOptions = model.gridNames.map((name, idx) => ({
    value: idx,
    label: `Grid ${idx + 1}${name ? ` • ${name}` : ""}`,
  }));
  const muOptions = model.muOptions.map((muIdx) => ({
    value: muIdx,
    label: `MU ${muIdx + 1}`,
  }));
  renderSelectPair(
    els.editMuGridSelect,
    els.editMuSelect,
    gridOptions,
    muOptions,
    model.targetGrid,
    model.currentMu,
  );
}

/** @param {App} app */
export function renderEditExplorer(app) {
  const {
    els,
    state,
    renderEditDropdowns,
    getDisplayPulse,
    renderInstantaneousDr,
  } = app;

  renderEditDropdowns();
  const muIdx = state.edit.currentMu ?? 0;
  const pulse = getDisplayPulse(muIdx);
  const spikes = state.edit.distimes?.[muIdx] || [];
  if (!state.edit.view || (pulse && state.edit.view.end > pulse.length)) {
    setEditView(state, { start: 0, end: pulse.length || 0 });
  }
  const overlays = [];
  if (state.edit.selectionPulse) overlays.push(state.edit.selectionPulse);
  if (state.edit.draftSelectionPulse)
    overlays.push(state.edit.draftSelectionPulse);
  const markerVals = spikes.map((s) => pulse?.[s] ?? 0);
  const artifacts = state.edit.artifactTimes?.[muIdx] || [];
  const artifactVals = artifacts.map((s) => pulse?.[s] ?? 0);
  const pulseCanvas = els?.editPulseCanvas || "editPulseCanvas";
  const canvasEl =
    typeof pulseCanvas === "string"
      ? document.getElementById(pulseCanvas)
      : pulseCanvas;
  drawSeries(
    pulseCanvas,
    pulse,
    UNIFORM_PULSE_COLOR,
    spikes,
    overlays,
    pulse.length,
    state.edit.view,
    markerVals,
    true,
    {
      showAxes: true,
      hideYAxis: false,
      fsamp: state.edit.fsamp,
      markerColor: COLORS.muPurple,
      extraMarkers: artifacts.length
        ? [
            {
              positions: artifacts,
              values: artifactVals,
              color: COLORS.artifactMarker,
            },
          ]
        : [],
    },
  );
  if (canvasEl) {
    renderBookmark(
      canvasEl,
      state,
      muIdx,
      state.edit.view,
      getCanvasPlotMetrics,
    );
  }
  renderInstantaneousDr();
}

/** @param {App} app */
export function renderInstantaneousDr(app) {
  const { state, els, getEditTotalSamples, ensureEditFlagged } = app;

  const canvas = els?.editDrCanvas || "editDrCanvas";
  const pulse = state.edit.pulseTrains?.[state.edit.currentMu] || [];
  const spikes = state.edit.distimes?.[state.edit.currentMu] || [];
  ensureEditFlagged();
  if (state.edit.flagged?.[state.edit.currentMu]) {
    drawSeries(canvas, [], COLORS.warning);
    return;
  }
  const total = getEditTotalSamples();
  if (!pulse.length || !spikes.length) {
    drawSeries(canvas, [], COLORS.warning);
    return;
  }
  const { series, markers, markerVals } = computeInstantaneousDr(
    spikes,
    state.edit.fsamp,
    total,
  );
  const drSelection = state.edit.selectionDr || state.edit.draftSelectionDr;
  drawSeries(
    canvas,
    series,
    COLORS.warning,
    markers,
    drSelection ? [drSelection] : [],
    total,
    state.edit.view,
    markerVals,
    false,
    {
      showAxes: true,
      hideYAxis: false,
      fsamp: state.edit.fsamp,
      markerColor: COLORS.muPurple,
    },
  );
}

/** @param {App} app */
export function bindEditCanvas(app) {
  const {
    els,
    state,
    getRawPulse,
    renderEditExplorer,
    setEditStatus,
    addSpikesInSelection,
    addArtifactInSelection,
    deleteSpikesInSelection,
    setEditMode,
  } = app;

  const canvas = els.editPulseCanvas;
  if (!canvas) return;

  const getPulse = () => getRawPulse(state.edit.currentMu ?? 0);

  const pxToSample = (px) => {
    const pulse = getPulse();
    const metrics = getCanvasPlotMetrics(canvas, true, { hideYAxis: false });
    const view = state.edit.view || { start: 0, end: pulse.length || 0 };
    const clamped = Math.max(
      metrics.padding.left,
      Math.min(metrics.padding.left + metrics.plotWidth, px),
    );
    const frac = metrics.plotWidth
      ? (clamped - metrics.padding.left) / metrics.plotWidth
      : 0;
    return Math.round(view.start + frac * Math.max(0, view.end - view.start));
  };

  const drag = createDragState(canvas, getCanvasPlotMetrics, pxToSample);

  canvas.addEventListener("mousedown", (e) => {
    if (!getPulse().length) return;
    drag.begin(e);
    setEditPulseDraftSelection(state, null);
  });

  canvas.addEventListener("mousemove", (e) => {
    if (!drag.dragging) return;
    const sel = drag.update(e);
    setEditPulseDraftSelection(state, sel);
    renderEditExplorer();
  });

  window.addEventListener("mouseup", () => {
    if (!drag.dragging) return;
    drag.stop();
    const sel = drag.selection();
    if (drag.delta < 6) {
      if (state.edit.mode === "add") {
        setEditStatus("Drag a box to add spikes", "muted");
        return;
      }
      if (state.edit.mode === "add_artifact") {
        setEditStatus("Drag a box to mark an artifact", "muted");
        return;
      }
      if (state.edit.mode === "delete_spikes") {
        const windowSel = {
          ...sel,
          start: Math.max(0, sel.start - 2),
          end: sel.start + 2,
        };
        deleteSpikesInSelection(windowSel);
      }
      return;
    }

    if (state.edit.mode === "add") {
      addSpikesInSelection(sel);
      setEditMode(null);
      setEditPulseDraftSelection(state, null);
      return;
    }
    if (state.edit.mode === "add_artifact") {
      addArtifactInSelection?.(sel);
      setEditMode(null);
      setEditPulseDraftSelection(state, null);
      return;
    }
    if (state.edit.mode === "delete_spikes") {
      deleteSpikesInSelection(sel);
      setEditMode(null);
      setEditPulseDraftSelection(state, null);
      return;
    }

    setEditPulseSelection(state, sel);
    setEditPulseDraftSelection(state, null);
    renderEditExplorer();
  });

  canvas.addEventListener("dblclick", () => {
    const pulse = getPulse();
    if (!pulse.length) return;
    setEditView(state, { start: 0, end: pulse.length });
    clearEditPulseSelections(state);
    setShowBookmark(state, true);
    renderEditExplorer();
  });
}

/** @param {App} app */
export function renderEditTimeline(app) {
  const { els, state, getDisplayPulse } = app;
  const canvas = els?.editTimelineCanvas;
  if (!canvas) return;

  const ctx = canvas.getContext("2d");
  const w = canvas.clientWidth || canvas.width || 1;
  canvas.width = w;
  canvas.height = 20;
  ctx.clearRect(0, 0, w, 20);

  const muIdx = state.edit.currentMu ?? 0;
  const pulse = getDisplayPulse(muIdx);
  const total = pulse?.length || 0;
  if (!total) return;

  const bw = Math.max(1, w - TIMELINE_PAD_L - TIMELINE_PAD_R);

  ctx.fillStyle = "rgba(255,255,255,0.07)";
  ctx.fillRect(TIMELINE_PAD_L, TIMELINE_BAR_TOP, bw, TIMELINE_BAR_H);

  // Last edit action for this MU: green = added, red = removed
  const muUid = state.edit.muUids?.[muIdx];
  if (muUid && Array.isArray(state.edit.editHistory)) {
    const lastEntry = [...state.edit.editHistory]
      .reverse()
      .find((e) => e.mu_uid === muUid);
    if (lastEntry) {
      const added = [
        ...(lastEntry.spikes_added || []),
        ...(lastEntry.artifacts_added || []),
      ];
      const removed = [
        ...(lastEntry.spikes_removed || []),
        ...(lastEntry.artifacts_removed || []),
      ];
      ctx.fillStyle = "rgba(74,222,128,0.85)";
      added.forEach((s) => {
        ctx.fillRect(
          TIMELINE_PAD_L + Math.round((s / total) * bw),
          TIMELINE_BAR_TOP,
          2,
          TIMELINE_BAR_H,
        );
      });
      ctx.fillStyle = "rgba(248,113,113,0.85)";
      removed.forEach((s) => {
        ctx.fillRect(
          TIMELINE_PAD_L + Math.round((s / total) * bw),
          TIMELINE_BAR_TOP,
          2,
          TIMELINE_BAR_H,
        );
      });
    }
  }

  // Current spike positions (faint purple, drawn on top of history)
  const spikes = state.edit.distimes?.[muIdx] || [];
  ctx.fillStyle = "rgba(231,193,255,0.35)";
  spikes.forEach((s) => {
    const x = TIMELINE_PAD_L + Math.round((s / total) * bw);
    ctx.fillRect(x, TIMELINE_BAR_TOP, 2, TIMELINE_BAR_H);
  });

  // View window
  const view = state.edit.view || { start: 0, end: total };
  const x1 = TIMELINE_PAD_L + (Math.max(0, view.start) / total) * bw;
  const x2 = TIMELINE_PAD_L + (Math.min(total, view.end) / total) * bw;
  const ww = Math.max(4, x2 - x1);
  ctx.fillStyle = "rgba(195,155,242,0.28)";
  ctx.fillRect(x1, TIMELINE_BAR_TOP - 2, ww, TIMELINE_BAR_H + 4);
  ctx.strokeStyle = "rgba(195,155,242,0.75)";
  ctx.lineWidth = 1;
  ctx.strokeRect(
    x1 + 0.5,
    TIMELINE_BAR_TOP - 1.5,
    Math.max(3, ww - 1),
    TIMELINE_BAR_H + 3,
  );
}

/** @param {App} app */
export function bindEditTimeline(app) {
  const { els, state, getDisplayPulse, renderEditExplorer } = app;
  const canvas = els?.editTimelineCanvas;
  if (!canvas) return;

  let dragging = false;
  let startClientX = 0;
  let dragViewStart = 0;
  let didMove = false;

  const getTotal = () =>
    (getDisplayPulse(state.edit.currentMu ?? 0) || []).length;

  const fracFromClientX = (clientX) => {
    const rect = canvas.getBoundingClientRect();
    const bw = Math.max(1, rect.width - TIMELINE_PAD_L - TIMELINE_PAD_R);
    return Math.max(
      0,
      Math.min(1, (clientX - rect.left - TIMELINE_PAD_L) / bw),
    );
  };

  canvas.addEventListener("mousedown", (e) => {
    dragging = true;
    didMove = false;
    startClientX = e.clientX;
    const total = getTotal();
    dragViewStart = (state.edit.view || { start: 0, end: total }).start;
  });

  window.addEventListener("mousemove", (e) => {
    if (!dragging) return;
    if (Math.abs(e.clientX - startClientX) > 3) didMove = true;
    if (!didMove) return;
    const total = getTotal();
    if (!total) return;
    const rect = canvas.getBoundingClientRect();
    const bw = Math.max(1, rect.width - TIMELINE_PAD_L - TIMELINE_PAD_R);
    const delta = Math.round(((e.clientX - startClientX) / bw) * total);
    const view = state.edit.view || { start: 0, end: total };
    const span = view.end - view.start;
    let s = dragViewStart + delta;
    let e2 = s + span;
    if (s < 0) {
      e2 -= s;
      s = 0;
    }
    if (e2 > total) {
      s = Math.max(0, s - (e2 - total));
      e2 = total;
    }
    setEditView(state, { start: s, end: e2 });
    renderEditExplorer();
  });

  window.addEventListener("mouseup", (e) => {
    if (!dragging) return;
    dragging = false;
    if (didMove) return;
    const total = getTotal();
    if (!total) return;
    const view = state.edit.view || { start: 0, end: total };
    const span = view.end - view.start;
    const frac = fracFromClientX(e.clientX);
    let s = Math.round(frac * total - span / 2);
    let e2 = s + span;
    if (s < 0) {
      e2 -= s;
      s = 0;
    }
    if (e2 > total) {
      s = Math.max(0, s - (e2 - total));
      e2 = total;
    }
    setEditView(state, { start: s, end: e2 });
    renderEditExplorer();
  });
}

/** @param {App} app */
export function bindEditDrCanvas(app) {
  const {
    els,
    state,
    getEditTotalSamples,
    renderEditExplorer,
    deleteDrInSelection,
  } = app;

  const canvas = els.editDrCanvas;
  if (!canvas) return;

  const pxToSample = (px) => {
    const metrics = getCanvasPlotMetrics(canvas, true, { hideYAxis: false });
    const total = getEditTotalSamples();
    const view = state.edit.view || { start: 0, end: total };
    const clamped = Math.max(
      metrics.padding.left,
      Math.min(metrics.padding.left + metrics.plotWidth, px),
    );
    const frac = metrics.plotWidth
      ? (clamped - metrics.padding.left) / metrics.plotWidth
      : 0;
    return Math.round(view.start + frac * Math.max(0, view.end - view.start));
  };

  const drag = createDragState(canvas, getCanvasPlotMetrics, pxToSample);

  canvas.addEventListener("mousedown", (e) => {
    drag.begin(e);
    setEditDrDraftSelection(state, null);
  });

  canvas.addEventListener("mousemove", (e) => {
    if (!drag.dragging) return;
    const sel = drag.update(e);
    setEditDrDraftSelection(state, sel);
    renderEditExplorer();
  });

  window.addEventListener("mouseup", () => {
    if (!drag.dragging) return;
    drag.stop();
    const sel = drag.selection();
    clearEditDrSelections(state);
    setEditDrSelection(state, sel);
    renderEditExplorer();
    if (state.edit.mode === "delete_dr") {
      deleteDrInSelection(sel);
    }
  });
}
