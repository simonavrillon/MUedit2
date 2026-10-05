/**
 * Edit-stage canvas rendering and pointer interaction: pulse-train and
 * discharge-rate plots, the navigation timeline, the bookmark marker, and the
 * pulse plot's drag-to-select handlers. The pulse plot draws the window of
 * the current MU the server sent (`state.edit.pulseView`). The draw functions
 * only draw; the edit stage settles the state and asks for missing windows
 * around them. Selection gestures update draft/committed selection state via
 * the action helpers; the container re-renders in response.
 */
import { COLORS, canvasFont } from "../config.js";
import {
  drawTrace,
  getCanvasPlotMetrics,
  prepareCanvas,
  viewScale,
} from "./plots.js";
import {
  clearEditPulseSelections,
  setEditPulseDraftSelection,
  setEditPulseSelection,
  setEditView,
  setShowBookmark,
} from "../state/actions.js";
import {
  cachedDischargeRates,
  clampView,
  fastestRateInView,
} from "../editing/operations.js";
import { renderSelectPair } from "./select-renderers.js";

/** @typedef {import("../app/context.js").App} App */
/** @typedef {import("../app/context.js").Els} Els */
/** @typedef {import("../app/context.js").Span} Span */
/** @typedef {import("../state/state.js").State} State */
/** @typedef {import("../editing/operations.js").EditDropdownModel} EditDropdownModel */

const TIMELINE_PAD_L = 38;
const TIMELINE_PAD_R = 8;
const TIMELINE_BAR_TOP = 4;
const TIMELINE_BAR_H = 12;

/**
 * Map a canvas x pixel to the sample it sits over within `view`.
 * @param {HTMLCanvasElement} canvas
 * @param {number} px
 * @param {Span} view
 */
function pxToViewSample(canvas, px, view) {
  const { padding, plotWidth } = getCanvasPlotMetrics(canvas, true);
  const clamped = Math.max(
    padding.left,
    Math.min(padding.left + plotWidth, px),
  );
  return Math.round(viewScale(view, padding.left, plotWidth).toSample(clamped));
}

/**
 * @param {HTMLCanvasElement} canvas
 * @param {State} state
 * @param {number} muIdx
 * @param {Span | null} view
 */
function renderBookmark(canvas, state, muIdx, view) {
  const bookmark = state.edit.bookmarkPosition;
  if (!view || !bookmark || bookmark.muIdx !== muIdx) return;
  if (!state.edit.showBookmark) return;

  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  const metrics = getCanvasPlotMetrics(canvas, true);

  const bookmarkPos = Math.max(
    view.start,
    Math.min(view.end - 1, bookmark.position),
  );
  const x = viewScale(view, metrics.padding.left, metrics.plotWidth).toX(
    bookmarkPos,
  );

  ctx.strokeStyle = COLORS.bookmark;
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(x, metrics.padding.top);
  ctx.lineTo(x, metrics.padding.top + metrics.plotHeight);
  ctx.stroke();

  ctx.fillStyle = COLORS.bookmark;
  ctx.font = canvasFont();
  ctx.textAlign = "center";
  ctx.fillText("You stopped here", x, metrics.padding.top + 15);
  ctx.textAlign = "start";
}

/**
 * @param {number} py
 * @param {HTMLCanvasElement} canvas
 */
function clampY(py, canvas) {
  const metrics = getCanvasPlotMetrics(canvas, true);
  const clamped = Math.max(
    metrics.padding.top,
    Math.min(metrics.padding.top + metrics.plotHeight, py),
  );
  return clamped - metrics.padding.top;
}

/**
 * @param {HTMLCanvasElement} canvas
 * @param {(px: number) => number} pxToSample
 */
