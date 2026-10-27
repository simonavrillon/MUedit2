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
  setEditPulseSelectionDraft,
  setEditPulseSelection,
  setEditView,
  setEditShowBookmark,
} from "../state/actions.js";
import {
  cachedDischargeRates,
  clampView,
  fastestRateInView,
} from "../editing/operations.js";
import { getEditView } from "../state/selectors.js";

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
  const { padding, plotWidth } = getCanvasPlotMetrics(canvas);
  const clamped = Math.max(
    padding.left,
    Math.min(padding.left + plotWidth, px),
  );
  return Math.round(viewScale(view, padding.left, plotWidth).toSample(clamped));
}

/**
 * @param {HTMLCanvasElement} canvas
 * @param {State} state
 * @param {number} mu
 * @param {Span | null} view
 */
function renderBookmark(canvas, state, mu, view) {
  const bookmark = state.edit.bookmark;
  if (!view || !bookmark || bookmark.mu !== mu) return;
  if (!state.edit.showBookmark) return;

  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  const metrics = getCanvasPlotMetrics(canvas);

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
  const metrics = getCanvasPlotMetrics(canvas);
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
 * @param {HTMLSelectElement} sel
 * @param {number | string} value
 * @param {string} label
 */
function addOption(sel, value, label) {
  const opt = document.createElement("option");
  opt.value = String(value);
  opt.textContent = label;
  sel.appendChild(opt);
}

/**
 * The grid and MU dropdowns; the MU one is disabled when the grid has none.
 *
 * @param {Els} els
 * @param {EditDropdownModel} model
 */
export function renderEditDropdownsView(els, model) {
  const gridSel = els.editMuGridSelect;
  const muSel = els.editMuSelect;
  if (!gridSel || !muSel) return;

  gridSel.innerHTML = "";
  model.gridNames.forEach((name, grid) =>
    addOption(gridSel, grid, `Grid ${grid + 1}${name ? ` • ${name}` : ""}`),
  );
  gridSel.value = String(model.currentGrid);

  muSel.innerHTML = "";
  muSel.disabled = !model.muOptions.length;
  if (!model.muOptions.length) {
    addOption(muSel, "", "No motor units");
    return;
  }
  model.muOptions.forEach((mu) => addOption(muSel, mu, `MU ${mu + 1}`));
  muSel.value = String(model.currentMu);
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
  const mu = state.edit.currentMu;
  const totalSamples = state.edit.totalSamples;
  const view = getEditView(state);
  const selections = [];
  if (state.edit.pulseSelection) selections.push(state.edit.pulseSelection);
  if (state.edit.pulseSelectionDraft)
    selections.push(state.edit.pulseSelectionDraft);
  // A window of another MU is not drawn; one of this MU still loading is.
  const shown = state.edit.pulseView?.mu === mu ? state.edit.pulseView : null;
  const canvasEl = els.editPulseCanvas;
  const hasData = !!(state.edit.spikeTimes.length && totalSamples);
  if (shown) {
    const { trace, spikeValues, artifactValues, range } = displayedPulse(
      shown,
      !!state.edit.flagged[mu],
    );
    drawTrace(canvasEl, trace, view, {
      color: COLORS.pulse,
      range,
      selections,
      fsamp: state.edit.fsamp,
      markers: [
        {
          positions: shown.spikes,
          values: spikeValues,
          color: COLORS.mu4,
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
    renderBookmark(canvasEl, state, mu, state.edit.view);
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
  const mu = state.edit.currentMu;
  const spikes = state.edit.spikeTimes[mu] || [];
  const totalSamples = state.edit.totalSamples;
  if (state.edit.flagged[mu] || !totalSamples || !spikes.length) {
    drawTrace(canvas, null, { start: 0, end: 0 });
    return;
  }
  const view = getEditView(state);
  const rates = cachedDischargeRates(spikes, state.edit.fsamp, totalSamples);
  drawTrace(canvas, { row: null, start: view.start, end: view.end }, view, {
    range: { min: 0, max: fastestRateInView(rates, view) },
    fsamp: state.edit.fsamp,
    markers: [
      { positions: rates.positions, values: rates.rates, color: COLORS.mu4 },
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
  add: { edit: "addSpikesInSelection", hint: "Drag a box to add discharges" },
  add_artifact: {
    edit: "addArtifactInSelection",
    hint: "Drag a box to mark an artifact",
  },
  delete_spikes: { edit: "deleteSpikesInSelection" },
};

/** @param {App} app */
export function bindEditCanvas(app) {
  const { els, state, renderEditExplorer, setStatus, setEditMode } = app;

  const canvas = els.editPulseCanvas;
  if (!canvas) return;

  const hasMu = () =>
    !!state.edit.totalSamples &&
    state.edit.spikeTimes[state.edit.currentMu] !== undefined;

  const pxToSample = (/** @type {number} */ px) =>
    pxToViewSample(canvas, px, getEditView(state));

  const drag = createDragState(canvas, pxToSample);

  canvas.addEventListener("pointerdown", (e) => {
    if (!hasMu()) return;
    drag.begin(e);
    setEditPulseSelectionDraft(state, null);
  });

  canvas.addEventListener("pointermove", (e) => {
    if (!drag.dragging) return;
    setEditPulseSelectionDraft(state, drag.update(e));
    app.scheduleEditRender();
  });

  canvas.addEventListener("pointercancel", () => {
    if (!drag.dragging) return;
    drag.stop();
    setEditPulseSelectionDraft(state, null);
    app.scheduleEditRender();
  });

  canvas.addEventListener("pointerup", () => {
    if (!drag.dragging) return;
    drag.stop();
    const sel = drag.selection();
    // The box being drawn goes, however the gesture ends.
    setEditPulseSelectionDraft(state, null);
    const armed = state.edit.mode ? PULSE_BOX_EDITS[state.edit.mode] : null;
    if (drag.delta < 6) {
      if (armed?.hint) setStatus(armed.hint, "muted");
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
    setEditShowBookmark(state, true);
    renderEditExplorer();
  });
}

/**
 * Fill a 2 px tick at each sample in `positions`, once per pixel column.
 *
 * @param {CanvasRenderingContext2D} ctx
 * @param {ArrayLike<number>} positions
 * @param {number} totalSamples
 * @param {number} bw
 */
function fillTicks(ctx, positions, totalSamples, bw) {
  const drawn = new Set();
  for (let i = 0; i < positions.length; i++) {
    const x = TIMELINE_PAD_L + Math.round((positions[i] / totalSamples) * bw);
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
  const prepared = prepareCanvas(els.editTimelineCanvas, { height: 20 });
  if (!prepared) return;
  const { ctx, width } = prepared;

  const mu = state.edit.currentMu;
  const totalSamples = state.edit.spikeTimes[mu] ? state.edit.totalSamples : 0;
  if (!totalSamples) return;

  const bw = Math.max(1, width - TIMELINE_PAD_L - TIMELINE_PAD_R);

  ctx.fillStyle = COLORS.timelineTrack;
  ctx.fillRect(TIMELINE_PAD_L, TIMELINE_BAR_TOP, bw, TIMELINE_BAR_H);

  // The MU's last edit: what it added, and what it removed.
  const muUid = state.edit.muUids[mu];
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
      fillTicks(ctx, added, totalSamples, bw);
      ctx.fillStyle = COLORS.timelineRemoved;
      fillTicks(ctx, removed, totalSamples, bw);
    }
  }

  // The current discharges, over the last edit.
  ctx.fillStyle = COLORS.timelineSpikes;
  fillTicks(ctx, state.edit.spikeTimes[mu] || [], totalSamples, bw);

  const view = getEditView(state);
  const x1 = TIMELINE_PAD_L + (Math.max(0, view.start) / totalSamples) * bw;
  const x2 =
    TIMELINE_PAD_L + (Math.min(totalSamples, view.end) / totalSamples) * bw;
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
  const canvas = els.editTimelineCanvas;
  if (!canvas) return;

  let dragging = false;
  let startClientX = 0;
  let dragViewStart = 0;
  let didMove = false;

  const barWidth = () =>
    Math.max(
      1,
      canvas.getBoundingClientRect().width - TIMELINE_PAD_L - TIMELINE_PAD_R,
    );

  canvas.addEventListener("pointerdown", (e) => {
    canvas.setPointerCapture(e.pointerId);
    dragging = true;
    didMove = false;
    startClientX = e.clientX;
    dragViewStart = getEditView(state).start;
  });

  canvas.addEventListener("pointermove", (e) => {
    if (!dragging) return;
    if (Math.abs(e.clientX - startClientX) > 3) didMove = true;
    if (!didMove) return;
    const totalSamples = state.edit.totalSamples;
    if (!totalSamples) return;
    const delta = Math.round(
      ((e.clientX - startClientX) / barWidth()) * totalSamples,
    );
    const view = getEditView(state);
    const span = view.end - view.start;
    setEditView(state, clampView(dragViewStart + delta, span, totalSamples));
    app.scheduleEditRender();
  });

  canvas.addEventListener("pointercancel", () => {
    dragging = false;
  });

  canvas.addEventListener("pointerup", (e) => {
    if (!dragging) return;
    dragging = false;
    if (didMove) return;
    const totalSamples = state.edit.totalSamples;
    if (!totalSamples) return;
    const view = getEditView(state);
    const span = view.end - view.start;
    const x = e.clientX - canvas.getBoundingClientRect().left - TIMELINE_PAD_L;
    const frac = Math.max(0, Math.min(1, x / barWidth()));
    setEditView(
      state,
      clampView(Math.round(frac * totalSamples - span / 2), span, totalSamples),
    );
    renderEditExplorer();
  });
}
