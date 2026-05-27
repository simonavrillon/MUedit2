import { UNIFORM_PULSE_COLOR } from "../config.js";
import {
  clearEditDrSelections,
  clearEditPulseSelections,
  setEditDrDraftSelection,
  setEditDrSelection,
  setEditPulseDraftSelection,
  setEditPulseSelection,
  setEditView,
} from "../state/actions.js";

function clampY(py, canvas, getCanvasPlotMetrics) {
  const metrics = getCanvasPlotMetrics(canvas, true);
  const clamped = Math.max(
    metrics.padding.top,
    Math.min(metrics.padding.top + metrics.plotHeight, py),
  );
  return clamped - metrics.padding.top;
}

export function renderEditExplorer(deps) {
  const {
    els,
    state,
    drawSeries,
    renderEditDropdowns,
    getDisplayPulse,
    renderInstantaneousDr,
  } = deps;

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
  const { COLORS } = deps;
  const pulseCanvas = els?.editPulseCanvas || "editPulseCanvas";
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
        ? [{ positions: artifacts, values: artifactVals, color: COLORS.artifactMarker }]
        : [],
    },
  );
  renderInstantaneousDr();
}

export function renderInstantaneousDr(deps) {
  const {
    state,
    els,
    COLORS,
    drawSeries,
    getEditTotalSamples,
    ensureEditFlagged,
  } = deps;

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
  const series = new Array(total).fill(0);
  const markers = [];
  const markerVals = [];
  for (let i = 0; i < spikes.length - 1; i++) {
    const isi = spikes[i + 1] - spikes[i];
    if (isi <= 0) continue;
    const dr = state.edit.fsamp ? state.edit.fsamp / isi : 0;
    const mid = Math.min(
      total - 1,
      Math.max(0, Math.round(spikes[i] + isi / 2)),
    );
    series[mid] = dr;
    markers.push(mid);
    markerVals.push(dr);
  }
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

export function bindEditCanvas(deps) {
  const {
    els,
    state,
    getRawPulse,
    getCanvasPlotMetrics,
    renderEditExplorer,
    setEditStatus,
    addSpikesInSelection,
    addArtifactInSelection,
    deleteSpikesInSelection,
    setEditMode,
  } = deps;

  const canvas = els.editPulseCanvas;
  if (!canvas) return;
  let dragging = false;
  let startPx = 0;
  let endPx = 0;
  let startPy = 0;
  let endPy = 0;

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

  canvas.addEventListener("mousedown", (e) => {
    if (!getPulse().length) return;
    dragging = true;
    const rect = canvas.getBoundingClientRect();
    startPx = e.clientX - rect.left;
    endPx = startPx;
    startPy = e.clientY - rect.top;
    endPy = startPy;
    setEditPulseDraftSelection(state, null);
  });

  canvas.addEventListener("mousemove", (e) => {
    if (!dragging) return;
    const rect = canvas.getBoundingClientRect();
    endPx = e.clientX - rect.left;
    endPy = e.clientY - rect.top;
    const startSample = pxToSample(Math.min(startPx, endPx));
    const endSample = pxToSample(Math.max(startPx, endPx));
    setEditPulseDraftSelection(state, {
      start: Math.max(0, startSample),
      end: Math.max(startSample + 1, endSample),
      yMin: clampY(Math.min(startPy, endPy), canvas, getCanvasPlotMetrics),
      yMax: clampY(Math.max(startPy, endPy), canvas, getCanvasPlotMetrics),
    });
    renderEditExplorer();
  });

  window.addEventListener("mouseup", () => {
    if (!dragging) return;
    dragging = false;
    const delta = Math.abs(endPx - startPx);
    const sel = {
      start: pxToSample(Math.min(startPx, endPx)),
      end: pxToSample(Math.max(startPx, endPx)),
      yMin: clampY(Math.min(startPy, endPy), canvas, getCanvasPlotMetrics),
      yMax: clampY(Math.max(startPy, endPy), canvas, getCanvasPlotMetrics),
    };
    if (delta < 6) {
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

    const startSample = pxToSample(Math.min(startPx, endPx));
    const endSample = pxToSample(Math.max(startPx, endPx));
    setEditPulseSelection(state, {
      start: Math.max(0, startSample),
      end: Math.max(startSample + 1, endSample),
      yMin: sel.yMin,
      yMax: sel.yMax,
    });
    setEditPulseDraftSelection(state, null);
    renderEditExplorer();
  });

  canvas.addEventListener("dblclick", () => {
    const pulse = getPulse();
    if (!pulse.length) return;
    setEditView(state, { start: 0, end: pulse.length });
    clearEditPulseSelections(state);
    renderEditExplorer();
  });
}

export function bindEditDrCanvas(deps) {
  const {
    els,
    state,
    getCanvasPlotMetrics,
    getEditTotalSamples,
    renderEditExplorer,
    deleteDrInSelection,
  } = deps;

  const canvas = els.editDrCanvas;
  if (!canvas) return;
  let dragging = false;
  let startPx = 0;
  let endPx = 0;
  let startPy = 0;
  let endPy = 0;

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

  canvas.addEventListener("mousedown", (e) => {
    dragging = true;
    const rect = canvas.getBoundingClientRect();
    startPx = e.clientX - rect.left;
    endPx = startPx;
    startPy = e.clientY - rect.top;
    endPy = startPy;
    setEditDrDraftSelection(state, null);
  });

  canvas.addEventListener("mousemove", (e) => {
    if (!dragging) return;
    const rect = canvas.getBoundingClientRect();
    endPx = e.clientX - rect.left;
    endPy = e.clientY - rect.top;
    const startSample = pxToSample(Math.min(startPx, endPx));
    const endSample = pxToSample(Math.max(startPx, endPx));
    setEditDrDraftSelection(state, {
      start: Math.max(0, startSample),
      end: Math.max(startSample + 1, endSample),
      yMin: clampY(Math.min(startPy, endPy), canvas, getCanvasPlotMetrics),
      yMax: clampY(Math.max(startPy, endPy), canvas, getCanvasPlotMetrics),
    });
    renderEditExplorer();
  });

  window.addEventListener("mouseup", () => {
    if (!dragging) return;
    dragging = false;
    const sel = {
      start: pxToSample(Math.min(startPx, endPx)),
      end: pxToSample(Math.max(startPx, endPx)),
      yMin: clampY(Math.min(startPy, endPy), canvas, getCanvasPlotMetrics),
      yMax: clampY(Math.max(startPy, endPy), canvas, getCanvasPlotMetrics),
    };
    clearEditDrSelections(state);
    setEditDrSelection(state, {
      start: Math.max(0, sel.start),
      end: Math.max(sel.start + 1, sel.end),
      yMin: sel.yMin,
      yMax: sel.yMax,
    });
    renderEditExplorer();
    if (state.edit.mode === "delete_dr") {
      deleteDrInSelection(sel);
    }
  });
}