function createDragState(canvas, pxToSample) {
  let dragging = false;
  let startPx = 0;
  let endPx = 0;
  let startPy = 0;
  let endPy = 0;

  return {
    /**
     * Start a drag; the canvas keeps the pointer until it is released, even
     * outside it.
     *
     * @param {PointerEvent} e
     */
    begin(e) {
      canvas.setPointerCapture(e.pointerId);
      const rect = canvas.getBoundingClientRect();
      startPx = e.clientX - rect.left;
      endPx = startPx;
      startPy = e.clientY - rect.top;
      endPy = startPy;
      dragging = true;
    },
    /** @param {PointerEvent} e */
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
        yMin: clampY(Math.min(startPy, endPy), canvas),
        yMax: clampY(Math.max(startPy, endPy), canvas),
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

/**
 * @param {Els} els
 * @param {EditDropdownModel} model
 */
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

/**
 * The current MU's window as drawn: a flagged MU shows as a flat line at 0,
 * with its discharges on it.
 *
 * @param {import("../state/state.js").PulseView} shown
 * @param {boolean} flagged
 */
function displayedPulse(shown, flagged) {
  if (!flagged) {
    return {
      trace: shown,
      spikeValues: shown.spikeValues,
      artifactValues: shown.artifactValues,
      range: undefined,
    };
  }
  const bins = Math.max(1, shown.bins);
  const zeros = new Float32Array(bins);
  return {
    trace: {
      row: { min: zeros, max: zeros },
      start: shown.start,
      end: shown.end,
    },
    spikeValues: new Float32Array(shown.spikes.length),
    artifactValues: new Float32Array(shown.artifacts.length),
    range: { min: 0, max: 0 },
  };
}

/**
 * The current MU's pulse train over the view, with its discharges, artifacts,
 * the selections and the bookmark.
 *
 * @param {Els} els
 * @param {State} state
 */
export function drawEditPulse(els, state) {
  const muIdx = state.edit.currentMu ?? 0;
  const total = state.edit.totalSamples || 0;
  const view = state.edit.view || { start: 0, end: total };
  const overlays = [];
  if (state.edit.selectionPulse) overlays.push(state.edit.selectionPulse);
  if (state.edit.draftSelectionPulse)
    overlays.push(state.edit.draftSelectionPulse);
  // A window of another MU is not drawn; one of this MU still loading is.
  const shown =
    state.edit.pulseView?.mu === muIdx ? state.edit.pulseView : null;
  const canvasEl = els.editPulseCanvas;
  const hasData = !!(state.edit.distimes?.length && total);
  if (shown) {
    const { trace, spikeValues, artifactValues, range } = displayedPulse(
      shown,
      !!state.edit.flagged?.[muIdx],
    );
    drawTrace(canvasEl, trace, view, {
      color: COLORS.pulse,
      range,
      selections: overlays,
      showAxes: true,
      hideYAxis: false,
      fsamp: state.edit.fsamp,
      markers: [
        {
          positions: shown.spikes,
          values: spikeValues,
          color: COLORS.muPurple,
        },
        {
          positions: shown.artifacts,
          values: artifactValues,
          color: COLORS.artifactMarker,
          radius: 4,
          outlined: true,
        },
      ],
    });
  } else {
    drawTrace(canvasEl, null, view, { noDataText: hasData ? "" : "No data" });
  }
  if (canvasEl) {
    renderBookmark(canvasEl, state, muIdx, state.edit.view);
  }
}

/**
 * The current MU's discharge rates over the view.
 *
 * @param {Els} els
 * @param {State} state
 */
export function drawEditRates(els, state) {
  const canvas = els.editDrCanvas;
  const muIdx = state.edit.currentMu ?? 0;
  const spikes = state.edit.distimes?.[muIdx] || [];
  const total = state.edit.totalSamples || 0;
  if (state.edit.flagged?.[muIdx] || !total || !spikes.length) {
    drawTrace(canvas, null, { start: 0, end: 0 });
    return;
  }
  const view = state.edit.view || { start: 0, end: total };
  const dr = cachedDischargeRates(spikes, state.edit.fsamp, total);
  drawTrace(canvas, { row: null, start: view.start, end: view.end }, view, {
    range: { min: 0, max: fastestRateInView(dr, view) },
    showAxes: true,
    hideYAxis: false,
    fsamp: state.edit.fsamp,
    markers: [
      { positions: dr.positions, values: dr.rates, color: COLORS.muPurple },
    ],
  });
}

/**
 * What a box drawn on the pulse plot does in each armed mode, and what a click
 * there does instead: show `hint`, or (with none) edit the samples around it.
 *
 * @type {Partial<Record<import("../state/state.js").EditMode, { edit: "addSpikesInSelection" | "addArtifactInSelection" | "deleteSpikesInSelection", hint?: string }>>}
 */
const PULSE_BOX_EDITS = {
  add: { edit: "addSpikesInSelection", hint: "Drag a box to add spikes" },
  add_artifact: {
    edit: "addArtifactInSelection",
    hint: "Drag a box to mark an artifact",
  },
  delete_spikes: { edit: "deleteSpikesInSelection" },
};

/** @param {App} app */
export function bindEditCanvas(app) {
  const { els, state, renderEditExplorer, setEditStatus, setEditMode } = app;

  const canvas = els.editPulseCanvas;
  if (!canvas) return;

  const hasMu = () =>
    !!state.edit.totalSamples &&
    state.edit.distimes?.[state.edit.currentMu ?? 0] !== undefined;

  const pxToSample = (/** @type {number} */ px) =>
    pxToViewSample(
      canvas,
      px,
      state.edit.view || { start: 0, end: state.edit.totalSamples || 0 },
    );

  const drag = createDragState(canvas, pxToSample);

  canvas.addEventListener("pointerdown", (e) => {
    if (!hasMu()) return;
    drag.begin(e);
    setEditPulseDraftSelection(state, null);
  });

  canvas.addEventListener("pointermove", (e) => {
    if (!drag.dragging) return;
    setEditPulseDraftSelection(state, drag.update(e));
    app.scheduleEditRender();
  });

  canvas.addEventListener("pointercancel", () => {
    if (!drag.dragging) return;
    drag.stop();
    setEditPulseDraftSelection(state, null);
    app.scheduleEditRender();
  });

  canvas.addEventListener("pointerup", () => {
    if (!drag.dragging) return;
    drag.stop();
    const sel = drag.selection();
    // The box being drawn goes, however the gesture ends.
    setEditPulseDraftSelection(state, null);
    const armed = state.edit.mode ? PULSE_BOX_EDITS[state.edit.mode] : null;
    if (drag.delta < 6) {
      if (armed?.hint) setEditStatus(armed.hint, "muted");
      else if (armed) {
        app[armed.edit]({
          ...sel,
          start: Math.max(0, sel.start - 2),
          end: sel.start + 2,
        });
      }
    } else if (armed) {
      app[armed.edit](sel);
      setEditMode(null);
    } else {
      setEditPulseSelection(state, sel);
    }
    app.scheduleEditRender();
  });

  canvas.addEventListener("dblclick", () => {
    if (!hasMu()) return;
    setEditView(state, { start: 0, end: state.edit.totalSamples });
    clearEditPulseSelections(state);
    setShowBookmark(state, true);
    renderEditExplorer();
  });
}

/**
 * Fill a 2 px tick at each sample in `positions`, once per pixel column.
 *
 * @param {CanvasRenderingContext2D} ctx
 * @param {ArrayLike<number>} positions
 * @param {number} total
 * @param {number} bw
 */
function fillTicks(ctx, positions, total, bw) {
  const drawn = new Set();
  for (let i = 0; i < positions.length; i++) {
    const x = TIMELINE_PAD_L + Math.round((positions[i] / total) * bw);
    if (drawn.has(x)) continue;
    drawn.add(x);
    ctx.fillRect(x, TIMELINE_BAR_TOP, 2, TIMELINE_BAR_H);
  }
}

/**
 * The whole recording under the view: the current MU's discharges, its last
 * edit, and where the view is.
 *
 * @param {Els} els
 * @param {State} state
 */
export function drawEditTimeline(els, state) {
  const prepared = prepareCanvas(els?.editTimelineCanvas, { height: 20 });
  if (!prepared) return;
  const { ctx, width } = prepared;

  const muIdx = state.edit.currentMu ?? 0;
  const total = state.edit.distimes?.[muIdx] ? state.edit.totalSamples || 0 : 0;
  if (!total) return;

  const bw = Math.max(1, width - TIMELINE_PAD_L - TIMELINE_PAD_R);

  ctx.fillStyle = COLORS.timelineTrack;
  ctx.fillRect(TIMELINE_PAD_L, TIMELINE_BAR_TOP, bw, TIMELINE_BAR_H);

  // Last edit action for this MU: green = added, red = removed
  const muUid = state.edit.muUids?.[muIdx];
  if (muUid) {
    const lastEntry = state.edit.editHistory.findLast(
      (e) => e.mu_uid === muUid,
    );
    if (lastEntry) {
      const added = [
        ...(lastEntry.spikes_added || []),
        ...(lastEntry.artifacts_added || []),
      ];
      const removed = [
        ...(lastEntry.spikes_removed || []),
        ...(lastEntry.artifacts_removed || []),
      ];
      ctx.fillStyle = COLORS.timelineAdded;
      fillTicks(ctx, added, total, bw);
      ctx.fillStyle = COLORS.timelineRemoved;
      fillTicks(ctx, removed, total, bw);
    }
  }

  // Current spike positions (faint purple, drawn on top of history)
  ctx.fillStyle = COLORS.timelineSpikes;
  fillTicks(ctx, state.edit.distimes?.[muIdx] || [], total, bw);

  // View window
  const view = state.edit.view || { start: 0, end: total };
  const x1 = TIMELINE_PAD_L + (Math.max(0, view.start) / total) * bw;
  const x2 = TIMELINE_PAD_L + (Math.min(total, view.end) / total) * bw;
  const ww = Math.max(4, x2 - x1);
  ctx.fillStyle = COLORS.timelineViewFill;
  ctx.fillRect(x1, TIMELINE_BAR_TOP - 2, ww, TIMELINE_BAR_H + 4);
  ctx.strokeStyle = COLORS.timelineViewStroke;
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
  const { els, state, renderEditExplorer } = app;
  const canvas = els?.editTimelineCanvas;
  if (!canvas) return;

  let dragging = false;
  let startClientX = 0;
  let dragViewStart = 0;
  let didMove = false;

  const getTotal = () => state.edit.totalSamples || 0;

  const fracFromClientX = (/** @type {number} */ clientX) => {
    const rect = canvas.getBoundingClientRect();
    const bw = Math.max(1, rect.width - TIMELINE_PAD_L - TIMELINE_PAD_R);
    return Math.max(
      0,
      Math.min(1, (clientX - rect.left - TIMELINE_PAD_L) / bw),
    );
  };

  canvas.addEventListener("pointerdown", (e) => {
    canvas.setPointerCapture(e.pointerId);
    dragging = true;
    didMove = false;
    startClientX = e.clientX;
    const total = getTotal();
    dragViewStart = (state.edit.view || { start: 0, end: total }).start;
  });

  canvas.addEventListener("pointermove", (e) => {
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
    setEditView(state, clampView(dragViewStart + delta, span, total));
    app.scheduleEditRender();
  });

  canvas.addEventListener("pointercancel", () => {
    dragging = false;
  });

  canvas.addEventListener("pointerup", (e) => {
    if (!dragging) return;
    dragging = false;
    if (didMove) return;
    const total = getTotal();
    if (!total) return;
    const view = state.edit.view || { start: 0, end: total };
    const span = view.end - view.start;
    const frac = fracFromClientX(e.clientX);
    setEditView(
      state,
      clampView(Math.round(frac * total - span / 2), span, total),
    );
    renderEditExplorer();
  });
}
